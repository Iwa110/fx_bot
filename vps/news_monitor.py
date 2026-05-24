"""
news_monitor.py - 経済指標戦略(B+C複合) リアルタイム監視 v1
magic: 20260040

戦略概要:
  B条件: サプライズZスコア >= surprise_z_th
          surprise_raw = actual - forecast (live では previous を forecast 代用)
          surprise_z   = surprise_raw / std(過去 surprise_window 回)
          forecast が取れた場合は actual - forecast を優先
  C条件: 発表後 delay_min 分後の値動きが move_th_pips 以上
  エントリー: B AND C (forecast あり) / C のみ (forecast なし)

対象指標・ペア (EVENT_PAIR_MAP):
  NFP      -> USDJPY LONG  (USD+ サプライズ)
  US_CPI   -> USDJPY LONG
  US_CPI   -> EURUSD SHORT
  GB_CPI   -> GBPUSD LONG
  GB_CPI   -> GBPJPY LONG

データソース: ForexFactory JSON (今週分)
  https://nfs.faireconomy.media/ff_calendar_thisweek.json

ログ:
  news_monitor_log_{broker}.txt
  heartbeat 30サイクル毎
  エントリー: NEWS entry: USDJPY LONG lot=0.1 z=1.23 move=6.2pips entry=155.200 sl=154.950 tp=155.950
  決済:       NEWS exit:  USDJPY LONG pnl=+8.5pips reason=TP

パラメータ更新手順 (VPS BT完了後):
  1. VPS で: python optimizer/news_event_bt.py
  2. optimizer/news_bt_result.csv の上位行(PF>1.3, n>=15)を確認
  3. 本ファイル PARAMS の「BT最適化対象」セクションを上位値で更新
  4. git commit/push -> VPS: git pull -> news_monitor.bat で再起動

v1   2026-05-24: 初版実装
  - ForexFactory JSON API ライブ取得
  - B+C複合条件 (BTと同一ロジック)
  - surprise_z キャッシュ (JSON永続化)
  - MT5 TP/SL + hold_max_min 強制決済
  - 取引履歴 data/news_history_{broker}.csv 追記
v1.1 2026-05-24: 仕様差異修正
  - Z計算不能時(サンプル不足): スキップ → C条件のみ発動に変更
  - --dry-run オプション追加 (MT5発注なしでロジック確認)
  - history CSV カラム変更: 仕様書準拠形式
    (datetime, pair, direction, entry, exit, pnl_pips, lot, reason, event, surprise_z)
"""

import sys, os, ssl, time, json, argparse, urllib.request
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# MAGIC / STRATEGY
# ══════════════════════════════════════════
MAGIC        = 20260040
STRATEGY_TAG = 'NEWS'
BROKER_KEY   = 'axiory'
DRY_RUN      = False   # True: ログのみ、MT5発注なし (--dry-run で有効)

_SYMBOL_MAP: dict[str, str] = {}

def _rsym(base: str) -> str:
    return _SYMBOL_MAP.get(base, base)

# ══════════════════════════════════════════
# PARAMS
# パラメータ更新手順: 上部 docstring 参照
# ══════════════════════════════════════════
PARAMS = {
    # --- BT最適化対象 (optimizer/news_bt_result.csv 上位行から転記) ---
    # 最終更新: 2026-05-24 (Mac/1h暫定BT, n=31)
    'delay_min':       2,     # 発表後エントリー遅延(分)
    'move_th_pips':    5.0,   # C条件: 値動き閾値(pips)
    'surprise_z_th':   0.5,   # B条件: Zスコア閾値
    'sl_pips':         5.0,   # SL(pips)
    'rr':              3.0,   # TP = move_th × rr
    'hold_max_min':    30,    # 最大保有(分)
    'surprise_window': 12,    # Zスコア計算ウィンドウ(同一指標件数)
    # --- 固定パラメータ ---
    'lot':             0.1,   # 最大ロット上限 (rm.calc_lot() でリスク計算後にキャップ)
    'max_pos':         1,     # 同時エントリー上限
    'slippage_pips': {
        'USDJPY': 3.0,
        'EURUSD': 2.0,
        'GBPUSD': 2.5,
        'GBPJPY': 4.0,
    },
    # VPS M1 BT完了後: 上記 BT最適化対象 を news_bt_result.csv 上位値に更新
}

# ══════════════════════════════════════════
# 戦略設定: 指標 -> ペア・方向
# (optimizer/news_event_bt.py の EVENT_PAIR_MAP と同一)
# ══════════════════════════════════════════
EVENT_PAIR_MAP = [
    {'event_key': 'Non-Farm',  'currency': 'USD', 'pair': 'USDJPY', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'USD', 'pair': 'USDJPY', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'USD', 'pair': 'EURUSD', 'sign': -1},
    {'event_key': 'CPI',       'currency': 'GBP', 'pair': 'GBPUSD', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'GBP', 'pair': 'GBPJPY', 'sign': +1},
]

PIP_UNIT = {
    'USDJPY': 0.01,
    'EURUSD': 0.0001,
    'GBPUSD': 0.0001,
    'GBPJPY': 0.01,
}

# ══════════════════════════════════════════
# ファイルパス
# ══════════════════════════════════════════
_VPS_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_VPS_DIR)
_DATA_DIR = os.path.join(_ROOT_DIR, 'data')

LOG_FILE           = os.path.join(_VPS_DIR, 'news_monitor_log.txt')
SURPRISE_CACHE_FILE = os.path.join(_VPS_DIR, 'news_surprise_cache.json')
ENV_FILE           = os.path.join(_VPS_DIR, '.env')

# ForexFactory JSON API
FF_JSON_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'

LOOP_INTERVAL = 60   # 秒

# ══════════════════════════════════════════
# 状態管理
# ══════════════════════════════════════════
# 処理済みイベントID (セッション内重複防止)
_processed_ids: set = set()

# 保留中エントリー: event_id -> {entry_check_time, pair, direction, ...}
_pending: dict = {}

# 追跡中ポジション: ticket -> open_time (UTC)
_tracked_positions: dict = {}


# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════

def load_env() -> dict:
    env = {}
    try:
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def log_print(msg: str, debug: bool = False) -> None:
    if debug:
        return
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def send_discord(msg: str, webhook: str) -> None:
    if not webhook:
        return
    try:
        import json as _json
        data = _json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'},
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log_print('Discord error: ' + str(e))


# ══════════════════════════════════════════
# 経済指標値のパース (BT と同一)
# ══════════════════════════════════════════

def parse_economic_value(s) -> float | None:
    """
    "280K" -> 280000 / "0.3%" -> 0.3 / "-0.1%" -> -0.1
    NaN / None / "" -> None
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s) if not (isinstance(s, float) and s != s) else None
    s = str(s).strip()
    if s in ('', 'nan', 'NaN', 'N/A', '-'):
        return None
    sign = -1.0 if s.startswith('-') else 1.0
    s_clean = s.lstrip('+-')
    is_pct = s_clean.endswith('%')
    if is_pct:
        s_clean = s_clean[:-1]
    multiplier = 1.0
    suffix_map = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    if s_clean and s_clean[-1].upper() in suffix_map:
        multiplier = suffix_map[s_clean[-1].upper()]
        s_clean = s_clean[:-1]
    try:
        return float(s_clean) * multiplier * sign
    except ValueError:
        return None


# ══════════════════════════════════════════
# サプライズ Z スコア (キャッシュ永続化)
# ══════════════════════════════════════════

def load_surprise_cache() -> dict:
    """surprise_cache.json をロードして返す。存在しない場合は空 dict。"""
    try:
        with open(SURPRISE_CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_surprise_cache(cache: dict) -> None:
    try:
        with open(SURPRISE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_print('surprise_cache save error: ' + str(e))


def compute_z_live(group_key: str, surprise_raw: float, cache: dict, window: int) -> float | None:
    """
    group_key = "USD_CPI" / "USD_Non-Farm" / "GBP_CPI" のような文字列。
    cache から過去 window 件の surprise_raw を取得して Z スコアを計算。
    データ不足(< 3件) または std が 0 に近い場合は None を返す。
    """
    history = cache.get(group_key, [])
    past = [h['surprise_raw'] for h in history[-window:]]
    if len(past) < 3:
        return None
    std_val = float(np.std(past, ddof=1))
    if std_val < 1e-9:
        return None
    return float(surprise_raw / std_val)


def update_surprise_cache(group_key: str, surprise_raw: float, event_date: str, cache: dict) -> None:
    """
    キャッシュに新しいサプライズ値を追加する。
    同一 event_date のエントリーが既にある場合は上書き。
    最大 200 件保持。
    """
    history = cache.get(group_key, [])
    # 同日エントリー重複防止
    history = [h for h in history if h.get('date') != event_date]
    history.append({'date': event_date, 'surprise_raw': surprise_raw})
    history = history[-200:]  # 最大 200 件
    cache[group_key] = history


def get_event_key_from_title(title: str) -> str:
    """ForexFactory のイベントタイトルから EVENT_PAIR_MAP の event_key を推定。"""
    title_l = title.lower()
    if 'non-farm' in title_l or 'nonfarm' in title_l:
        return 'Non-Farm'
    if 'cpi' in title_l or 'consumer price' in title_l:
        return 'CPI'
    return ''


# ══════════════════════════════════════════
# ForexFactory JSON 取得
# ══════════════════════════════════════════

def fetch_ff_calendar() -> list:
    """
    ForexFactory JSON API から今週の経済指標を取得して返す。
    Returns: list of dicts with keys: title, country, date(UTC ISO8601), impact, forecast, previous, actual
    失敗時は []。
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        req = urllib.request.Request(
            FF_JSON_URL,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; fx-bot/1.0)'},
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read().decode('utf-8')
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return data
    except Exception as e:
        log_print('FF calendar fetch error: ' + str(e))
        return []


def parse_ff_event_time(date_str: str) -> datetime | None:
    """
    ForexFactory JSON の date フィールド (ISO 8601) を UTC naive datetime に変換。
    例: "2026-05-02T12:30:00+00:00" -> datetime(2026, 5, 2, 12, 30)
    """
    if not date_str:
        return None
    try:
        # Python 3.7+ fromisoformat が "+00:00" に対応
        dt = datetime.fromisoformat(date_str)
        # timezone aware -> UTC naive
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        try:
            # フォールバック: "2026-05-02T12:30:00" 形式
            return datetime.strptime(date_str[:19], '%Y-%m-%dT%H:%M:%S')
        except Exception:
            return None


# ══════════════════════════════════════════
# 価格取得
# ══════════════════════════════════════════

def get_current_price(symbol: str, direction: int) -> float | None:
    """
    現在の価格を取得。
    LONG: ask / SHORT: bid を返す。
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return tick.ask if direction == +1 else tick.bid


def get_pre_event_price(symbol: str, event_utc: datetime) -> float | None:
    """
    イベント発表前の参照価格を取得。
    MT5 M1 足でイベント時刻の直前バーの close を返す。
    取得失敗時は現在 mid を返す。
    """
    try:
        # イベント時刻から 5 分前〜イベント時刻 のM1 バーを取得
        tf_from = event_utc - timedelta(minutes=5)
        tf_to   = event_utc
        rates = mt5.copy_rates_range(
            symbol, mt5.TIMEFRAME_M1,
            tf_from, tf_to
        )
        if rates is not None and len(rates) > 0:
            return float(rates[-1]['close'])
    except Exception:
        pass
    # フォールバック: 現在 mid
    tick = mt5.symbol_info_tick(symbol)
    if tick and tick.bid > 0 and tick.ask > 0:
        return (tick.bid + tick.ask) / 2.0
    return None


# ══════════════════════════════════════════
# ポジション管理
# ══════════════════════════════════════════

def count_news_positions() -> int:
    pos = mt5.positions_get(group='*')
    if pos is None:
        return 0
    return sum(1 for p in pos if p.magic == MAGIC)


def get_news_positions() -> list:
    pos = mt5.positions_get(group='*')
    if pos is None:
        return []
    return [p for p in pos if p.magic == MAGIC]


def _close_position(pos, comment: str) -> bool:
    """成行でポジションをクローズする。DRY_RUN 時は発注せず True を返す。"""
    if DRY_RUN:
        return True
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return False
    if pos.type == mt5.ORDER_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price      = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price      = tick.ask

    req = {
        'action':    mt5.TRADE_ACTION_DEAL,
        'symbol':    pos.symbol,
        'volume':    pos.volume,
        'type':      order_type,
        'position':  pos.ticket,
        'price':     price,
        'deviation': 20,
        'magic':     MAGIC,
        'comment':   comment,
    }
    result = mt5.order_send(req)
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


def manage_positions(webhook: str) -> None:
    """
    hold_max_min 超過ポジションを強制決済する。
    決済済みポジションを検出し history CSV に保存。
    """
    global _tracked_positions

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    current_positions = get_news_positions()
    current_tickets   = {p.ticket for p in current_positions}

    # ── hold_max_min 強制決済 ──────────────────────────────────────
    for pos in current_positions:
        rec = _tracked_positions.get(pos.ticket)
        if rec is None:
            # 初認識: 記録 (起動時から開いているポジション)
            rec = {
                'open_time':   datetime.fromtimestamp(pos.time, tz=timezone.utc).replace(tzinfo=None),
                'event_label': '',
                'surprise_z':  None,
            }
            _tracked_positions[pos.ticket] = rec
        open_ts = rec['open_time']

        hold_min = (now_utc - open_ts).total_seconds() / 60.0
        if hold_min >= PARAMS['hold_max_min']:
            side = 'LONG' if pos.type == mt5.ORDER_TYPE_BUY else 'SHORT'
            log_print(
                STRATEGY_TAG + ' force-close: ' + pos.symbol + ' ' + side +
                ' hold=' + str(round(hold_min, 1)) + 'min' +
                ' ticket=' + str(pos.ticket)
            )
            if _close_position(pos, STRATEGY_TAG + '_HOLD_MAX'):
                msg = STRATEGY_TAG + ' force-close OK: ' + pos.symbol + ' ' + side
                log_print(msg)
                send_discord(msg, webhook)

    # ── 決済済みポジションの検出 → 履歴保存 ──────────────────────────
    closed_tickets = set(_tracked_positions.keys()) - current_tickets
    for ticket in closed_tickets:
        rec = _tracked_positions[ticket]
        _save_closed_trade(
            ticket,
            event_label = rec.get('event_label', ''),
            surprise_z  = rec.get('surprise_z'),
        )
        del _tracked_positions[ticket]


def _save_closed_trade(ticket: int, event_label: str = '', surprise_z: float | None = None) -> None:
    """
    MT5 の取引履歴から ticket を検索し、
    data/news_history_{broker}.csv に追記する。
    カラム: datetime, pair, direction, entry, exit, pnl_pips, lot, reason, event, surprise_z
    """
    if DRY_RUN:
        return
    try:
        import csv
        # 直近 7 日分の deal 履歴から検索
        now   = datetime.now(timezone.utc)
        deals = mt5.history_deals_get(
            now - timedelta(days=7), now,
            group='*'
        )
        if deals is None:
            return

        # ticket に対応する entry/exit deal を収集
        entry_deal = None
        exit_deal  = None
        for d in deals:
            if d.position_id == ticket:
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entry_deal = d
                elif d.entry == mt5.DEAL_ENTRY_OUT:
                    exit_deal = d

        if entry_deal is None or exit_deal is None:
            return

        close_time = datetime.fromtimestamp(exit_deal.time, tz=timezone.utc).replace(tzinfo=None)
        direction  = 'LONG' if entry_deal.type == mt5.DEAL_TYPE_BUY else 'SHORT'

        # pnl_pips 計算
        pair_base = exit_deal.symbol[:6]
        pip       = PIP_UNIT.get(pair_base, 0.0001)
        sign      = 1 if direction == 'LONG' else -1
        pnl_pips  = round((exit_deal.price - entry_deal.price) * sign / pip, 1)

        # exit reason: コメント内の TP/SL/HOLD_MAX を抽出
        cmt = str(exit_deal.comment or '')
        if 'tp' in cmt.lower():
            reason = 'TP'
        elif 'sl' in cmt.lower():
            reason = 'SL'
        elif 'hold' in cmt.lower():
            reason = 'hold_max'
        else:
            reason = cmt or 'unknown'

        # ログ出力 (仕様書準拠形式)
        log_print(
            STRATEGY_TAG + ' exit: ' + pair_base +
            ' ' + direction +
            ' pnl=' + ('+' if pnl_pips >= 0 else '') + str(pnl_pips) + 'pips' +
            ' reason=' + reason
        )

        # history CSV 追記 (仕様書カラム準拠)
        history_path = os.path.join(
            _DATA_DIR, 'news_history_' + BROKER_KEY + '.csv'
        )
        cols = ['datetime', 'pair', 'direction', 'entry', 'exit',
                'pnl_pips', 'lot', 'reason', 'event', 'surprise_z']
        z_str = str(round(surprise_z, 3)) if surprise_z is not None else ''
        row = {
            'datetime':   close_time.strftime('%Y-%m-%d %H:%M:%S'),
            'pair':       pair_base,
            'direction':  direction,
            'entry':      entry_deal.price,
            'exit':       exit_deal.price,
            'pnl_pips':   pnl_pips,
            'lot':        entry_deal.volume,
            'reason':     reason,
            'event':      event_label,
            'surprise_z': z_str,
        }
        write_header = not os.path.exists(history_path)
        with open(history_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    except Exception as e:
        log_print('_save_closed_trade error: ' + str(e))


# ══════════════════════════════════════════
# 注文発注
# ══════════════════════════════════════════

def place_order(
    base_sym: str,
    direction: int,
    entry_px: float,
    sl_px: float,
    tp_px: float,
    lot: float,
    event_label: str,
    surprise_z: float | None,
    move_pips: float,
) -> bool:
    """成行エントリーを発注する。"""
    symbol   = _rsym(base_sym)
    info     = mt5.symbol_info(symbol)
    if info is None:
        log_print('place_order: symbol_info failed: ' + symbol)
        return False

    order_type = mt5.ORDER_TYPE_BUY if direction == +1 else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
    price = tick.ask if direction == +1 else tick.bid

    # 桁数に合わせて丸め
    digits = info.digits
    sl_px  = round(sl_px, digits)
    tp_px  = round(tp_px, digits)
    price  = round(price, digits)

    z_str = (str(round(surprise_z, 2)) if surprise_z is not None else 'N/A')
    log_print(
        STRATEGY_TAG + ' entry: ' + base_sym +
        ' ' + ('LONG' if direction == +1 else 'SHORT') +
        ' lot=' + str(lot) +
        ' z=' + z_str +
        ' move=' + str(round(move_pips, 1)) + 'pips' +
        ' entry=' + str(price) +
        ' sl=' + str(sl_px) +
        ' tp=' + str(tp_px)
    )

    if DRY_RUN:
        log_print('DRY_RUN: order NOT sent.')
        return True

    req = {
        'action':    mt5.TRADE_ACTION_DEAL,
        'symbol':    symbol,
        'volume':    lot,
        'type':      order_type,
        'price':     price,
        'sl':        sl_px,
        'tp':        tp_px,
        'deviation': 20,
        'magic':     MAGIC,
        'comment':   STRATEGY_TAG + '_' + event_label,
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else 'None'
        log_print('place_order FAILED: ' + base_sym + ' retcode=' + str(retcode))
        return False

    # 追跡リスト: ticket -> {open_time, event_label, surprise_z}
    _tracked_positions[result.order] = {
        'open_time':   datetime.now(timezone.utc).replace(tzinfo=None),
        'event_label': event_label,
        'surprise_z':  surprise_z,
    }
    return True


# ══════════════════════════════════════════
# イベント処理メイン
# ══════════════════════════════════════════

def process_ff_events(calendar: list, cache: dict, webhook: str) -> None:
    """
    ForexFactory カレンダーから対象イベントを抽出し、
    pending エントリーを登録 / 実行する。
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    for event in calendar:
        title    = str(event.get('title', ''))
        country  = str(event.get('country', '')).upper()
        impact   = str(event.get('impact', '')).lower()
        actual_s = event.get('actual', None)

        # High impact のみ対象
        if impact not in ('high',):
            continue

        # ForexFactory JSON の date フィールドを UTC datetime に変換
        event_utc = parse_ff_event_time(str(event.get('date', '')))
        if event_utc is None:
            continue

        # actual が出ているイベントのみ処理 (空文字はまだ未発表)
        actual_val = parse_economic_value(actual_s)
        if actual_val is None:
            continue

        # マップ対象の event_key を特定
        event_key = get_event_key_from_title(title)
        if not event_key:
            continue

        # EVENT_PAIR_MAP の対象エントリーを検索
        for cfg in EVENT_PAIR_MAP:
            if cfg['event_key'].lower() not in event_key.lower():
                continue
            if cfg['currency'] != country:
                continue

            pair  = cfg['pair']
            sign  = cfg['sign']
            event_date_str = event_utc.strftime('%Y-%m-%d')

            # ユニーク ID
            event_id = event_date_str + '_' + event_key + '_' + country + '_' + pair

            # 処理済みスキップ
            if event_id in _processed_ids:
                continue

            # ──────────────────────────────────────
            # 1) 保留中でなければ登録
            # ──────────────────────────────────────
            if event_id not in _pending:
                # forecast / previous 解析
                forecast_s  = event.get('forecast', None)
                previous_s  = event.get('previous', None)
                forecast_val = parse_economic_value(forecast_s)
                previous_val = parse_economic_value(previous_s)

                # サプライズ計算
                # forecast がある -> actual - forecast
                # なければ actual - previous (naive)
                ref_val = forecast_val if forecast_val is not None else previous_val
                surprise_raw = (actual_val - ref_val) if ref_val is not None else None
                b_skip = (forecast_val is None)

                # Zスコア計算
                surprise_z_val = None
                group_key = country + '_' + event_key
                if surprise_raw is not None:
                    surprise_z_val = compute_z_live(
                        group_key, surprise_raw, cache, PARAMS['surprise_window']
                    )
                    # キャッシュ更新 (今回の値を追加)
                    update_surprise_cache(group_key, surprise_raw, event_date_str, cache)
                    save_surprise_cache(cache)

                # B条件チェック
                # surprise_z_val is None = サンプル不足 → C条件のみ発動 (スキップしない)
                # abs(z) < threshold かつ z 計算済み → B条件不成立 → スキップ
                if not b_skip:
                    if surprise_z_val is None:
                        # サンプル不足でZ計算不能: C条件のみで発動 (b_skipと同等扱い)
                        log_print(
                            'NEWS z-insufficient (C-only mode): ' + event_id +
                            ' history<3, proceeding to C check'
                        )
                        b_skip = True   # C条件のみで判定
                    elif abs(surprise_z_val) < PARAMS['surprise_z_th']:
                        log_print(
                            'NEWS skip (B cond fail): ' + event_id +
                            ' z=' + str(round(surprise_z_val, 2)) +
                            ' th=' + str(PARAMS['surprise_z_th'])
                        )
                        _processed_ids.add(event_id)
                        continue

                # direction: サプライズ方向 × sign
                if surprise_raw is not None:
                    direction = sign if surprise_raw >= 0 else -sign
                else:
                    direction = sign  # C 条件で確認

                # イベント前価格の取得
                pre_px = get_pre_event_price(_rsym(pair), event_utc)
                if pre_px is None:
                    log_print('NEWS skip (pre_event price failed): ' + event_id)
                    _processed_ids.add(event_id)
                    continue

                entry_check_time = event_utc + timedelta(minutes=PARAMS['delay_min'])

                _pending[event_id] = {
                    'event_time':       event_utc,
                    'entry_check_time': entry_check_time,
                    'pair':             pair,
                    'direction':        direction,
                    'surprise_z':       surprise_z_val,
                    'b_skip':           b_skip,
                    'pre_event_px':     pre_px,
                    'event_label':      event_key + '_' + country,
                }
                log_print(
                    'NEWS scheduled: ' + event_id +
                    ' z=' + (str(round(surprise_z_val, 2)) if surprise_z_val else 'N/A') +
                    ' pre_px=' + str(round(pre_px, 5)) +
                    ' entry_check=' + entry_check_time.strftime('%H:%M:%S UTC')
                )

            # ──────────────────────────────────────
            # 2) エントリーチェック (delay_min 経過後)
            # ──────────────────────────────────────
            pend = _pending.get(event_id)
            if pend is None:
                continue

            # エントリーチェック窓: [entry_check_time, entry_check_time + 5min]
            check_start = pend['entry_check_time']
            check_end   = check_start + timedelta(minutes=5)

            if now_utc < check_start:
                continue  # まだ時間前

            if now_utc > check_end:
                # 窓を過ぎた -> 破棄
                log_print('NEWS expired: ' + event_id)
                _processed_ids.add(event_id)
                del _pending[event_id]
                continue

            # max_pos チェック
            if count_news_positions() >= PARAMS['max_pos']:
                log_print('NEWS skip (max_pos): ' + event_id)
                continue

            # C 条件: 値動きチェック
            pair       = pend['pair']
            direction  = pend['direction']
            pre_px     = pend['pre_event_px']
            pip        = PIP_UNIT[pair]
            move_th    = PARAMS['move_th_pips'] * pip

            cur_px = get_current_price(_rsym(pair), direction)
            if cur_px is None:
                continue

            price_move = (cur_px - pre_px) * direction
            move_pips  = price_move / pip

            if price_move < move_th:
                log_print(
                    'NEWS C-cond fail: ' + event_id +
                    ' move=' + str(round(move_pips, 1)) + 'pips' +
                    ' th=' + str(PARAMS['move_th_pips'])
                )
                _processed_ids.add(event_id)
                del _pending[event_id]
                continue

            # SL/TP 計算
            sl_dist = PARAMS['sl_pips'] * pip
            tp_dist = PARAMS['move_th_pips'] * pip * PARAMS['rr']
            slip    = PARAMS['slippage_pips'].get(pair, 2.0) * pip

            if direction == +1:
                entry_px = cur_px + slip
                sl_px    = entry_px - sl_dist
                tp_px    = entry_px + tp_dist
            else:
                entry_px = cur_px - slip
                sl_px    = entry_px + sl_dist
                tp_px    = entry_px - tp_dist

            # ロット計算 (rm.calc_lot で残高ベース、PARAMS['lot'] でキャップ)
            try:
                balance = rm.get_balance()
                lot = rm.calc_lot(balance, sl_dist, _rsym(pair))
                lot = min(lot, PARAMS['lot'])
                lot = max(0.01, round(lot, 2))
            except Exception:
                lot = PARAMS['lot']

            # 発注
            ok = place_order(
                base_sym    = pair,
                direction   = direction,
                entry_px    = entry_px,
                sl_px       = sl_px,
                tp_px       = tp_px,
                lot         = lot,
                event_label = pend['event_label'],
                surprise_z  = pend['surprise_z'],
                move_pips   = move_pips,
            )
            if ok:
                send_discord(
                    STRATEGY_TAG + ' entry: ' + pair +
                    ' ' + ('LONG' if direction == +1 else 'SHORT') +
                    ' lot=' + str(lot) +
                    ' move=' + str(round(move_pips, 1)) + 'pips',
                    webhook,
                )

            _processed_ids.add(event_id)
            if event_id in _pending:
                del _pending[event_id]


# ══════════════════════════════════════════
# メインループ
# ══════════════════════════════════════════

def main_loop(webhook: str) -> None:
    log_print(
        'news_monitor v1 started  broker=' + BROKER_KEY +
        '  interval=' + str(LOOP_INTERVAL) + 's'
    )
    log_print(
        'PARAMS: delay=' + str(PARAMS['delay_min']) + 'min' +
        ' move_th=' + str(PARAMS['move_th_pips']) + 'pips' +
        ' z_th=' + str(PARAMS['surprise_z_th']) +
        ' sl=' + str(PARAMS['sl_pips']) + ' rr=' + str(PARAMS['rr']) +
        ' hold=' + str(PARAMS['hold_max_min']) + 'min'
    )

    # サプライズキャッシュをロード (再起動後も Z スコア計算に使用)
    cache = load_surprise_cache()
    log_print('surprise_cache loaded: ' + str(sum(len(v) for v in cache.values())) + ' entries')

    _cycle = 0

    while True:
        try:
            _cycle += 1

            # ポジション管理 (hold_max 強制決済 + 決済済み検出)
            manage_positions(webhook)

            # ForexFactory カレンダー取得 (毎サイクル。APIは今週分キャッシュを返すため軽量)
            calendar = fetch_ff_calendar()
            if calendar:
                process_ff_events(calendar, cache, webhook)

            # ハートビート (30 サイクル = 30 分毎)
            if _cycle % 30 == 0:
                log_print(
                    'heartbeat  alive  pos=' + str(count_news_positions()) +
                    '/' + str(PARAMS['max_pos']) +
                    '  pending=' + str(len(_pending)) +
                    '  cycle=' + str(_cycle)
                )

        except Exception as e:
            log_print('loop error: ' + str(e))

        time.sleep(LOOP_INTERVAL)


# ══════════════════════════════════════════
# エントリーポイント
# ══════════════════════════════════════════

def main() -> None:
    global BROKER_KEY, LOG_FILE, DRY_RUN

    parser = argparse.ArgumentParser(description='NEWS monitor v1.1 (economic indicator strategy)')
    parser.add_argument(
        '--broker', default=BROKER_KEY,
        choices=['oanda', 'oanda_demo', 'axiory', 'exness'],
        help='broker key'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='MT5発注なし・ログのみ (ロジック確認用)'
    )
    args = parser.parse_args()

    BROKER_KEY = args.broker
    DRY_RUN    = args.dry_run
    LOG_FILE   = os.path.join(_VPS_DIR, 'news_monitor_log_' + BROKER_KEY + '.txt')

    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    if not connect_mt5(BROKER_KEY):
        log_print('MT5 connection failed: ' + BROKER_KEY)
        sys.exit(1)

    log_print('MT5 connected: ' + BROKER_KEY + ('  [DRY_RUN]' if DRY_RUN else ''))

    # シンボルマップ構築
    pairs = list({cfg['pair'] for cfg in EVENT_PAIR_MAP})
    global _SYMBOL_MAP
    _SYMBOL_MAP = build_symbol_map(pairs, BROKER_KEY)
    log_print('symbol_map: ' + str(_SYMBOL_MAP))

    try:
        main_loop(webhook)
    except KeyboardInterrupt:
        log_print('news_monitor stopped (KeyboardInterrupt)')
    except Exception as e:
        log_print('news_monitor fatal error: ' + str(e))
        raise
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
