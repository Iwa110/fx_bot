"""
bb_monitor.py  ― BB逆張り戦略（5分毎実行）v8
v7からの変更:
  - SLクールダウン追加（SL後15分以内の同ペア再エントリーをブロック）
v7の内容:
  - BB発注のDiscord逐次通知を廃止 → ログのみ・Discord通知はsummary_notify.pyサマリーに集約
  - BBバンド非到達もログ出力 → 全7ペアが毎回いずれかのログを出す
  - calc_lot引数順バグ修正（balance, abs(sl-entry), symbol）
  - ORDER_FILLING_IOC → FOK に変更（TitanFX非対応のため）
  - Discord SSL証明書エラー対応（Windows VPS用）
v6の内容:
  - 1時間足レンジフィルター追加（上位足トレンド中はスキップ）
  - RSIフィルター追加（過熱感を数値確認してからエントリー）
  - フィルター理由をログ出力
"""

import sys, os, ssl, json
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')
import risk_manager as rm

# ══════════════════════════════════════════
# 定数・設定
# ══════════════════════════════════════════
MAX_JPY_LOT      = 0.4
MAX_TOTAL_POS    = 13
COOLDOWN_MINUTES = 15   # SL後クールダウン（分）

BB_PARAMS = {
    'period':     20,
    'sigma':      1.5,
    'rr':         1.0,
    'exit_sigma': 1.0,
}

# 1時間足レンジフィルター設定
HTF_PARAMS = {
    'period':      20,   # 1h足BBの期間
    'sigma':       1.5,  # 1h足BBのσ
    'range_sigma': 1.0,  # これ以内なら「レンジ」と判定
    'bars':        50,   # 取得バー数
}

# RSIフィルター設定
RSI_PARAMS = {
    'period':   14,
    'sell_min': 60,  # SELL: RSI ≥ この値（買われすぎ確認）
    'buy_max':  40,  # BUY:  RSI ≤ この値（売られすぎ確認）
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
    """Discord通知（Windows VPS SSL対応）"""
    if not webhook:
        return
    try:
        import urllib.request, json as _json
        data = _json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
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

def is_in_cooldown(symbol):
    """SL決済後COOLDOWN_MINUTES分以内の同ペア再エントリーをブロック"""
    from_dt = datetime.now() - timedelta(minutes=COOLDOWN_MINUTES)
    deals   = mt5.history_deals_get(from_dt, datetime.now())
    if not deals:
        return False
    for d in deals:
        if (d.symbol == symbol
                and d.entry == mt5.DEAL_ENTRY_OUT
                and 'BB_' in d.comment
                and d.profit < 0):
            elapsed = round((datetime.now().timestamp() - d.time), 0)
            log(symbol + ': クールダウン中（SL後' +
                str(int(elapsed // 60)) + '分' + str(int(elapsed % 60)) + '秒）')
            return True
    return False

def check_closed(logf, webhook):
    """決済済みポジションを確認してログ記録"""
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
    """日次損失チェック"""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals       = mt5.history_deals_get(today_start, datetime.now())
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
    """RSIを計算して最新値を返す"""
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
    """BB上限・下限・MAと最新σ位置を返す（1本シフト済み）"""
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
    戻り値: (is_range: bool, sigma_pos: float, reason: str)
      is_range=True  → レンジ相場 → エントリー可
      is_range=False → トレンド中 → スキップ
    """
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, HTF_PARAMS['bars'])
    if bars is None or len(bars) < HTF_PARAMS['period'] + 5:
        return True, 0.0, '1hデータ不足（スキップ判定せず通過）'

    df          = pd.DataFrame(bars)
    bb          = calc_bb(df, HTF_PARAMS['period'], HTF_PARAMS['sigma'])
    sigma_pos   = bb['sigma_pos']
    range_limit = HTF_PARAMS['range_sigma']

    if abs(sigma_pos) > range_limit:
        direction = '上方トレンド' if sigma_pos > 0 else '下方トレンド'
        reason    = '1h足 ' + direction + '（σ=' + f'{sigma_pos:+.2f}' + '）'
        return False, sigma_pos, reason

    return True, sigma_pos, 'レンジ判定OK（1hσ=' + f'{sigma_pos:+.2f}' + '）'

# ══════════════════════════════════════════
# RSIフィルター（5分足）
# ══════════════════════════════════════════
def rsi_filter(rates_df, direction):
    """
    RSIが実際に過熱圏にあることを確認する。
    direction: 'buy' or 'sell'
    戻り値: (ok: bool, rsi_val: float, reason: str)
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
      1. 1時間足レンジフィルター（トレンド中はスキップ）
      2. 5分足BBタッチ確認
      3. RSIフィルター（過熱感確認）
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

    df_5m     = pd.DataFrame(bars_5m)
    bb        = calc_bb(df_5m, BB_PARAMS['period'], BB_PARAMS['sigma'])
    close     = bb['close']
    upper     = bb['upper']
    lower     = bb['lower']
    sigma_pos = bb['sigma_pos']

    if close >= upper:
        direction = 'sell'
    elif close <= lower:
        direction = 'buy'
    else:
        log(symbol + ': BBバンド未到達（σ=' + f'{sigma_pos:+.2f}' + '） ' + htf_reason)
        return None

    # ── Step3: RSIフィルター ──────────────────
    rsi_ok, rsi_val, rsi_reason = rsi_filter(df_5m, direction)
    if not rsi_ok:
        log(symbol + ': RSIスキップ → ' + rsi_reason + ' HTF:' + htf_reason)
        return None

    # ── TP/SL計算 ────────────────────────────
    try:
        tp_dist, sl_dist = rm.calc_tp_sl(
            rm.get_atr(symbol, 'BB'),
            'BB',
            is_jpy=is_jpy
        )
    except Exception as e:
        log(symbol + ': TP/SL計算エラー: ' + str(e))
        return None

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

    balance  = rm.get_balance()
    sl_dist  = abs(sig['sl'] - sig['entry'])
    lot      = rm.calc_lot(balance, sl_dist, symbol)
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

    msg = ('BB発注: ' + symbol + ' ' + direction.upper() +
           ' lot=' + str(lot) +
           ' σ=' + f'{sig["sigma_pos"]:+.2f}' +
           ' RSI=' + f'{sig["rsi"]:.1f}' +
           ' 1hσ=' + f'{sig["htf_sigma"]:+.2f}')
    log(msg, logf)
    # Discord通知はsummary_notify.pyのサマリーに集約（逐次通知なし）
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
        if is_in_cooldown(symbol):          # ← v8追加: SLクールダウン
            skipped += 1
            continue

        sig = calc_bb_signal(symbol, cfg['is_jpy'])
        if not sig:
            continue
        if place_order(symbol, sig, logf, webhook):
            executed += 1

    now = datetime.now().strftime('%H:%M')
    log('[' + now + '] BB v8完了: 発注' + str(executed) + '件 ' +
        'スキップ' + str(skipped) + '件 ' +
        'ポジション' + str(count_total()) + '/' + str(MAX_TOTAL_POS))

    mt5.shutdown()

if __name__ == '__main__':
    main()