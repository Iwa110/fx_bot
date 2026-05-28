"""
grid_monitor.py - Multi-pair Grid Strategy monitor v3
Bi-directional grid: Long and Short run concurrently.

v3 changes:
  - Per-pair LOT sizing (LOT_PER_PAIR) replacing single global LOT=0.01
  - Per-pair DD limits (DD_DAY_PER_PAIR / DD_WEEK_PER_PAIR) scaled to lot size
  - GBPJPY: 0.02 lot (B48 worst-case both-dir ~42k JPY, BT MaxDD ~53k JPY)
  - CHFJPY: 0.02 lot (OOS_DD ~25.5k JPY, B48 worst-case ~45k JPY)
  - NZDUSD: 0.01 lot (not running, preserve original)

Supported pairs and magic numbers:
  NZDUSD: magic=20260030, tag=GRID_NZD, atr_mult=2.0
  GBPJPY: magic=20260031, tag=GRID_GBP, atr_mult=1.5
  CHFJPY: magic=20260032, tag=GRID_CHF, atr_mult=2.0

Strategy:
  tf: H1
  grid_width = ATR(H1, 14) x atr_mult (per pair)
  max_levels = 7 per direction
  TP = entry +/- grid_width (1 step), SL = none

Entry filter (range market required):
  Choppiness Index(D1, 14) > 61.8
  CI = 100 * log10(SUM_TR14 / (High14_max - Low14_min)) / log10(14)
  D1 data resampled from H1 bars.

Grid logic:
  Long:  no pos -> enter; count < max_levels and price <= min_entry - grid_width -> add
  Short: no pos -> enter; count < max_levels and price >= max_entry + grid_width -> add
  TP per position = entry +/- grid_width.

Exit B48:
  When max_levels reached, start 48h timer per direction.
  TP fires -> count drops below max_levels -> timer reset.
  Timer expires -> close all positions in that direction at market.

Stop conditions (new orders only; existing TP continues):
  CI <= 61.8
  Daily realized PnL < -5,000 JPY
  Weekly realized PnL < -15,000 JPY

JPY pairs (GBPJPY, CHFJPY):
  MT5 profit is returned in account currency (JPY) directly - no conversion needed.

Brokers: axiory / exness (oanda: disabled)
State:   vps/grid_monitor_state_{PAIR}.json  (per-pair)
Log:     vps/grid_log_{PAIR}_{broker}.txt
"""

import sys
import os
import time
import argparse
import json
import math
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# Pair configuration
# ══════════════════════════════════════════
PAIR_CONFIG = {
    'NZDUSD': {'magic': 20260030, 'tag': 'GRID_NZD', 'atr_mult': 2.0, 'max_levels': 7},
    'GBPJPY': {'magic': 20260031, 'tag': 'GRID_GBP', 'atr_mult': 1.5, 'max_levels': 7},
    'CHFJPY': {'magic': 20260032, 'tag': 'GRID_CHF', 'atr_mult': 2.0, 'max_levels': 7},
}

# ══════════════════════════════════════════
# Per-pair lot sizing
# ══════════════════════════════════════════
# GBPJPY 0.02: BT full_DD~53k JPY, B48 worst-case(both dir)~42k JPY
# CHFJPY 0.02: OOS_DD~25.5k JPY,   B48 worst-case(both dir)~45k JPY
LOT_PER_PAIR = {
    'GBPJPY': 0.02,
    'CHFJPY': 0.02,
    'NZDUSD': 0.01,
}

# Per-pair daily/weekly DD limits scaled to lot size (vs original 0.01 lot baseline)
DD_DAY_PER_PAIR = {
    'GBPJPY': -10000.0,
    'CHFJPY': -10000.0,
    'NZDUSD':  -5000.0,
}
DD_WEEK_PER_PAIR = {
    'GBPJPY': -30000.0,
    'CHFJPY': -30000.0,
    'NZDUSD': -15000.0,
}

# ══════════════════════════════════════════
# Common constants
# ══════════════════════════════════════════
LOT            = 0.01    # overridden per-pair in main()
ATR_PERIOD     = 14
CI_THRESHOLD   = 61.8
CI_PERIOD      = 14
B48_HOURS      = 48
DD_DAY_JPY     = -5000.0   # overridden per-pair in main()
DD_WEEK_JPY    = -15000.0  # overridden per-pair in main()
LOOP_INTERVAL  = 60
HB_CYCLES      = 30   # heartbeat every 30 cycles (~30 min)

_DEAL_REASON_TP = getattr(mt5, 'DEAL_REASON_TP', 5)

# ══════════════════════════════════════════
# Runtime globals (set in main() from --pair / --broker)
# ══════════════════════════════════════════
MAGIC        = 20260030
STRATEGY_TAG = 'GRID_NZD'
SYMBOL       = 'NZDUSD'
ATR_MULT     = 2.0
MAX_LEVELS   = 7
BROKER_KEY   = 'axiory'
_SYMBOL_MAP: dict = {}

def _rsym() -> str:
    return _SYMBOL_MAP.get(SYMBOL, SYMBOL)

# ══════════════════════════════════════════
# Logging
# ══════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(_BASE_DIR, 'grid_log.txt')

def log(msg: str) -> None:
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = ts + '  ' + STRATEGY_TAG + '  ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

# ══════════════════════════════════════════
# State persistence (per-pair)
# ══════════════════════════════════════════
_STATE_FILE = os.path.join(_BASE_DIR, 'grid_monitor_state.json')

_STATE_DEFAULTS = {
    'max_lv_reached_ts_long':  None,
    'max_lv_reached_ts_short': None,
    'day_realized_jpy':        0.0,
    'week_realized_jpy':       0.0,
    'current_day':             '',
    'current_week':            '',
}

def load_state() -> dict:
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        for k, v in _STATE_DEFAULTS.items():
            if k not in s:
                s[k] = v
        return s
    except Exception:
        return dict(_STATE_DEFAULTS)

def save_state(s: dict) -> None:
    try:
        with open(_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log('state_save_error ' + str(e))

# ══════════════════════════════════════════
# Data fetch
# ══════════════════════════════════════════
def _get_h1(symbol: str, n: int):
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, n)
    if bars is None or len(bars) < 5:
        return None
    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df = df.rename(columns={'time': 'datetime', 'tick_volume': 'volume'})
    return df.sort_values('datetime').reset_index(drop=True)

def _get_d1(symbol: str, n: int):
    """Get D1 bars via H1 resample (n = number of D1 bars needed)."""
    df_h = _get_h1(symbol, n * 24 + 48)
    if df_h is None:
        return None
    df = df_h.set_index('datetime')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in df.columns:
        agg['volume'] = 'sum'
    df_d = df.resample('1D').agg(agg).dropna(subset=['close'])
    return df_d.reset_index()

# ══════════════════════════════════════════
# Indicators
# ══════════════════════════════════════════
def calc_atr(df: pd.DataFrame, period: int = 14):
    """ATR(period). Returns float or None."""
    if len(df) < period + 2:
        return None
    h  = df['high']
    l  = df['low']
    c  = df['close']
    tr = pd.concat([h - l,
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    v  = tr.rolling(period).mean().iloc[-1]
    return float(v) if not pd.isna(v) else None

def calc_ci(df_d1: pd.DataFrame, period: int = 14):
    """
    Choppiness Index.
    CI = 100 * log10(SUM_TR(period) / (max_high - min_low)) / log10(period)
    Returns float or None.
    """
    if len(df_d1) < period + 2:
        return None
    h  = df_d1['high']
    l  = df_d1['low']
    c  = df_d1['close']
    tr = pd.concat([h - l,
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    # Last `period` bars (skip NaN from shift at index 0)
    tr_tail  = tr.iloc[-period:].dropna()
    hi_tail  = h.iloc[-period:]
    lo_tail  = l.iloc[-period:]
    if len(tr_tail) < period:
        return None
    tr_sum   = float(tr_tail.sum())
    hl_range = float(hi_tail.max() - lo_tail.min())
    if hl_range <= 0 or tr_sum <= 0:
        return None
    return 100.0 * math.log10(tr_sum / hl_range) / math.log10(period)

# ══════════════════════════════════════════
# Realized PnL helpers
# ══════════════════════════════════════════
def _realized_jpy_since(from_dt: datetime) -> float:
    """Sum realized PnL (in account currency = JPY) for this pair since from_dt UTC."""
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=timezone.utc)
    sym   = _rsym()
    deals = mt5.history_deals_get(from_dt, datetime.now(timezone.utc))
    if not deals:
        return 0.0
    return sum(d.profit for d in deals
               if d.magic == MAGIC and d.symbol == sym
               and d.entry == mt5.DEAL_ENTRY_OUT)

def _day_start_utc() -> datetime:
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)

def _week_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)

# ══════════════════════════════════════════
# Positions
# ══════════════════════════════════════════
def get_positions():
    """Returns (longs, shorts) position lists for this pair / magic."""
    sym = _rsym()
    pos = mt5.positions_get(symbol=sym)
    if not pos:
        return [], []
    longs  = [p for p in pos if p.magic == MAGIC and p.type == mt5.ORDER_TYPE_BUY]
    shorts = [p for p in pos if p.magic == MAGIC and p.type == mt5.ORDER_TYPE_SELL]
    return longs, shorts

# ══════════════════════════════════════════
# Order management
# ══════════════════════════════════════════
def place_order(direction: str, grid_width: float, level: int) -> bool:
    """Place market order. direction: 'LONG' or 'SHORT'. Returns True on success."""
    sym  = _rsym()
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        log('order_failed ' + direction + ' symbol_info=None')
        return False
    digits = info.digits

    if direction == 'LONG':
        order_type = mt5.ORDER_TYPE_BUY
        entry      = tick.ask
        tp         = round(entry + grid_width, digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        entry      = tick.bid
        tp         = round(entry - grid_width, digits)

    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       sym,
        'volume':       LOT,
        'type':         order_type,
        'price':        entry,
        'tp':           tp,
        'sl':           0.0,
        'deviation':    20,
        'magic':        MAGIC,
        'comment':      STRATEGY_TAG,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        log('order_failed ' + direction + ' code=' + str(code))
        return False

    log('entry ' + direction +
        ' lot=' + str(LOT) +
        ' price=' + str(round(entry, digits)) +
        ' grid_width=' + str(round(grid_width, digits)) +
        ' level=' + str(level) + '/' + str(MAX_LEVELS))
    return True

def close_positions(direction: str, positions: list) -> tuple:
    """
    Close all positions in given direction at market.
    Returns (closed_count, total_pnl, closed_ticket_set).
    """
    if not positions:
        return 0, 0.0, set()
    sym     = _rsym()
    closed  = 0
    tot_pnl = 0.0
    tickets = set()
    for p in positions:
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            continue
        is_long    = (p.type == mt5.ORDER_TYPE_BUY)
        close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
        price      = tick.bid if is_long else tick.ask
        req = {
            'action':       mt5.TRADE_ACTION_DEAL,
            'symbol':       sym,
            'volume':       p.volume,
            'type':         close_type,
            'price':        price,
            'deviation':    20,
            'magic':        MAGIC,
            'comment':      STRATEGY_TAG + '_B48',
            'position':     p.ticket,
            'type_time':    mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            tot_pnl += p.profit
            tickets.add(p.ticket)
            closed  += 1
        else:
            code = result.retcode if result else 'None'
            log('close_failed ' + direction +
                ' ticket=' + str(p.ticket) + ' code=' + str(code))
    return closed, tot_pnl, tickets

# ══════════════════════════════════════════
# TP close detection
# ══════════════════════════════════════════
def check_tp_closes(from_dt: datetime, b48_tickets: set) -> None:
    """Detect and log TP-hit closures since from_dt, skipping B48-closed tickets."""
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=timezone.utc)
    sym   = _rsym()
    deals = mt5.history_deals_get(from_dt, datetime.now(timezone.utc))
    if not deals:
        return
    info   = mt5.symbol_info(sym)
    digits = info.digits if info else 5
    for d in deals:
        if (d.magic != MAGIC or d.symbol != sym
                or d.entry != mt5.DEAL_ENTRY_OUT
                or d.reason != _DEAL_REASON_TP):
            continue
        if d.position_id in b48_tickets:
            continue
        # Closing a Long = SELL deal; closing a Short = BUY deal
        side = 'SHORT' if d.type == mt5.DEAL_TYPE_BUY else 'LONG'
        hold_h = 0
        pos_deals = mt5.history_deals_get(position=d.position_id)
        if pos_deals:
            open_times = [dd.time for dd in pos_deals
                          if dd.entry == mt5.DEAL_ENTRY_IN]
            if open_times:
                hold_h = int((d.time - min(open_times)) / 3600)
        pnl_str = ('+' if d.profit >= 0 else '') + str(round(d.profit))
        log('tp_close ' + side +
            ' price=' + str(round(d.price, digits)) +
            ' pnl=' + pnl_str + ' JPY' +
            ' hold=' + str(hold_h) + 'h')

# ══════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════
def main_loop() -> None:
    log('grid_monitor v3 started  pair=' + SYMBOL +
        '  broker=' + BROKER_KEY +
        '  magic=' + str(MAGIC) +
        '  lot=' + str(LOT) +
        '  atr_mult=' + str(ATR_MULT) +
        '  dd_day=' + str(int(DD_DAY_JPY)) +
        '  dd_week=' + str(int(DD_WEEK_JPY)) +
        '  interval=' + str(LOOP_INTERVAL) + 's')

    state   = load_state()
    _cycle  = 0
    # Look back 5 min on start to catch any TP fires during downtime (short window)
    _last_tp_check      = datetime.now(timezone.utc) - timedelta(minutes=5)
    _last_filter_block  = 0   # cycle number of last filter_block log

    while True:
        try:
            _cycle  += 1
            now_utc  = datetime.now(timezone.utc)
            today_s  = now_utc.strftime('%Y-%m-%d')
            week_s   = now_utc.strftime('%G-W%V')   # ISO week Mon-start

            # ── Day / week reset ──
            if state['current_day'] != today_s:
                state['current_day']      = today_s
                state['day_realized_jpy'] = 0.0
            if state['current_week'] != week_s:
                state['current_week']      = week_s
                state['week_realized_jpy'] = 0.0

            # ── Realized PnL (recomputed from MT5 history for accuracy) ──
            day_pnl  = _realized_jpy_since(_day_start_utc())
            week_pnl = _realized_jpy_since(_week_start_utc())
            state['day_realized_jpy']  = day_pnl
            state['week_realized_jpy'] = week_pnl

            # ── Fetch OHLCV ──
            sym   = _rsym()
            df_h1 = _get_h1(sym, ATR_PERIOD + 10)
            df_d1 = _get_d1(sym, CI_PERIOD + 5)

            if df_h1 is None or df_d1 is None:
                log('loop_error data_fetch_failed h1=' +
                    str(df_h1 is not None) + ' d1=' + str(df_d1 is not None))
                time.sleep(LOOP_INTERVAL)
                continue

            # ── Indicators ──
            atr = calc_atr(df_h1, ATR_PERIOD)
            ci  = calc_ci(df_d1, CI_PERIOD)

            if atr is None or atr <= 0:
                log('loop_error atr_invalid atr=' + str(atr))
                time.sleep(LOOP_INTERVAL)
                continue

            grid_width = atr * ATR_MULT

            # ── Live mid price for grid comparison ──
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                log('loop_error tick_failed sym=' + sym)
                time.sleep(LOOP_INTERVAL)
                continue
            mid_price = (tick.ask + tick.bid) / 2.0

            # ── Get positions ──
            longs, shorts = get_positions()
            long_count    = len(longs)
            short_count   = len(shorts)

            # ── B48 timer: Long ──
            b48_closed_tickets: set = set()

            if long_count >= MAX_LEVELS:
                if state['max_lv_reached_ts_long'] is None:
                    state['max_lv_reached_ts_long'] = now_utc.isoformat()
            else:
                state['max_lv_reached_ts_long'] = None

            if state['max_lv_reached_ts_long'] is not None and long_count > 0:
                ts_l = datetime.fromisoformat(state['max_lv_reached_ts_long'])
                if ts_l.tzinfo is None:
                    ts_l = ts_l.replace(tzinfo=timezone.utc)
                elapsed_h = (now_utc - ts_l).total_seconds() / 3600.0
                if elapsed_h >= B48_HOURS:
                    n_cl, tot, tix = close_positions('LONG', longs)
                    b48_closed_tickets |= tix
                    pnl_s = ('+' if tot >= 0 else '') + str(round(tot))
                    log('b48_close LONG positions=' + str(n_cl) +
                        ' total_pnl=' + pnl_s + ' JPY')
                    state['max_lv_reached_ts_long'] = None
                    longs, _  = get_positions()
                    long_count = len(longs)

            # ── B48 timer: Short ──
            if short_count >= MAX_LEVELS:
                if state['max_lv_reached_ts_short'] is None:
                    state['max_lv_reached_ts_short'] = now_utc.isoformat()
            else:
                state['max_lv_reached_ts_short'] = None

            if state['max_lv_reached_ts_short'] is not None and short_count > 0:
                ts_s = datetime.fromisoformat(state['max_lv_reached_ts_short'])
                if ts_s.tzinfo is None:
                    ts_s = ts_s.replace(tzinfo=timezone.utc)
                elapsed_h = (now_utc - ts_s).total_seconds() / 3600.0
                if elapsed_h >= B48_HOURS:
                    _, shorts_now = get_positions()
                    n_cl, tot, tix = close_positions('SHORT', shorts_now)
                    b48_closed_tickets |= tix
                    pnl_s = ('+' if tot >= 0 else '') + str(round(tot))
                    log('b48_close SHORT positions=' + str(n_cl) +
                        ' total_pnl=' + pnl_s + ' JPY')
                    state['max_lv_reached_ts_short'] = None
                    _, shorts     = get_positions()
                    short_count   = len(shorts)

            # ── TP close detection ──
            check_tp_closes(_last_tp_check, b48_closed_tickets)
            _last_tp_check = now_utc

            # ── Save state ──
            save_state(state)

            # ── Heartbeat ──
            ci_str = str(round(ci, 1)) if ci is not None else 'N/A'
            if _cycle % HB_CYCLES == 0:
                log('heartbeat alive' +
                    ' long_pos='  + str(long_count)  + '/' + str(MAX_LEVELS) +
                    ' short_pos=' + str(short_count) + '/' + str(MAX_LEVELS) +
                    ' ci=' + ci_str)

            # ── Filter check (applies to new orders only) ──
            if ci is None:
                time.sleep(LOOP_INTERVAL)
                continue

            entry_blocked  = False
            block_reasons  = []
            if ci <= CI_THRESHOLD:
                entry_blocked = True
                block_reasons.append('ci=' + ci_str + ' (threshold=' + str(CI_THRESHOLD) + ')')
            if day_pnl < DD_DAY_JPY:
                entry_blocked = True
                block_reasons.append('day_pnl=' + str(round(day_pnl)) + ' JPY')
            if week_pnl < DD_WEEK_JPY:
                entry_blocked = True
                block_reasons.append('week_pnl=' + str(round(week_pnl)) + ' JPY')

            # Refresh positions after B48 closes
            longs, shorts = get_positions()
            long_count    = len(longs)
            short_count   = len(shorts)

            info_sym = mt5.symbol_info(sym)
            digits   = info_sym.digits if info_sym else 5

            # ── Grid logic: Long ──
            long_entry_needed = False
            if long_count == 0:
                long_entry_needed = True
            elif long_count < MAX_LEVELS:
                min_long_entry = min(p.price_open for p in longs)
                if mid_price <= round(min_long_entry - grid_width, digits):
                    long_entry_needed = True

            # ── Grid logic: Short ──
            short_entry_needed = False
            if short_count == 0:
                short_entry_needed = True
            elif short_count < MAX_LEVELS:
                max_short_entry = max(p.price_open for p in shorts)
                if mid_price >= round(max_short_entry + grid_width, digits):
                    short_entry_needed = True

            # ── Log filter_block (throttled to once per HB_CYCLES) ──
            if entry_blocked and (long_entry_needed or short_entry_needed):
                if _cycle - _last_filter_block >= HB_CYCLES:
                    for reason in block_reasons:
                        log('filter_block ' + reason)
                    _last_filter_block = _cycle

            # ── Execute entries ──
            if not entry_blocked:
                if long_entry_needed:
                    place_order('LONG', grid_width, long_count + 1)
                    longs, _ = get_positions()
                    long_count = len(longs)

                if short_entry_needed:
                    place_order('SHORT', grid_width, short_count + 1)

        except Exception as e:
            log('loop_error ' + str(e))

        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main() -> None:
    global MAGIC, STRATEGY_TAG, SYMBOL, ATR_MULT, MAX_LEVELS
    global BROKER_KEY, LOG_FILE, _STATE_FILE
    global LOT, DD_DAY_JPY, DD_WEEK_JPY

    parser = argparse.ArgumentParser(description='Grid Strategy monitor v2 (multi-pair)')
    parser.add_argument('--pair', default='NZDUSD',
                        choices=list(PAIR_CONFIG.keys()),
                        help='trading pair')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['axiory', 'exness', 'oanda', 'oanda_demo'],
                        help='broker key')
    args = parser.parse_args()

    # Apply pair config
    cfg          = PAIR_CONFIG[args.pair]
    SYMBOL       = args.pair
    MAGIC        = cfg['magic']
    STRATEGY_TAG = cfg['tag']
    ATR_MULT     = cfg['atr_mult']
    MAX_LEVELS   = cfg['max_levels']
    BROKER_KEY   = args.broker

    # Per-pair lot and DD limits
    LOT         = LOT_PER_PAIR.get(SYMBOL, 0.01)
    DD_DAY_JPY  = DD_DAY_PER_PAIR.get(SYMBOL,  -5000.0)
    DD_WEEK_JPY = DD_WEEK_PER_PAIR.get(SYMBOL, -15000.0)

    # Per-pair log and state files
    LOG_FILE    = os.path.join(_BASE_DIR,
                               'grid_log_' + SYMBOL + '_' + BROKER_KEY + '.txt')
    _STATE_FILE = os.path.join(_BASE_DIR,
                               'grid_monitor_state_' + SYMBOL + '.json')

    if not connect_mt5(BROKER_KEY):
        log('MT5 init failed  broker=' + BROKER_KEY)
        return

    try:
        account = mt5.account_info()
        if account is None:
            log('account_info failed')
            disconnect_mt5()
            return
        log('connected  broker=' + BROKER_KEY + '  login=' + str(account.login))
    except Exception as e:
        log('MT5 error: ' + str(e))
        disconnect_mt5()
        return

    _SYMBOL_MAP.update(build_symbol_map([SYMBOL], BROKER_KEY))

    try:
        main_loop()
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
