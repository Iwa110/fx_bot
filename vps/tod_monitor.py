"""
tod_monitor.py  ― 時間帯別平均回帰戦略（1時間毎実行）v1
================================================================
戦略概要:
  - 各時間帯の「平均リターン±entry_sigma×std」から大きく乖離したら
    平均回帰方向にエントリー
  - 時間帯統計は起動時に過去730日の1時間足から計算（訓練期間相当）
  - 1時間ごとに実行（cronまたはタスクスケジューラー）

バックテスト結果（検証期間）:
  EURUSD: Sharpe 8.38 / +18.3% / DD 5.5% / 勝率38.4% / RR2.06
  GBPUSD: Sharpe 8.70 / +5.2%  / DD 2.8% / 勝率65.7% / RR0.66

パラメーター（バックテスト最適値）:
  EURUSD: entry_sigma=1.5 / TP=ATR×1.0 / SL=ATR×0.5
  GBPUSD: entry_sigma=3.0 / TP=ATR×1.0 / SL=ATR×1.5

実行方法（タスクスケジューラー）:
  毎時0分に python tod_monitor.py を実行
"""

import sys, os, json
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

# 対象ペア設定
TOD_PAIRS = {
    'EURUSD': {
        'is_jpy':      False,
        'entry_sigma': 1.5,
        'tp_atr_mult': 1.0,
        'sl_atr_mult': 0.5,
        'max_pos':     1,
    },
    'GBPUSD': {
        'is_jpy':      False,
        'entry_sigma': 3.0,
        'tp_atr_mult': 1.0,
        'sl_atr_mult': 1.5,
        'max_pos':     1,
    },
}

# 時間帯統計の計算設定
STAT_PARAMS = {
    'lookback_days': 730,    # 過去730日（yfinanceと同じ期間）
    'min_samples':   4,      # 時間帯ごとの最低サンプル数
    'h1_bars':       800,    # MT5から取得する1時間足本数
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
# 時間帯統計の計算（MT5の1時間足から）
# ══════════════════════════════════════════
def build_hour_stats(symbol: str) -> dict | None:
    """
    MT5から1時間足データを取得し、時間帯別統計を計算する。
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

    # JST換算（UTC+9）
    df['hour_jst'] = (df.index.hour + 9) % 24
    df['weekday']  = df.index.dayofweek   # 0=月 4=金

    # 1時間リターン
    df['ret'] = df['close'].pct_change()
    df = df.dropna(subset=['ret'])

    # 時間帯別統計（平日のみ: weekday<=4）
    wd = df[df['weekday'] <= 4]
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

def save_stats(all_stats: dict):
    """時間帯統計をJSONに保存（デバッグ・再利用用）"""
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
      2. 直前1時間足のリターンを計算
      3. その時間帯のmean/stdでz_score算出
      4. |z_score| > entry_sigma → 逆方向シグナル
      5. ATRからTP/SL距離を計算
    """
    # ── Step1: 現在時刻のhour_jst ────────────────
    now_jst  = datetime.now(timezone.utc).hour
    hour_jst = (now_jst + 9) % 24

    # 土日チェック（MT5市場クローズ）
    weekday = datetime.now(timezone.utc).weekday()
    if weekday >= 5:
        log(symbol + ': 週末のためスキップ')
        return None

    # ── Step2: 直前1時間足のリターン ────────────
    # 2本取得して、1本前（確定足）のリターンを使用
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 3)
    if bars is None or len(bars) < 2:
        log(symbol + ': 1hデータ取得失敗')
        return None

    prev_close = float(bars[-2]['close'])
    prev_open  = float(bars[-2]['open'])
    if prev_open == 0:
        return None
    ret = (prev_close - prev_open) / prev_open   # 簡易リターン（open→close）

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
            ' 閾値±' + str(entry_sigma) + '）')
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
        # FOK失敗時はIOCでリトライ
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

    if not check_daily_loss(webhook):
        mt5.shutdown()
        return

    # ── 時間帯統計の計算（全ペア分） ──────────────
    all_stats = {}
    for symbol in TOD_PAIRS:
        stats = build_hour_stats(symbol)
        if stats:
            all_stats[symbol] = stats

    if not all_stats:
        log('全ペアで統計計算失敗 → 終了')
        mt5.shutdown()
        return

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
    log('[' + now + '] TOD v1完了: 発注' + str(executed) + '件 ' +
        'スキップ' + str(skipped) + '件 ' +
        'ポジション' + str(count_total()) + '/' + str(MAX_TOTAL_POS))

    mt5.shutdown()

if __name__ == '__main__':
    main()