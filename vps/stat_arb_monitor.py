# stat_arb_monitor.py
# Statistical Arbitrage Monitor - Pairs Trading Strategy
# magic = 20260001

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import time

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

MAGIC = 20260001
TIMEFRAME = mt5.TIMEFRAME_H1
COMMENT = 'stat_arb'

# Pairs config: (symbol_a, symbol_b, enabled)
PAIRS = [
    ('GBPJPY', 'USDJPY', True),
    ('EURUSD', 'GBPUSD', True),
    # ('AUDJPY', 'NZDJPY', False),  # reserved
]

# Model parameters
OLS_WINDOW = 500
ZSCORE_WINDOW = 100
ENTRY_Z = 2.0
SL_Z = 3.5
TP_Z = 0.5

# Lot parameters
LOT_A = 0.01
LOT_STEP = 0.01
MAX_JPY_LOT = 0.4

# Risk / position limits
MAX_TOTAL_POS = 13
COOLDOWN_SEC = 15 * 60

# Retry for leg-B fill
RETRY_COUNT = 3
RETRY_WAIT = 5

# Loop interval (seconds)
LOOP_INTERVAL = 10

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')


def round_lot(lot, step=LOT_STEP, max_lot=MAX_JPY_LOT):
    lot = round(round(lot / step) * step, 2)
    lot = max(step, min(lot, max_lot))
    return lot


def is_jpy_pair(symbol):
    return symbol.endswith('JPY')


def get_total_positions():
    positions = mt5.positions_get()
    return len(positions) if positions else 0


def get_pair_positions(symbol_a, symbol_b):
    pos_a = mt5.positions_get(symbol=symbol_a)
    pos_b = mt5.positions_get(symbol=symbol_b)
    magic_a = [p for p in pos_a if p.magic == MAGIC] if pos_a else []
    magic_b = [p for p in pos_b if p.magic == MAGIC] if pos_b else []
    return magic_a, magic_b

# ─── DATA FETCH ───────────────────────────────────────────────────────────────

def fetch_closes(symbol, n):
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, n)
    if rates is None or len(rates) < n:
        return None
    return pd.Series([r['close'] for r in rates])

# ─── ROLLING OLS (beta) ───────────────────────────────────────────────────────

def rolling_ols_beta(y, x, window):
    betas = []
    for i in range(len(y)):
        if i < window - 1:
            betas.append(np.nan)
            continue
        yi = y.iloc[i - window + 1: i + 1].values
        xi = x.iloc[i - window + 1: i + 1].values
        X = np.column_stack([np.ones(window), xi])
        try:
            coef = np.linalg.lstsq(X, yi, rcond=None)[0]
            betas.append(coef[1])
        except Exception:
            betas.append(np.nan)
    return pd.Series(betas, index=y.index)


def compute_spread_zscore(close_a, close_b, ols_window, zscore_window):
    beta = rolling_ols_beta(close_a, close_b, ols_window)
    spread = close_a - beta * close_b
    mean = spread.rolling(zscore_window).mean()
    std = spread.rolling(zscore_window).std()
    zscore = (spread - mean) / std
    return zscore, beta, spread

# ─── COINTEGRATION CHECK (monthly use) ────────────────────────────────────────

def check_cointegration(symbol_a, symbol_b, n_bars=1000):
    '''
    Monthly cointegration check using Engle-Granger ADF test.
    Returns (is_cointegrated: bool, p_value: float)
    Usage: call manually before running the strategy each month.
    '''
    try:
        from statsmodels.tsa.stattools import coint
    except ImportError:
        log('statsmodels not installed. pip install statsmodels')
        return False, 1.0

    ca = fetch_closes(symbol_a, n_bars)
    cb = fetch_closes(symbol_b, n_bars)
    if ca is None or cb is None:
        log(f'[COINT] Failed to fetch data for {symbol_a}/{symbol_b}')
        return False, 1.0

    score, p_value, _ = coint(ca.values, cb.values)
    is_coint = p_value < 0.05
    log(f'[COINT] {symbol_a}/{symbol_b}: p={p_value:.4f} -> {"OK" if is_coint else "NG"}')
    return is_coint, p_value

# ─── ORDER EXECUTION ──────────────────────────────────────────────────────────

def send_order(symbol, order_type, lot, comment=''):
    info = mt5.symbol_info(symbol)
    if info is None:
        log(f'[ORDER] symbol_info failed: {symbol}')
        return None

    price = mt5.symbol_info_tick(symbol).ask if order_type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid
    filling = mt5.ORDER_FILLING_IOC
    req = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': symbol,
        'volume': lot,
        'type': order_type,
        'price': price,
        'deviation': 20,
        'magic': MAGIC,
        'comment': comment,
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': filling,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else 'None'
        log(f'[ORDER] FAILED {symbol} {lot} retcode={retcode}')
        return None
    log(f'[ORDER] OK {symbol} {"BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL"} lot={lot} ticket={result.order}')
    return result


def close_position(pos):
    symbol = pos.symbol
    lot = pos.volume
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = mt5.symbol_info_tick(symbol).bid if order_type == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(symbol).ask
    req = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': symbol,
        'volume': lot,
        'type': order_type,
        'price': price,
        'deviation': 20,
        'magic': MAGIC,
        'comment': 'stat_arb_close',
        'position': pos.ticket,
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else 'None'
        log(f'[CLOSE] FAILED {symbol} ticket={pos.ticket} retcode={retcode}')
        return False
    log(f'[CLOSE] OK {symbol} ticket={pos.ticket}')
    return True

# ─── ENTRY LOGIC ──────────────────────────────────────────────────────────────

def try_entry(symbol_a, symbol_b, direction, lot_a, lot_b):
    '''
    direction: +1 -> short A, long B (z > ENTRY_Z)
               -1 -> long A, short B (z < -ENTRY_Z)
    Leg-A first, then retry Leg-B up to RETRY_COUNT times.
    On Leg-B failure, force close Leg-A.
    '''
    if direction == 1:
        type_a = mt5.ORDER_TYPE_SELL
        type_b = mt5.ORDER_TYPE_BUY
        label = 'SHORT_A'
    else:
        type_a = mt5.ORDER_TYPE_BUY
        type_b = mt5.ORDER_TYPE_SELL
        label = 'LONG_A'

    log(f'[ENTRY] {label} {symbol_a}/{symbol_b} lot_a={lot_a} lot_b={lot_b}')

    res_a = send_order(symbol_a, type_a, lot_a, COMMENT)
    if res_a is None:
        log(f'[ENTRY] Leg-A failed, abort')
        return False

    # Retry Leg-B
    for attempt in range(1, RETRY_COUNT + 1):
        res_b = send_order(symbol_b, type_b, lot_b, COMMENT)
        if res_b is not None:
            log(f'[ENTRY] Leg-B OK attempt={attempt}')
            return True
        log(f'[ENTRY] Leg-B attempt {attempt}/{RETRY_COUNT} failed, wait {RETRY_WAIT}s')
        time.sleep(RETRY_WAIT)

    # Leg-B failed -> force close Leg-A
    log(f'[ENTRY] Leg-B all retries failed. Force closing Leg-A')
    pos_a = mt5.positions_get(symbol=symbol_a)
    if pos_a:
        for p in pos_a:
            if p.magic == MAGIC and p.ticket == res_a.order:
                close_position(p)
    return False

# ─── EXIT LOGIC ───────────────────────────────────────────────────────────────

def try_exit_pair(symbol_a, symbol_b, pos_a_list, pos_b_list, reason=''):
    log(f'[EXIT] {symbol_a}/{symbol_b} reason={reason}')
    for p in pos_a_list:
        close_position(p)
    for p in pos_b_list:
        close_position(p)

# ─── COOLDOWN TRACKING ────────────────────────────────────────────────────────

last_entry_time = {}

def can_enter(pair_key):
    if pair_key not in last_entry_time:
        return True
    elapsed = time.time() - last_entry_time[pair_key]
    return elapsed >= COOLDOWN_SEC


def mark_entry(pair_key):
    last_entry_time[pair_key] = time.time()

# ─── MAIN LOOP PER PAIR ───────────────────────────────────────────────────────

def process_pair(symbol_a, symbol_b):
    pair_key = f'{symbol_a}_{symbol_b}'

    # fetch data
    n_needed = OLS_WINDOW + ZSCORE_WINDOW + 10
    ca = fetch_closes(symbol_a, n_needed)
    cb = fetch_closes(symbol_b, n_needed)
    if ca is None or cb is None:
        log(f'[{pair_key}] Data fetch failed')
        return

    zscore, beta, spread = compute_spread_zscore(ca, cb, OLS_WINDOW, ZSCORE_WINDOW)
    z = zscore.iloc[-1]
    b = beta.iloc[-1]

    if np.isnan(z) or np.isnan(b):
        log(f'[{pair_key}] z or beta is NaN, skip')
        return

    log(f'[{pair_key}] z={z:.3f} beta={b:.4f}')

    pos_a, pos_b = get_pair_positions(symbol_a, symbol_b)
    has_pos = len(pos_a) > 0 or len(pos_b) > 0

    # ── EXIT CHECK ──
    if has_pos:
        # Determine current direction from pos_a side
        direction = 1 if (pos_a and pos_a[0].type == mt5.POSITION_TYPE_SELL) else -1
        exit_reason = None

        if direction == 1 and z <= TP_Z:
            exit_reason = f'TP z={z:.3f}<={TP_Z}'
        elif direction == -1 and z >= -TP_Z:
            exit_reason = f'TP z={z:.3f}>=-{TP_Z}'
        elif direction == 1 and z >= SL_Z:
            exit_reason = f'SL z={z:.3f}>={SL_Z}'
        elif direction == -1 and z <= -SL_Z:
            exit_reason = f'SL z={z:.3f}<=-{SL_Z}'

        if exit_reason:
            try_exit_pair(symbol_a, symbol_b, pos_a, pos_b, exit_reason)
        return

    # ── ENTRY CHECK ──
    if not can_enter(pair_key):
        return

    total_pos = get_total_positions()
    if total_pos >= MAX_TOTAL_POS:
        log(f'[{pair_key}] MAX_TOTAL_POS reached ({total_pos})')
        return

    direction = None
    if z >= ENTRY_Z:
        direction = 1   # spread too high -> short A, long B
    elif z <= -ENTRY_Z:
        direction = -1  # spread too low -> long A, short B

    if direction is None:
        return

    # calculate lot_b from beta
    raw_lot_b = abs(b) * LOT_A
    lot_b = round_lot(raw_lot_b, LOT_STEP, MAX_JPY_LOT if is_jpy_pair(symbol_b) else 100.0)

    success = try_entry(symbol_a, symbol_b, direction, LOT_A, lot_b)
    if success:
        mark_entry(pair_key)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    log('=== stat_arb_monitor START ===')

    if not mt5.initialize():
        log(f'MT5 initialize failed: {mt5.last_error()}')
        return

    log(f'MT5 version: {mt5.version()}')
    log(f'Pairs: {[(a, b) for a, b, en in PAIRS if en]}')

    active_pairs = [(a, b) for a, b, en in PAIRS if en]

    while True:
        try:
            for symbol_a, symbol_b in active_pairs:
                process_pair(symbol_a, symbol_b)
        except Exception as e:
            log(f'[ERROR] {e}')

        time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
    main()