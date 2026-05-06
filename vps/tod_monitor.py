"""
tod_monitor.py  - 時間帯別平均回帰戦略（1時間毎実行）v2
================================================================
v2変更点:
  Fix1: 時間帯統計のキャッシュ化（23h TTL）
        tod_stats.jsonが23時間以内なら再計算スキップ
  Fix2: リターン計算をclose-to-closeに統一
        変更前: (prev_close - prev_open) / prev_open
        変更後: bars[-3]終値 → bars[-2]終値（build_hour_statsと一致）
  Fix3: 市場クローズフィルター追加
        金曜22:00UTC以降 / 土日 / 月曜06:00UTC以前はスキップ
  Fix4: バックテスト結果（tod_bt_result.json）の自動反映
        entry_sigma / tp_atr_mult / sl_atr_mult を上書き

戦略概要:
  - 各時間帯の「平均リターン±entry_sigma×std」から大きく乖離したら
    平均回帰方向にエントリー
  - 時間帯統計は1時間足（close-to-close）から計算
  - 1時間ごとに実行（cronまたはタスクスケジューラー）

バックテスト結果（2024-09-02 ~ 2026-05-05 / n=843,691）:
  EURUSD: PF=1.232 / 勝率62.8% / sigma=2.5 / TP*1.0 / SL*1.5
  GBPUSD: PF=1.201 / 勝率70.3% / sigma=2.5 / TP*1.0 / SL*2.0

実行方法（タスクスケジューラー）:
  毎時0分に python tod_monitor.py を実行
"""

import sys, os, json, time as _time
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')
import risk_manager as rm

# ══════════════════════════════════════════
# 定数・設定
# ══════════════════════════════════════════
MAX_TOTAL_POS    = 13
DAILY_LOSS_LIMIT = -30000   # 日次損失上限（円）
MAGIC_NUMBER     = 20250002  # BB=20250001と区別

LOG_FILE  = r'C:\Users\Administrator\fx_bot\vps\tod_log.txt'
ENV_FILE  = r'C:\Users\Administrator\fx_bot\vps\.env'
STAT_FILE = r'C:\Users\Administrator\fx_bot\vps\tod_stats.json'

# Fix1: キャッシュTTL
CACHE_TTL = 23 * 3600

# Fix4: BT結果ファイル
BT_RESULT_FILE = r'C:\Users\Administrator\fx_bot\optimizer\tod_bt_result.json'

# 対象ペア設定（Fix4でBT最適値に上書きされる）
TOD_PAIRS = {
    'EURUSD': {
        'is_jpy':      False,
        'entry_sigma': 2.5,
        'tp_atr_mult': 1.0,
        'sl_atr_mult': 1.5,
        'max_pos':     1,
    },
    'GBPUSD': {
        'is_jpy':      False,
        'entry_sigma': 2.5,
        'tp_atr_mult': 1.0,
        'sl_atr_mult': 2.0,
        'max_pos':     1,
    },
}

# 時間帯統計の計算設定
STAT_PARAMS = {
    'lookback_days': 730,
    'min_samples':   4,
    'h1_bars':       8760,   # 730日 * 24h（Fix1キャッシュ前提で大きく取る）
}

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
def load_env():
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

def log(msg, filepath=LOG_FILE):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def send_discord(msg, webhook):
    if not webhook:
        return
    try:
        import urllib.request, json as _json
        data = _json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log('Discord送信エラー: ' + str(e))

# ══════════════════════════════════════════
# Fix4: BT結果の自動反映
# ══════════════════════════════════════════
def apply_bt_params():
    """tod_bt_result.jsonを読み込みTOD_PAIRSパラメーターを上書き"""
    if not os.path.exists(BT_RESULT_FILE):
        log('BT結果ファイルなし → デフォルトパラメーター使用')
        return
    try:
        with open(BT_RESULT_FILE, encoding='utf-8') as f:
            bt = json.load(f)
    except Exception as e:
        log('BT結果読み込みエラー: ' + str(e))
        return
    for symbol, result in bt.items():
        if symbol in TOD_PAIRS:
            TOD_PAIRS[symbol]['entry_sigma'] = result['entry_sigma']
            TOD_PAIRS[symbol]['tp_atr_mult'] = result['tp_atr_mult']
            TOD_PAIRS[symbol]['sl_atr_mult'] = result['sl_atr_mult']
            log(symbol + ': BT最適値適用 sigma=' + str(result['entry_sigma']) +
                ' TP*' + str(result['tp_atr_mult']) +
                ' SL*' + str(result['sl_atr_mult']) +
                ' PF=' + str(result['pf']) +
                ' n=' + str(result['n']))

# ══════════════════════════════════════════
# MT5 ポジション管理
# ══════════════════════════════════════════
def count_total():
    pos = mt5.positions_get()
    return len(pos) if pos else 0

def count_by_strategy(strategy):
    pos = mt5.positions_get()
    if not pos:
        return 0
    return sum(1 for p in pos if p.comment == strategy)

def is_dup(symbol, strategy):
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return False
    return any(p.comment == strategy for p in pos)

def check_closed(webhook):
    """直近1時間の決済をログ記録"""
    hist = mt5.history_deals_get(
        datetime.now() - timedelta(hours=1),
        datetime.now()
    )
    if not hist:
        return
    for deal in hist:
        if deal.entry == mt5.DEAL_ENTRY_OUT and 'TOD_' in deal.comment:
            pnl  = round(deal.profit)
            sign = '+' if pnl >= 0 else ''
            log('決済: ' + deal.symbol +
                ' PnL=' + sign + str(pnl) + '円 ' + deal.comment)

def check_daily_loss(webhook):
    """日次損失チェック。上限超えたらFalseを返す"""
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    deals = mt5.history_deals_get(today_start, datetime.now())
    if not deals:
        return True
    daily_pnl = sum(d.profit for d in deals if 'TOD_' in d.comment)
    if daily_pnl < DAILY_LOSS_LIMIT:
        msg = ('【TOD警告】日次損失上限到達: ' +
               str(round(daily_pnl)) + '円 → 本日の取引停止')
        log(msg)
        send_discord(msg, webhook)
        return False
    return True

# ══════════════════════════════════════════
# Fix3: 市場クローズフィルター
# ══════════════════════════════════════════
def is_market_closed():
    """金曜22:00UTC以降 / 土日 / 月曜06:00UTC以前はTrueを返す"""
    now = datetime.now(timezone.utc)
    wd  = now.weekday()   # 0=月 4=金 5=土 6=日
    if wd == 4 and now.hour >= 22:
        return True   # 金曜夜
    if wd == 5 or wd == 6:
        return True   # 土日
    if wd == 0 and now.hour < 6:
        return True   # 月曜早朝
    return False

# ══════════════════════════════════════════
# 時間帯統計の計算（MT5の1時間足から）
# ══════════════════════════════════════════
def build_hour_stats(symbol: str) -> dict | None:
    """
    MT5から1時間足データを取得し、時間帯別統計を計算する。
    close-to-closeリターン使用（Fix2と一致）。
    戻り値: {hour_jst(0-23): {'mean': float, 'std': float, 'n': int}}
    """
    bars = mt5.copy_rates_from_pos(
        symbol, mt5.TIMEFRAME_H1, 0, STAT_PARAMS['h1_bars']
    )
    if bars is None or len(bars) < 100:
        log(symbol + ': 1時間足データ不足（' +
            str(len(bars) if bars is not None else 0) + '本）')
        return None

    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time', inplace=True)

    df['hour_jst'] = (df.index.hour + 9) % 24
    df['weekday']  = df.index.dayofweek   # 0=月 4=金

    # Fix2: close-to-closeリターン（calc_tod_signalと一致）
    df['ret'] = df['close'].pct_change()
    df = df.dropna(subset=['ret'])

    wd    = df[df['weekday'] <= 4]
    stats = {}
    for h, grp in wd.groupby('hour_jst'):
        if len(grp) < STAT_PARAMS['min_samples']:
            continue
        stats[int(h)] = {
            'mean': float(grp['ret'].mean()),
            'std':  float(grp['ret'].std()),
            'n':    int(len(grp)),
        }

    if len(stats) < 12:
        log(symbol + ': 時間帯統計の時間帯数不足（' +
            str(len(stats)) + '時間帯）')
        return None

    log(symbol + ': 時間帯統計計算完了（' +
        str(len(stats)) + '時間帯 / ' + str(len(df)) + '本）')
    return stats

# ══════════════════════════════════════════
# Fix1: 統計キャッシュ化
# ══════════════════════════════════════════
def save_stats(all_stats: dict):
    """時間帯統計をJSONに保存（Fix1キャッシュ用）"""
    try:
        with open(STAT_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log('統計保存エラー: ' + str(e))

# ══════════════════════════════════════════
# ATR計算（1時間足・EMA14）
# ══════════════════════════════════════════
def get_atr_h1(symbol: str, period: int = 14) -> float | None:
    """1時間足ATRをEMAで計算して返す"""
    bars = mt5.copy_rates_from_pos(
        symbol, mt5.TIMEFRAME_H1, 0, period + 5
    )
    if bars is None or len(bars) < period:
        return None

    highs  = [b['high']  for b in bars]
    lows   = [b['low']   for b in bars]
    closes = [b['close'] for b in bars]

    trs = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)

    atr = trs[0]
    k   = 2.0 / (period + 1)
    for tr in trs[1:]:
        atr = tr * k + atr * (1 - k)
    return atr

# ══════════════════════════════════════════
# TODシグナル計算
# ══════════════════════════════════════════
def calc_tod_signal(symbol: str, cfg: dict,
                    hour_stats: dict) -> dict | None:
    """
    現在時間帯のz_scoreを計算し、entry_sigmaを超えたらシグナル返却。

    フロー:
      1. 現在時刻のhour_jst取得
      2. Fix2: bars[-3]終値→bars[-2]終値のclose-to-closeリターン
      3. その時間帯のmean/stdでz_score算出
      4. |z_score| > entry_sigma → 逆方向シグナル
      5. ATRからTP/SL距離を計算
    """
    # ── Step1: 現在時刻のhour_jst ────────────────
    now_jst  = datetime.now(timezone.utc).hour
    hour_jst = (now_jst + 9) % 24

    weekday = datetime.now(timezone.utc).weekday()
    if weekday >= 5:
        log(symbol + ': 週末のためスキップ')
        return None

    # ── Step2: Fix2 close-to-closeリターン ───────
    # 4本取得: bars[-1]=現在足 bars[-2]=前足(直前完了) bars[-3]=前々足
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 4)
    if bars is None or len(bars) < 3:
        log(symbol + ': 1hデータ取得失敗')
        return None

    prev_close_0 = float(bars[-2]['close'])   # 直前完了足終値
    prev_close_1 = float(bars[-3]['close'])   # その前の足終値
    if prev_close_1 == 0:
        return None
    ret = (prev_close_0 - prev_close_1) / prev_close_1   # close-to-close

    # ── Step3: z_score計算 ───────────────────────
    stat = hour_stats.get(hour_jst)
    if stat is None:
        log(symbol + ': JST' + str(hour_jst) + '時の統計なし → スキップ')
        return None

    mean = stat['mean']
    std  = stat['std']
    if std == 0:
        return None

    z_score = (ret - mean) / std

    # ── Step4: シグナル判定 ───────────────────────
    entry_sigma = cfg['entry_sigma']
    if z_score < -entry_sigma:
        direction = 'buy'    # 過剰に下落 → 平均回帰でBUY
    elif z_score > entry_sigma:
        direction = 'sell'   # 過剰に上昇 → 平均回帰でSELL
    else:
        log(symbol + ': シグナルなし（JST' + str(hour_jst) + '時' +
            ' z=' + f'{z_score:+.2f}' +
            ' 閾値+-' + str(entry_sigma) + '）')
        return None

    # ── Step5: ATRからTP/SL計算 ──────────────────
    atr = get_atr_h1(symbol)
    if atr is None:
        log(symbol + ': ATR取得失敗')
        return None

    tp_dist = atr * cfg['tp_atr_mult']
    sl_dist = atr * cfg['sl_atr_mult']

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log(symbol + ': tick取得失敗')
        return None

    if direction == 'sell':
        entry = tick.bid
        tp    = entry - tp_dist
        sl    = entry + sl_dist
    else:
        entry = tick.ask
        tp    = entry + tp_dist
        sl    = entry - sl_dist

    pip     = 0.01 if cfg['is_jpy'] else 0.0001
    atr_pip = round(atr / pip, 1)

    log(symbol + ': シグナル確定 dir=' + direction +
        ' JST' + str(hour_jst) + '時' +
        ' z=' + f'{z_score:+.2f}' +
        ' ret=' + f'{ret*100:+.3f}%' +
        ' mean=' + f'{mean*100:+.3f}%' +
        ' ATR=' + str(atr_pip) + 'pips')

    return {
        'direction': direction,
        'entry':     entry,
        'tp':        tp,
        'sl':        sl,
        'z_score':   z_score,
        'ret':       ret,
        'hour_jst':  hour_jst,
        'atr':       atr,
        'atr_pips':  atr_pip,
    }

# ══════════════════════════════════════════
# 発注
# ══════════════════════════════════════════
def place_order(symbol: str, sig: dict,
                cfg: dict, webhook: str) -> bool:
    direction  = sig['direction']
    order_type = (mt5.ORDER_TYPE_SELL if direction == 'sell'
                  else mt5.ORDER_TYPE_BUY)

    info = mt5.symbol_info(symbol)
    if info is None:
        log('symbol_info取得失敗: ' + symbol)
        return False

    lot      = rm.calc_lot(symbol, sig['sl'], rm.get_balance())
    strategy = 'TOD_' + symbol

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        sig['entry'],
        'tp':           round(sig['tp'], info.digits),
        'sl':           round(sig['sl'], info.digits),
        'deviation':    10,
        'magic':        MAGIC_NUMBER,
        'comment':      strategy,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        if code != 'None':
            request['type_filling'] = mt5.ORDER_FILLING_IOC
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                log('発注失敗: ' + symbol +
                    ' code=' + str(result.retcode if result else 'None'))
                return False
        else:
            log('発注失敗: ' + symbol + ' code=None')
            return False

    msg = ('【TOD発注】' + symbol + ' ' + direction.upper() +
           ' lot=' + str(lot) +
           ' z=' + f'{sig["z_score"]:+.2f}' +
           ' JST' + str(sig['hour_jst']) + '時' +
           ' ATR=' + str(sig['atr_pips']) + 'pips' +
           ' TP=' + str(round(sig['tp'], info.digits)) +
           ' SL=' + str(round(sig['sl'], info.digits)))
    log(msg)
    send_discord(msg, webhook)
    return True

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    # MT5接続
    if not mt5.initialize():
        log('MT5初期化失敗')
        return

    try:
        account = mt5.account_info()
        if account is None:
            log('MT5口座情報取得失敗')
            mt5.shutdown()
            return
    except Exception as e:
        log('MT5接続エラー: ' + str(e))
        mt5.shutdown()
        return

    check_closed(webhook)

    # Fix3: 市場クローズ判定（土日・金曜夜・月曜早朝はスキップ）
    if is_market_closed():
        log('市場クローズ時間帯 → スキップ')
        mt5.shutdown()
        return

    if not check_daily_loss(webhook):
        mt5.shutdown()
        return

    # Fix4: BT最適パラメーターを適用
    apply_bt_params()

    # ── Fix1: 統計キャッシュ利用（23h以内なら再計算スキップ） ──
    all_stats  = {}
    needs_save = False
    for symbol in TOD_PAIRS:
        cached_stat = None
        if os.path.exists(STAT_FILE):
            try:
                age = _time.time() - os.path.getmtime(STAT_FILE)
                if age < CACHE_TTL:
                    with open(STAT_FILE, encoding='utf-8') as f:
                        cached = json.load(f)
                    cached_stat = cached.get(symbol)
            except Exception:
                pass

        if cached_stat is not None:
            all_stats[symbol] = cached_stat
        else:
            stats = build_hour_stats(symbol)
            if stats:
                all_stats[symbol] = stats
                needs_save = True

    if not all_stats:
        log('全ペアで統計計算失敗 → 終了')
        mt5.shutdown()
        return

    if needs_save:
        save_stats(all_stats)

    # ── シグナル検出・発注 ─────────────────────────
    executed = 0
    skipped  = 0

    for symbol, cfg in TOD_PAIRS.items():
        strategy = 'TOD_' + symbol

        if symbol not in all_stats:
            log(symbol + ': 統計なし → スキップ')
            skipped += 1
            continue

        if count_by_strategy(strategy) >= cfg['max_pos']:
            skipped += 1
            continue

        if is_dup(symbol, strategy):
            skipped += 1
            continue

        if count_total() >= MAX_TOTAL_POS:
            log('最大ポジション数到達 → 残りスキップ')
            break

        sig = calc_tod_signal(symbol, cfg, all_stats[symbol])
        if not sig:
            continue

        if place_order(symbol, sig, cfg, webhook):
            executed += 1

    now = datetime.now().strftime('%H:%M')
    log('[' + now + '] TOD v2完了: 発注' + str(executed) + '件 ' +
        'スキップ' + str(skipped) + '件 ' +
        'ポジション' + str(count_total()) + '/' + str(MAX_TOTAL_POS))

    mt5.shutdown()

if __name__ == '__main__':
    main()
