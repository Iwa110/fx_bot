"""
sma_squeeze.py - SMA Squeeze Play strategy monitor v2
Trend-following: SMA200 slope filter + SMA20 squeeze/expansion entry.
magic: 20260010
v2 2026-05-12: A-1 SMA_long slope reversal exit + B-1 breakeven SL move
  be_r=0.5 / slope_exit=3 (BT-optimized: sma_squeeze_exit_bt.py, 80 runs)
v2.1 2026-05-13: enhanced debug logging in check_entry (sub-condition breakdown)
"""

import sys, os, time, argparse, ssl, urllib.request
from datetime import datetime, timezone
import json as _json

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# Strategy constants
# ══════════════════════════════════════════
MAGIC        = 20260010
STRATEGY_TAG = 'SMA_SQ'
BROKER_KEY   = 'oanda'

_SYMBOL_MAP: dict[str, str] = {}

def _rsym(base: str) -> str:
    return _SYMBOL_MAP.get(base, base)

# ══════════════════════════════════════════
# Config
# BT best params (PF>1.2, n>=30) from sma_squeeze_bt.py
# BT period: 2024-04-24 ~ 2026-04-24, 9720 runs
# v2 exit params from sma_squeeze_exit_bt.py (80 runs):
#   be_r=0.5 (universal), slope_exit=3 (GBPJPY +PF, others neutral)
# ══════════════════════════════════════════
PAIRS_CFG = {
    'USDJPY': {'sma_short': 25, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 5,  'rr': 2.5, 'sl_atr_mult': 1.5,
               'timeframe': '4h', 'be_r': 0.5, 'slope_exit': 3, 'enabled': True},
    'GBPJPY': {'sma_short': 25, 'sma_long': 250, 'squeeze_th': 0.5,
               'slope_period': 10, 'rr': 2.0, 'sl_atr_mult': 1.5,
               'timeframe': '1h', 'be_r': 0.5, 'slope_exit': 3, 'enabled': True},
    'EURUSD': {'sma_short': 25, 'sma_long': 200, 'squeeze_th': 2.0,
               'slope_period': 10, 'rr': 2.5, 'sl_atr_mult': 1.0,
               'timeframe': '4h', 'be_r': 0.5, 'slope_exit': 3, 'enabled': True},
    'GBPUSD': {'sma_short': 15, 'sma_long': 250, 'squeeze_th': 1.5,
               'slope_period': 20, 'rr': 2.0, 'sl_atr_mult': 1.0,
               'timeframe': '1h', 'be_r': 0.5, 'slope_exit': 3, 'enabled': True},
    'EURJPY': {'sma_short': 15, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 20, 'rr': 2.5, 'sl_atr_mult': 1.5,
               'timeframe': '4h', 'be_r': 0.5, 'slope_exit': 3, 'enabled': True},
}

MAX_JPY_LOT   = 0.4
MAX_TOTAL_POS = 3
COOLDOWN_MIN  = 60
LOOP_INTERVAL = 60

DEBUG = False

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sma_squeeze_log.txt')
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

_last_entry: dict[str, datetime] = {}

# ══════════════════════════════════════════
# Utilities
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


def log_print(msg, debug=False):
    if debug and not DEBUG:
        return
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def send_discord(msg, webhook):
    if not webhook:
        return
    try:
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
# OHLCV fetch
# ══════════════════════════════════════════
def get_ohlcv(symbol, tf, n, broker):
    """Fetch OHLCV from MT5. tf='4h' resamples from 1h bars."""
    if tf == '4h':
        df_1h = get_ohlcv(symbol, '1h', n * 4 + 20, broker)
        if df_1h is None:
            return None
        return resample_4h(df_1h)

    tf_map = {'1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4}
    mt5_tf = tf_map.get(tf, mt5.TIMEFRAME_H1)
    bars   = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, n)
    if bars is None or len(bars) < 5:
        return None
    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df = df.rename(columns={'time': 'datetime', 'tick_volume': 'volume'})
    cols = [c for c in ['datetime', 'open', 'high', 'low', 'close', 'volume'] if c in df.columns]
    return df[cols].sort_values('datetime').reset_index(drop=True)


def resample_4h(df_1h):
    """Resample 1h DataFrame to 4h OHLCV."""
    df = df_1h.set_index('datetime')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in df.columns:
        agg['volume'] = 'sum'
    df4h = df.resample('4h').agg(agg).dropna(subset=['close'])
    df4h.index.name = 'datetime'
    return df4h.reset_index()


# ══════════════════════════════════════════
# Indicators
# ══════════════════════════════════════════
def calc_indicators(df, cfg):
    """Return copy of df with sma_short, sma_long, atr14, adx14 columns added."""
    df    = df.copy()
    close = df['close']
    high  = df['high']
    low   = df['low']

    df['sma_short'] = close.rolling(cfg['sma_short']).mean()
    df['sma_long']  = close.rolling(cfg['sma_long']).mean()

    hl   = high - low
    hc   = (high - close.shift()).abs()
    lc   = (low  - close.shift()).abs()
    tr   = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr14'] = tr.rolling(14).mean()

    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_s    = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df['adx14'] = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()

    return df


def calc_slope(sma_series, period):
    """
    Return True if last `period` bars are strictly monotonically rising,
    False if strictly falling, None if neither.
    """
    vals = sma_series.dropna().values
    if len(vals) < period:
        return None
    seg   = vals[-period:]
    diffs = np.diff(seg)
    if np.all(diffs > 0):
        return True
    if np.all(diffs < 0):
        return False
    return None


def calc_squeeze_ratio(sma_short_val, sma_long_val):
    """Divergence rate % between SMA short and SMA long."""
    if sma_long_val == 0.0:
        return 999.0
    return abs(sma_short_val - sma_long_val) / sma_long_val * 100.0


# ══════════════════════════════════════════
# Entry signal
# ══════════════════════════════════════════
def check_entry(df, cfg, base_sym=''):
    """
    Check entry on last confirmed bar (iloc[-2]).
    Returns 'long' / 'short' / None.
    With debug logging: shows exactly which sub-condition fails and by how much.
    """
    min_bars = cfg['sma_long'] + cfg['slope_period'] + 5
    if len(df) < min_bars:
        log_print(base_sym + ': bars=' + str(len(df)) + ' < ' + str(min_bars) + ' (not enough)', debug=True)
        return None

    cur  = df.iloc[-2]
    prev = df.iloc[-3]

    sl_v  = cur['sma_long']
    ss_v  = cur['sma_short']
    atr_v = cur['atr14']
    adx_v = cur['adx14']
    c     = cur['close']
    o     = cur['open']

    if any(pd.isna(x) for x in [sl_v, ss_v, atr_v, adx_v, c, o]):
        log_print(base_sym + ': NaN in indicators  skip', debug=True)
        return None

    # ADX filter
    if adx_v <= 20.0:
        log_print(base_sym + ': ADX=' + f'{adx_v:.1f}' + ' <= 20  skip', debug=True)
        return None

    # Squeeze filter
    sq_ratio = calc_squeeze_ratio(ss_v, sl_v)
    if sq_ratio > cfg['squeeze_th']:
        log_print(base_sym + ': sq=' + f'{sq_ratio:.3f}' + ' > th=' + str(cfg['squeeze_th']) + '  skip', debug=True)
        return None

    # Slope filter
    slope = calc_slope(df['sma_long'], cfg['slope_period'])
    if slope is None:
        # Show last few diffs to diagnose non-monotone slope
        vals  = df['sma_long'].dropna().values
        seg   = vals[-cfg['slope_period']:]
        diffs = np.diff(seg)
        log_print(base_sym + ': slope=None (non-monotone)  diffs=' +
                  str([round(float(d), 6) for d in diffs]), debug=True)
        return None

    prev_c = prev['close']
    prev_s = prev['sma_short']
    if pd.isna(prev_c) or pd.isna(prev_s):
        return None

    slope_dir = 'UP' if slope else 'DN'

    # Check directional conditions and log sub-condition breakdown on miss
    if slope is True:
        if c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            return 'long'
        fails = []
        if not (c > sl_v):
            fails.append('c>SMAlong?NO gap=' + f'{c - sl_v:.5f}')
        if not (prev_c < prev_s):
            fails.append('prev_c<SMAshort?NO gap=' + f'{prev_c - prev_s:.5f}')
        if not (c > ss_v):
            fails.append('c>SMAshort?NO gap=' + f'{c - ss_v:.5f}')
        if not (c > o):
            fails.append('bullish_bar?NO c-o=' + f'{c - o:.5f}')
        log_print(base_sym + ': LONG_miss slope=' + slope_dir +
                  ' ADX=' + f'{adx_v:.1f}' +
                  ' sq=' + f'{sq_ratio:.3f}' +
                  '  ' + '  '.join(fails), debug=True)
    else:
        if c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            return 'short'
        fails = []
        if not (c < sl_v):
            fails.append('c<SMAlong?NO gap=' + f'{sl_v - c:.5f}')
        if not (prev_c > prev_s):
            fails.append('prev_c>SMAshort?NO gap=' + f'{prev_s - prev_c:.5f}')
        if not (c < ss_v):
            fails.append('c<SMAshort?NO gap=' + f'{ss_v - c:.5f}')
        if not (c < o):
            fails.append('bearish_bar?NO o-c=' + f'{o - c:.5f}')
        log_print(base_sym + ': SHORT_miss slope=' + slope_dir +
                  ' ADX=' + f'{adx_v:.1f}' +
                  ' sq=' + f'{sq_ratio:.3f}' +
                  '  ' + '  '.join(fails), debug=True)

    return None


# ══════════════════════════════════════════
# Position helpers
# ══════════════════════════════════════════
def count_total_strategy():
    pos = mt5.positions_get()
    if not pos:
        return 0
    return sum(1 for p in pos if p.magic == MAGIC)


def count_jpy_lots():
    pos = mt5.positions_get()
    if not pos:
        return 0.0
    return sum(p.volume for p in pos if p.magic == MAGIC and 'JPY' in p.symbol)


def has_open_position(symbol):
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return False
    return any(p.magic == MAGIC for p in pos)


def is_in_cooldown(base_sym):
    last = _last_entry.get(base_sym)
    if last is None:
        return False
    elapsed = (datetime.now() - last).total_seconds() / 60.0
    if elapsed < COOLDOWN_MIN:
        log_print(base_sym + ': cooldown ' + f'{elapsed:.0f}/{COOLDOWN_MIN}min', debug=True)
        return True
    return False


# ══════════════════════════════════════════
# Position management
# ══════════════════════════════════════════

# v2 2026-05-12: factored out close helper (used by force-close and A-1 slope-exit)
def _close_position(p, is_long, comment):
    """Send market close order. Returns True on success."""
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
    tick       = mt5.symbol_info_tick(p.symbol)
    if tick is None:
        return False
    price = tick.bid if is_long else tick.ask
    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       p.symbol,
        'volume':       p.volume,
        'type':         close_type,
        'price':        price,
        'deviation':    10,
        'magic':        MAGIC,
        'comment':      comment,
        'position':     p.ticket,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True
    code = result.retcode if result else 'None'
    log_print('close failed: ' + p.symbol + ' code=' + str(code))
    return False


# v2 2026-05-12: B-1 breakeven move (profit >= be_r * original_sl_dist -> SL to entry)
def _check_breakeven(p, is_long, be_r, webhook):
    """Move SL to entry when unrealized profit >= be_r * (price_open - original_sl)."""
    info = mt5.symbol_info(p.symbol)
    tick = mt5.symbol_info_tick(p.symbol)
    if info is None or tick is None:
        return

    if is_long:
        if p.sl >= p.price_open - info.point:   # BE already applied
            return
        orig_sl_dist = p.price_open - p.sl
        if orig_sl_dist <= 0:
            return
        profit = tick.bid - p.price_open
    else:
        if p.sl <= p.price_open + info.point:   # BE already applied
            return
        orig_sl_dist = p.sl - p.price_open
        if orig_sl_dist <= 0:
            return
        profit = p.price_open - tick.ask

    log_print('BE check: ' + p.symbol +
              '  profit=' + str(round(profit, info.digits)) +
              '  need='   + str(round(be_r * orig_sl_dist, info.digits)), debug=True)

    if profit < be_r * orig_sl_dist:
        return

    new_sl = round(p.price_open, info.digits)
    req = {
        'action':   mt5.TRADE_ACTION_SLTP,
        'position': p.ticket,
        'sl':       new_sl,
        'tp':       p.tp,
    }
    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        side = 'LONG' if is_long else 'SHORT'
        msg  = (STRATEGY_TAG + ' BE: ' + p.symbol + ' ' + side +
                '  SL->entry=' + str(new_sl) + '  ticket=' + str(p.ticket))
        log_print(msg)
        send_discord(msg, webhook)
    else:
        code = result.retcode if result else 'None'
        log_print('BE modify failed: ' + p.symbol + ' code=' + str(code))


def manage_positions(broker, webhook):
    """
    Manage open positions per confirmed bar (iloc[-2]):
    - Force close: SMA_long break in opposite direction (priority)
    - A-1: SMA_long slope reversal exit (v2 2026-05-12)
    - B-1: Breakeven SL move when profit >= be_r * orig_sl_dist (v2 2026-05-12)
    """
    pos = mt5.positions_get()
    if not pos:
        return

    for p in pos:
        if p.magic != MAGIC:
            continue

        cfg      = None
        base_sym = None
        for k, v in PAIRS_CFG.items():
            if p.symbol == _rsym(k):
                cfg      = v
                base_sym = k
                break
        if cfg is None:
            continue

        tf = cfg.get('timeframe', '1h')
        n  = cfg['sma_long'] + max(cfg.get('slope_exit', 3), cfg.get('slope_period', 5)) + 10
        df = get_ohlcv(p.symbol, tf, n, broker)
        if df is None or len(df) < cfg['sma_long'] + 2:
            continue

        df_ind = calc_indicators(df, cfg)
        cur    = df_ind.iloc[-2]
        sl_v   = cur['sma_long']
        c      = cur['close']

        if pd.isna(sl_v) or pd.isna(c):
            continue

        is_long = (p.type == mt5.ORDER_TYPE_BUY)

        # ── Force close: SMA_long break (priority) ──
        if (is_long and c < sl_v) or (not is_long and c > sl_v):
            side = 'LONG' if is_long else 'SHORT'
            msg  = (STRATEGY_TAG + ' force-close: ' + p.symbol + ' ' + side +
                    ' SMA' + str(cfg['sma_long']) + ' break  ticket=' + str(p.ticket))
            log_print(msg)
            send_discord(msg, webhook)
            _close_position(p, is_long, STRATEGY_TAG + '_CLOSE')
            continue

        # ── A-1: SMA_long slope reversal exit ──
        slope_exit = cfg.get('slope_exit', None)
        if slope_exit is not None:
            slope_now = calc_slope(df_ind['sma_long'], slope_exit)
            reversed_ = (is_long and slope_now is False) or (not is_long and slope_now is True)
            if reversed_:
                side = 'LONG' if is_long else 'SHORT'
                msg  = (STRATEGY_TAG + ' slope-exit: ' + p.symbol + ' ' + side +
                        '  SMA' + str(cfg['sma_long']) + ' slope reversed' +
                        '  ticket=' + str(p.ticket))
                log_print(msg)
                send_discord(msg, webhook)
                _close_position(p, is_long, STRATEGY_TAG + '_SLOPE_EXIT')
                continue

        # ── B-1: Breakeven SL move ──
        be_r = cfg.get('be_r', None)
        if be_r is not None:
            _check_breakeven(p, is_long, be_r, webhook)


# ══════════════════════════════════════════
# Order placement
# ══════════════════════════════════════════
def place_order(symbol, base_sym, direction, sl_pips, tp_pips, broker, cfg):
    """
    Place market order with fixed SL/TP.
    sl_pips / tp_pips are price distances (ATR-based, not literal pips).
    Returns True on success.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        log_print('symbol_info failed: ' + symbol)
        return False

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log_print('symbol_info_tick failed: ' + symbol)
        return False

    if direction == 'long':
        order_type = mt5.ORDER_TYPE_BUY
        entry      = tick.ask
        tp         = round(entry + tp_pips, info.digits)
        sl         = round(entry - sl_pips, info.digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        entry      = tick.bid
        tp         = round(entry - tp_pips, info.digits)
        sl         = round(entry + sl_pips, info.digits)

    balance = rm.get_balance()
    lot     = rm.calc_lot(balance, sl_pips, symbol)

    if is_live_broker(broker):
        log_print('*** LIVE ORDER *** ' + symbol + ' ' + direction.upper() +
                  ' lot=' + str(lot) + ' broker=' + broker)

    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        entry,
        'tp':           tp,
        'sl':           sl,
        'deviation':    10,
        'magic':        MAGIC,
        'comment':      STRATEGY_TAG + '_' + base_sym,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        log_print('order failed: ' + symbol + ' ' + direction.upper() +
                  ' code=' + str(code))
        return False

    _last_entry[base_sym] = datetime.now()
    msg = (STRATEGY_TAG + ' entry: ' + symbol + ' ' + direction.upper() +
           ' lot=' + str(lot) +
           ' entry=' + str(round(entry, info.digits)) +
           ' sl=' + str(sl) + ' tp=' + str(tp))
    log_print(msg)
    return True


# ══════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════
def main_loop(webhook):
    log_print('sma_squeeze started  broker=' + BROKER_KEY +
              '  interval=' + str(LOOP_INTERVAL) + 's')

    while True:
        try:
            manage_positions(BROKER_KEY, webhook)

            total_pos = count_total_strategy()
            jpy_lots  = count_jpy_lots()

            for base_sym, cfg in PAIRS_CFG.items():
                if not cfg.get('enabled', True):
                    continue
                if total_pos >= MAX_TOTAL_POS:
                    log_print('MAX_TOTAL_POS reached', debug=True)
                    break

                symbol = _rsym(base_sym)
                is_jpy = 'JPY' in base_sym

                if is_jpy and jpy_lots >= MAX_JPY_LOT:
                    log_print(base_sym + ': JPY lot limit', debug=True)
                    continue
                if has_open_position(symbol):
                    log_print(base_sym + ': position open', debug=True)
                    continue
                if is_in_cooldown(base_sym):
                    continue

                tf  = cfg.get('timeframe', '1h')
                n   = cfg['sma_long'] + cfg['slope_period'] + 20
                df  = get_ohlcv(symbol, tf, n, BROKER_KEY)
                if df is None:
                    log_print(base_sym + ': OHLCV fetch failed', debug=True)
                    continue

                df_ind    = calc_indicators(df, cfg)
                direction = check_entry(df_ind, cfg, base_sym)
                if direction is None:
                    continue

                cur     = df_ind.iloc[-2]
                atr_v   = cur['atr14']
                sl_dist = atr_v * cfg['sl_atr_mult']
                tp_dist = sl_dist * cfg['rr']

                if pd.isna(sl_dist) or sl_dist <= 0:
                    continue

                if place_order(symbol, base_sym, direction, sl_dist, tp_dist, BROKER_KEY, cfg):
                    total_pos += 1
                    if is_jpy:
                        jpy_lots += rm.calc_lot(rm.get_balance(), sl_dist, symbol)

            log_print('cycle done  pos=' + str(count_total_strategy()) +
                      '/' + str(MAX_TOTAL_POS), debug=True)

        except Exception as e:
            log_print('loop error: ' + str(e))

        time.sleep(LOOP_INTERVAL)


# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main():
    global BROKER_KEY, LOG_FILE, DEBUG

    parser = argparse.ArgumentParser(description='SMA Squeeze Play monitor v2')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['oanda', 'oanda_demo', 'axiory', 'exness'],
                        help='broker key')
    parser.add_argument('--debug', action='store_true', help='enable debug logging')
    args = parser.parse_args()

    BROKER_KEY = args.broker
    if args.debug:
        DEBUG = True

    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'sma_squeeze_log_' + BROKER_KEY + '.txt')

    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    if not connect_mt5(BROKER_KEY):
        log_print('MT5 init failed  broker=' + BROKER_KEY)
        return

    try:
        account = mt5.account_info()
        if account is None:
            log_print('account_info failed')
            disconnect_mt5()
            return
        log_print('connected  broker=' + BROKER_KEY + '  login=' + str(account.login))
    except Exception as e:
        log_print('MT5 error: ' + str(e))
        disconnect_mt5()
        return

    _SYMBOL_MAP.update(build_symbol_map(list(PAIRS_CFG.keys()), BROKER_KEY))

    try:
        main_loop(webhook)
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
