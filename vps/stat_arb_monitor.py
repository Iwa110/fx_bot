# stat_arb_monitor.py
# Statistical Arbitrage Monitor - Pairs Trading Strategy
# magic = 20260001
# v2: マルチブローカー対応: broker_utils / argparse --broker 追加

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import time
import json
import heartbeat_check as hb
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ─── BROKER SETTINGS ──────────────────────────────────────────────────────────

BROKER_KEY = 'oanda'

# ベースシンボル → MT5シンボル名（main()内で populate）
_SYMBOL_MAP: dict[str, str] = {}

def _rsym(base: str) -> str:
    """ベースシンボルをブローカー固有のMT5シンボル名に変換する"""
    return _SYMBOL_MAP.get(base, base)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

MAGIC = 20260001
TIMEFRAME = mt5.TIMEFRAME_H1
COMMENT = 'stat_arb'

# Pairs config: (symbol_a, symbol_b, enabled)  ← ベース名で定義
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
MAX_NON_JPY_LOT = 1.0

# Risk / position limits
MAX_TOTAL_POS = 2   # stat_arb固有ペア数上限（他戦略のポジションを除外）
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
    return 'JPY' in symbol


def get_total_positions():
    positions = mt5.positions_get()
    if not positions:
        return 0
    return len([p for p in positions if p.magic == MAGIC])


def get_pair_positions(symbol_a, symbol_b):
    """symbol_a/symbol_b はブローカー固有名（_rsym 適用済み）で渡す"""
    pos_a = mt5.positions_get(symbol=symbol_a)
    pos_b = mt5.positions_get(symbol=symbol_b)
    magic_a = [p for p in pos_a if p.magic == MAGIC] if pos_a else []
    magic_b = [p for p in pos_b if p.magic == MAGIC] if pos_b else []
    return magic_a, magic_b

# ─── DATA FETCH ───────────────────────────────────────────────────────────────

def fetch_closes(symbol, n):
    """symbol はブローカー固有名（_rsym 適用済み）で渡す"""
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
    symbol_a/symbol_b はブローカー固有名（_rsym 適用済み）で渡す。
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
    """symbol はブローカー固有名（_rsym 適用済み）で渡す"""
    info = mt5.symbol_info(symbol)
    if info is None:
        log(f'[ORDER] symbol_info failed: {symbol}')
        return None

    if is_live_broker(BROKER_KEY):
        direction_str = 'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'
        log(f'[ORDER] *** ライブ口座発注 *** {symbol} {direction_str} lot={lot} broker={BROKER_KEY}')

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
    symbol = pos.symbol   # MT5から返るブローカー固有名
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
    symbol_a/symbol_b はブローカー固有名（_rsym 適用済み）で渡す。
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

    for attempt in range(1, RETRY_COUNT + 1):
        res_b = send_order(symbol_b, type_b, lot_b, COMMENT)
        if res_b is not None:
            log(f'[ENTRY] Leg-B OK attempt={attempt}')
            return True
        log(f'[ENTRY] Leg-B attempt {attempt}/{RETRY_COUNT} failed, wait {RETRY_WAIT}s')
        time.sleep(RETRY_WAIT)

    log(f'[ENTRY] Leg-B all retries failed. Force closing Leg-A')
    entry_ts = time.time()
    pos_a_all = mt5.positions_get(symbol=symbol_a)
    if pos_a_all:
        for p in pos_a_all:
            if p.magic == MAGIC and (entry_ts - p.time) <= 30:
                close_position(p)
    return False

# ─── EXIT LOGIC ───────────────────────────────────────────────────────────────

def try_exit_pair(symbol_a, symbol_b, pos_a_list, pos_b_list, reason=''):
    log(f'[EXIT] {symbol_a}/{symbol_b} reason={reason}')
    for p in pos_a_list:
        close_position(p)
    for p in pos_b_list:
        close_position(p)

# ─── COINTEGRATION STATE ──────────────────────────────────────────────────────

coint_ok: dict[str, bool] = {}
coint_checked_month: dict[str, int] = {}


def ensure_coint(symbol_a, symbol_b):
    """symbol_a/symbol_b はブローカー固有名（_rsym 適用済み）で渡す"""
    pair_key = f'{symbol_a}_{symbol_b}'
    now_month = datetime.now(timezone.utc).month
    if coint_ok.get(pair_key) is None or coint_checked_month.get(pair_key) != now_month:
        ok, pval = check_cointegration(symbol_a, symbol_b)
        coint_ok[pair_key] = ok
        coint_checked_month[pair_key] = now_month
        if not ok:
            log(f'[COINT] {pair_key} NG (p={pval:.4f}). Skipping entries this month.')
    return coint_ok[pair_key]

# ─── COOLDOWN TRACKING ────────────────────────────────────────────────────────

COOLDOWN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stat_arb_cooldown.json')

last_entry_time: dict[str, float] = {}


def _load_cooldown():
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            data = json.load(f)
        last_entry_time.update({k: float(v) for k, v in data.items()})
        log(f'[COOLDOWN] Loaded from {COOLDOWN_FILE}: {list(last_entry_time.keys())}')
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f'[COOLDOWN] Load error: {e}')


def _save_cooldown():
    try:
        with open(COOLDOWN_FILE, 'w') as f:
            json.dump(last_entry_time, f)
    except Exception as e:
        log(f'[COOLDOWN] Save error: {e}')


def can_enter(pair_key):
    if pair_key not in last_entry_time:
        return True
    elapsed = time.time() - last_entry_time[pair_key]
    return elapsed >= COOLDOWN_SEC


def mark_entry(pair_key):
    last_entry_time[pair_key] = time.time()
    _save_cooldown()

# ─── MAIN LOOP PER PAIR ───────────────────────────────────────────────────────

def process_pair(base_a, base_b):
    """base_a/base_b はベース名。内部で _rsym() を適用してブローカー固有名に変換する"""
    symbol_a = _rsym(base_a)
    symbol_b = _rsym(base_b)
    pair_key = f'{symbol_a}_{symbol_b}'

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

    # ── 片脚状態の検出と強制クローズ ──
    only_a = len(pos_a) > 0 and len(pos_b) == 0
    only_b = len(pos_b) > 0 and len(pos_a) == 0
    if only_a or only_b:
        log(f'[{pair_key}] WARNING: one-leg detected (pos_a={len(pos_a)} pos_b={len(pos_b)}). Force closing.')
        for p in pos_a:
            close_position(p)
        for p in pos_b:
            close_position(p)
        return

    # ── EXIT CHECK ──
    if has_pos:
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
    if not ensure_coint(symbol_a, symbol_b):
        return

    if not can_enter(pair_key):
        return

    total_pos = get_total_positions()
    if total_pos >= MAX_TOTAL_POS:
        log(f'[{pair_key}] MAX_TOTAL_POS reached ({total_pos})')
        return

    direction = None
    if z >= ENTRY_Z:
        direction = 1
    elif z <= -ENTRY_Z:
        direction = -1

    if direction is None:
        return

    raw_lot_b = abs(b) * LOT_A
    lot_b = round_lot(raw_lot_b, LOT_STEP, MAX_JPY_LOT if is_jpy_pair(symbol_b) else MAX_NON_JPY_LOT)

    success = try_entry(symbol_a, symbol_b, direction, LOT_A, lot_b)
    if success:
        mark_entry(pair_key)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    global BROKER_KEY

    parser = argparse.ArgumentParser(description='Statistical Arbitrage Monitor')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['oanda', 'oanda_demo', 'axiory', 'exness'],
                        help='使用するブローカーキー')
    args = parser.parse_args()
    BROKER_KEY = args.broker

    log(f'=== stat_arb_monitor START broker={BROKER_KEY} ===')
    _load_cooldown()

    if not connect_mt5(BROKER_KEY):
        log(f'MT5 initialize failed broker={BROKER_KEY}')
        return

    log(f'MT5 version: {mt5.version()}')

    # シンボルマップを構築
    all_bases = list({sym for pair in PAIRS for sym in (pair[0], pair[1])})
    _SYMBOL_MAP.update(build_symbol_map(all_bases, BROKER_KEY))

    active_pairs = [(a, b) for a, b, en in PAIRS if en]
    log(f'Pairs: {[(a, b) for a, b in active_pairs]}')
    log(f'Resolved: {[(a, _rsym(a), b, _rsym(b)) for a, b in active_pairs]}')

    while True:
        try:
            for base_a, base_b in active_pairs:
                process_pair(base_a, base_b)
            hb.record_heartbeat('stat_arb_monitor')
        except Exception as e:
            log(f'[ERROR] {e}')

        time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
    main()
