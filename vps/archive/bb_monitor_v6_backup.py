"""
bb_monitor.py  ― BB逆張り戦略（5分毎実行）v7
v6からの変更点:
  - HTFフィルター閾値: range_sigma 1.0 → 2.0（トレンド中の戻りを取れるように）
  - RSIフィルター閾値: sell_min 60→55 / buy_max 40→45（過剰除外を緩和）
  - bb_monitor内でATRを直接計算する get_atr_local() を実装
    → risk_manager.get_atr 属性エラーを完全に回避
  - TP/SL計算もrisk_manager非依存の内部実装に切り替え（フォールバック付き）
"""

import sys, os, json, time
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')
import risk_manager as rm

# ══════════════════════════════════════════
# 定数・設定
# ══════════════════════════════════════════
MAX_JPY_LOT   = 0.4
MAX_TOTAL_POS = 13

BB_PARAMS = {
    'period':     20,
    'sigma':      1.5,
    'rr':         1.0,
    'exit_sigma': 1.0,
}

# 1時間足レンジフィルター設定
# ★ v7変更: range_sigma 1.0 → 2.0
#   理由: σ±1.0はBB期間20の中で「外側1/3」に当たり常に除外されすぎる。
#   逆張りBBはトレンドの戻り場面が主戦場のため、±2.0（外れすぎ）のみ除外に緩和。
HTF_PARAMS = {
    'period':      20,
    'sigma':       1.5,
    'range_sigma': 2.0,   # ★ 1.0 → 2.0
    'bars':        50,
}

# RSIフィルター設定
# ★ v7変更: sell_min 60→55 / buy_max 40→45
#   理由: RSI60/40は「ほぼ中立域」の5分足では稀。55/45に緩和してBBタッチとの
#   一致条件を現実的なレベルに調整。
RSI_PARAMS = {
    'period':   14,
    'sell_min': 55,   # ★ 60 → 55
    'buy_max':  45,   # ★ 40 → 45
}

BB_PAIRS = {
    'USDCAD': {'is_jpy': False, 'max_pos': 1},
    'GBPJPY': {'is_jpy': True,  'max_pos': 1},
    'EURJPY': {'is_jpy': True,  'max_pos': 1},
    'USDJPY': {'is_jpy': True,  'max_pos': 1},
    'AUDJPY': {'is_jpy': True,  'max_pos': 1},
    'EURUSD': {'is_jpy': False, 'max_pos': 1},
    'GBPUSD': {'is_jpy': False, 'max_pos': 1},
}

LOG_FILE         = r'C:\Users\Administrator\fx_bot\vps\bb_log.txt'
ENV_FILE         = r'C:\Users\Administrator\fx_bot\vps\.env'
DAILY_LOSS_LIMIT = -50000  # 日次損失上限（円）

# ATRベースTP/SL（risk_manager.get_atr が使えない場合のフォールバック用）
BB_ATR_MULT_TP = 3.0   # TP = ATR × 3.0
BB_ATR_MULT_SL = 3.0   # SL = ATR × 3.0

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
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log('Discord送信エラー: ' + str(e))

# ══════════════════════════════════════════
# ★ v7追加: ATR直接計算（risk_manager非依存）
# ══════════════════════════════════════════
def get_atr_local(symbol, timeframe=None, period=14):
    """
    MT5から直接ATRを計算する。
    risk_manager.get_atr の代替実装。
    timeframe: mt5.TIMEFRAME_* 定数。Noneなら5分足。
    """
    if timeframe is None:
        timeframe = mt5.TIMEFRAME_M5
    bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, period + 5)
    if bars is None or len(bars) < period:
        return None
    highs  = [b['high']  for b in bars]
    lows   = [b['low']   for b in bars]
    closes = [b['close'] for b in bars]
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]),
        )
        trs.append(tr)
    # EMA
    atr = trs[0]
    k   = 2.0 / (period + 1)
    for tr in trs[1:]:
        atr = tr * k + atr * (1 - k)
    return atr

def calc_tp_sl_local(symbol, is_jpy):
    """
    risk_manager.calc_tp_sl / get_atr の代替。
    ATRベースでTP/SLを計算して (tp_dist, sl_dist) を返す。
    """
    atr = get_atr_local(symbol, mt5.TIMEFRAME_M5, 14)
    if atr is None:
        return None, None
    tp_dist = atr * BB_ATR_MULT_TP
    sl_dist = atr * BB_ATR_MULT_SL
    return tp_dist, sl_dist

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

def count_jpy_lots():
    pos = mt5.positions_get()
    if not pos:
        return 0.0
    total = 0.0
    for p in pos:
        sym = p.symbol
        if sym.endswith('JPY') or (len(sym) > 3 and sym[3:6] == 'JPY'):
            total += p.volume
    return total

def is_dup(symbol, strategy, logf):
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return False
    for p in pos:
        if p.comment == strategy:
            return True
    return False

def check_closed(logf, webhook):
    hist = mt5.history_deals_get(
        datetime.now() - timedelta(hours=1),
        datetime.now()
    )
    if not hist:
        return
    for deal in hist:
        if deal.entry == mt5.DEAL_ENTRY_OUT and 'BB_' in deal.comment:
            pnl  = round(deal.profit)
            sign = '+' if pnl >= 0 else ''
            log('決済: ' + deal.symbol + ' PnL=' + sign + str(pnl) + '円 ' + deal.comment, logf)

def check_daily_loss(logf, webhook):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today_start, datetime.now())
    if not deals:
        return True
    daily_pnl = sum(d.profit for d in deals if 'BB_' in d.comment)
    if daily_pnl < DAILY_LOSS_LIMIT:
        msg = '【BB警告】日次損失上限到達: ' + str(round(daily_pnl)) + '円 → 本日の取引停止'
        log(msg, logf)
        send_discord(msg, webhook)
        return False
    return True

# ══════════════════════════════════════════
# インジケーター計算
# ══════════════════════════════════════════
def calc_rsi(rates_df, period=14):
    close = rates_df['close']
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0

def calc_bb(rates_df, period, sigma):
    close   = rates_df['close']
    ma      = close.rolling(period).mean()
    std     = close.rolling(period).std()
    upper   = ma + sigma * std
    lower   = ma - sigma * std
    idx     = -2  # 1本シフト（先読み防止）
    ma_v    = float(ma.iloc[idx])
    std_v   = float(std.iloc[idx])
    upper_v = float(upper.iloc[idx])
    lower_v = float(lower.iloc[idx])
    close_v = float(close.iloc[idx])
    sigma_pos = (close_v - ma_v) / std_v if std_v > 0 else 0.0
    return {
        'ma':        ma_v,
        'upper':     upper_v,
        'lower':     lower_v,
        'close':     close_v,
        'sigma_pos': sigma_pos,
    }

# ══════════════════════════════════════════
# 上位足フィルター（1時間足）
# ══════════════════════════════════════════
def htf_range_filter(symbol):
    """
    1時間足でレンジ相場かどうかを判定する。
    v7: range_sigma=2.0 に緩和。σ±2.0超のみトレンドとしてスキップ。
    """
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, HTF_PARAMS['bars'])
    if bars is None or len(bars) < HTF_PARAMS['period'] + 5:
        return True, 0.0, '1hデータ不足（スキップ判定せず通過）'

    df  = pd.DataFrame(bars)
    bb  = calc_bb(df, HTF_PARAMS['period'], HTF_PARAMS['sigma'])

    sigma_pos   = bb['sigma_pos']
    range_limit = HTF_PARAMS['range_sigma']

    if abs(sigma_pos) > range_limit:
        direction = '上方トレンド' if sigma_pos > 0 else '下方トレンド'
        reason = '1h足 ' + direction + '（σ=' + f'{sigma_pos:+.2f}' + '）'
        return False, sigma_pos, reason

    return True, sigma_pos, 'HTF通過（1hσ=' + f'{sigma_pos:+.2f}' + '）'

# ══════════════════════════════════════════
# RSIフィルター（5分足）
# ══════════════════════════════════════════
def rsi_filter(rates_df, direction):
    """
    v7: sell_min=55 / buy_max=45 に緩和。
    """
    rsi_val = calc_rsi(rates_df, RSI_PARAMS['period'])

    if direction == 'sell':
        if rsi_val < RSI_PARAMS['sell_min']:
            reason = 'RSI未達（' + f'{rsi_val:.1f}' + ' < ' + str(RSI_PARAMS['sell_min']) + '、買われすぎ未確認）'
            return False, rsi_val, reason
    else:  # buy
        if rsi_val > RSI_PARAMS['buy_max']:
            reason = 'RSI未達（' + f'{rsi_val:.1f}' + ' > ' + str(RSI_PARAMS['buy_max']) + '、売られすぎ未確認）'
            return False, rsi_val, reason

    return True, rsi_val, 'RSI OK（' + f'{rsi_val:.1f}' + '）'

# ══════════════════════════════════════════
# BBシグナル計算（メイン）
# ══════════════════════════════════════════
def calc_bb_signal(symbol, is_jpy):
    """
    フィルター適用順:
      1. 1時間足レンジフィルター（σ±2.0超のトレンド中はスキップ）
      2. 5分足BBタッチ確認
      3. RSIフィルター（55/45基準）
    """
    # ── Step1: 1時間足レンジフィルター ──────────
    is_range, htf_sigma, htf_reason = htf_range_filter(symbol)
    if not is_range:
        log(symbol + ': HTFスキップ → ' + htf_reason)
        return None

    # ── Step2: 5分足BBタッチ確認 ──────────────
    bars_5m = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 60)
    if bars_5m is None or len(bars_5m) < BB_PARAMS['period'] + 5:
        return None

    df_5m = pd.DataFrame(bars_5m)
    bb    = calc_bb(df_5m, BB_PARAMS['period'], BB_PARAMS['sigma'])

    close     = bb['close']
    upper     = bb['upper']
    lower     = bb['lower']
    sigma_pos = bb['sigma_pos']

    # バンドタッチ判定
    if close >= upper:
        direction = 'sell'
    elif close <= lower:
        direction = 'buy'
    else:
        return None  # バンドタッチなし

    # ── Step3: RSIフィルター ──────────────────
    rsi_ok, rsi_val, rsi_reason = rsi_filter(df_5m, direction)
    if not rsi_ok:
        log(symbol + ': RSIスキップ → ' + rsi_reason + ' ' + htf_reason)
        return None

    # ── TP/SL計算 ────────────────────────────
    # ★ v7: ATRをローカル取得 → rm.calc_tp_sl(atr, strategy, is_jpy) に渡す
    #   risk_manager v2のシグネチャ: calc_tp_sl(atr, strategy, is_jpy)
    #   ※ rm.get_atr / rm.get_balance は存在しないため使用しない
    atr_val = get_atr_local(symbol, mt5.TIMEFRAME_M5, 14)
    if atr_val is None:
        log(symbol + ': ATR取得失敗')
        return None

    try:
        tp_dist, sl_dist = rm.calc_tp_sl(atr_val, 'BB', is_jpy=is_jpy)
    except Exception as e:
        log(symbol + ': calc_tp_sl エラー: ' + str(e))
        # フォールバック: ATR×定数で直接計算
        tp_dist = atr_val * BB_ATR_MULT_TP
        sl_dist = atr_val * BB_ATR_MULT_SL

    # ── 方向別価格 ────────────────────────────
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None

    if direction == 'sell':
        entry = tick.bid
        tp    = entry - tp_dist
        sl    = entry + sl_dist
    else:
        entry = tick.ask
        tp    = entry + tp_dist
        sl    = entry - sl_dist

    log(symbol + ': シグナル確定 dir=' + direction +
        ' σ=' + f'{sigma_pos:+.2f}' +
        ' RSI=' + f'{rsi_val:.1f}' +
        ' ' + htf_reason)

    return {
        'direction': direction,
        'entry':     entry,
        'tp':        tp,
        'sl':        sl,
        'sigma_pos': sigma_pos,
        'rsi':       rsi_val,
        'htf_sigma': htf_sigma,
    }

# ══════════════════════════════════════════
# 発注
# ══════════════════════════════════════════
def place_order(symbol, sig, logf, webhook):
    direction  = sig['direction']
    order_type = mt5.ORDER_TYPE_SELL if direction == 'sell' else mt5.ORDER_TYPE_BUY

    info = mt5.symbol_info(symbol)
    if info is None:
        log('symbol_info取得失敗: ' + symbol, logf)
        return False

    # risk_manager v2: calc_lot(balance, sl_dist, symbol)
    # rm.get_balance は存在しないためMT5から直接取得
    _account = mt5.account_info()
    _balance = _account.balance if _account else 1_000_000
    _sl_dist = abs(sig['entry'] - sig['sl'])
    lot      = rm.calc_lot(_balance, _sl_dist, symbol)
    strategy = 'BB_' + symbol

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        sig['entry'],
        'tp':           round(sig['tp'], info.digits),
        'sl':           round(sig['sl'], info.digits),
        'deviation':    10,
        'magic':        20250001,
        'comment':      strategy,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        log('発注失敗: ' + symbol + ' code=' + str(code), logf)
        return False

    msg = ('【BB発注】' + symbol + ' ' + direction.upper() +
           ' lot=' + str(lot) +
           ' σ=' + f'{sig["sigma_pos"]:+.2f}' +
           ' RSI=' + f'{sig["rsi"]:.1f}' +
           ' 1hσ=' + f'{sig["htf_sigma"]:+.2f}')
    log(msg, logf)
     # Discord通知はサマリーのみ（逐次通知なし）
    return True

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')
    logf    = LOG_FILE

    if not mt5.initialize():
        log('MT5初期化失敗', logf)
        return

    try:
        account = mt5.account_info()
        if account is None:
            log('MT5口座情報取得失敗', logf)
            mt5.shutdown()
            return
    except Exception as e:
        log('MT5接続エラー: ' + str(e), logf)
        mt5.shutdown()
        return

    check_closed(logf, webhook)

    if not check_daily_loss(logf, webhook):
        mt5.shutdown()
        return

    executed = 0
    skipped  = 0

    for symbol, cfg in BB_PAIRS.items():
        strategy = 'BB_' + symbol
        if count_by_strategy(strategy) >= cfg['max_pos']:
            skipped += 1
            continue
        if is_dup(symbol, strategy, logf):
            skipped += 1
            continue
        if count_total() >= MAX_TOTAL_POS:
            break
        if cfg['is_jpy'] and count_jpy_lots() >= MAX_JPY_LOT:
            log('JPYロット上限: ' + symbol + ' スキップ')
            skipped += 1
            continue

        sig = calc_bb_signal(symbol, cfg['is_jpy'])
        if not sig:
            continue
        if place_order(symbol, sig, logf, webhook):
            executed += 1

    now = datetime.now().strftime('%H:%M')
    log('[' + now + '] BB v7完了: 発注' + str(executed) + '件 ' +
        'スキップ' + str(skipped) + '件 ' +
        'ポジション' + str(count_total()) + '/' + str(MAX_TOTAL_POS))

    mt5.shutdown()

if __name__ == '__main__':
    main()