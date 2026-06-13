"""
grid_monitor.py - Multi-pair Grid Strategy monitor v8
Bi-directional grid: Long and Short run concurrently (per dir_mode).

v8 changes (2026-06-13; backtests under optimizer/grid_dd_reduction_bt.py,
grid_dirbias_improve_bt.py, grid_event_trend_gate_bt.py, grid_exit_lot_bt.py,
grid_toolkit_allpairs_bt.py, Step B = grid_stepb_recompute.py):
  Adds the BT-validated improvement toolkit so every pair that cleared the
  PF-expectation bar (IS-selectable & OOS>1.2 & wfoMin>1.0) can be forward-tested
  with its recommended config. New per-pair knobs (all default OFF = legacy v7):
    - mom_thr      : 24h ATR-normalized return gate. Block new/added entries on a
                     direction whose 24h move (t-1) is adverse beyond thr.
                     long blocked if ret24<=-thr ; short blocked if ret24>=+thr.
    - mom120_thr   : same gate on a 120h (5-day) horizon (EURGBP).
    - cull_frac    : worst-leg cull. When a direction's basket unrealized loss
                     <= cull_frac*float_stop, close the single worst leg (staged
                     de-risk before a full float-stop fires).
    - taper        : lot taper. Level-k lot = base * taper^(k-1) (de-weight deep
                     adverse adds = the diagnosed "knife-catching" loss).
    - dir_mode     : 'both' | 'long_only' | 'regime_short'.
                     long_only  = never open shorts (carry-grid: USDJPY/NZDJPY).
                     regime_short = block NEW shorts while close>SMA(sma_period)
                       (block counter-trend shorts in up-regimes; AUDCAD/AUDNZD).
    - sma_period   : H1 SMA window for regime_short (1200 = ~50d).
    - short_lot_mult: soft directional tilt; short base lot * mult (EURGBP=0.5).
    - tp_mult      : per-leg TP distance = grid_width * tp_mult (EURGBP=0.8).

  Forward-test set (recommended configs, demo lot=1.0; real-money lot=equity/req_cap_99):
    AUDCAD 20260034 R-SMA1200+combo  : atr1.5/lv5/ci65/fs-750k  dir=regime_short(1200)
                                       mom2.0/cull0.5/taper0.7  (Step B req_cap_99=734k)
    EURGBP 20260035 combo+slot0.5    : atr1.5/lv5/ci65/fs-1.32M dir=both short_lot0.5
                                       mom2.0/cull0.5/taper0.7 + mom120=4 + tp0.8 (req~4.2M)
    AUDNZD 20260036 R-SMA1200+combo  : atr1.5/lv5/ci65/fs-625k  dir=regime_short(1200)
                                       mom2.0/cull0.5/taper0.7  (req~1.41M, marginal)
    USDJPY 20260037 long-only+combo  : atr1.5/lv5/ci65/fs-876k  dir=long_only
                                       mom2.0/cull0.5/taper0.7  (carry; SCALE-BANNED)
    NZDJPY 20260033 long-only+combo  : atr1.5/lv7/ci61.8/fs-1.0M dir=long_only
                                       mom2.0/cull0.5/taper0.7  (carry; SCALE-BANNED)
  Legacy / No-Go (kept for magic preservation, NOT forward-tested):
    GBPJPY 20260031, CHFJPY 20260032, NZDUSD 20260030  (features OFF = pure v7).

v7 changes (float_stop joint optimization 2026-06-02):
  - float_stop only a meaningful lever for DEEP ladders; keep loose for shallow.
  - NZDJPY fs-500k->-1.0M revives lv7. AUDCAD ci65/atr1.0/lv5/fs-750k.

(earlier v3-v6 history: see git log; per-pair lot/DD/float-stop, ci_threshold,
 shallow-ladder B48 fix, float stop, DD realized force-close, NZDJPY/AUDCAD add.)

Strategy:
  tf: H1 ; grid_width = ATR(H1,14) * atr_mult ; max_levels per pair ; SL none.
  Entry filter: Choppiness Index(D1,14) > ci_threshold (range market required)
    + (v8) momentum gate / directional mode / regime gate.
  Grid: no pos -> enter ; count<max & price beyond min/max +/- gw -> add.
  TP per leg = entry +/- grid_width*tp_mult.
  Exits: TP ; float-stop (basket unrealized < float_stop) ; B48 timer ;
         (v8) worst-leg cull ; DD realized force-close (day/week breaker).

JPY profit: MT5 returns profit in account currency (JPY) directly.
Brokers: axiory / exness (oanda disabled).
State: grid_monitor_state_{PAIR}.json   Log: grid_log_{PAIR}_{broker}.txt
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
# Per-pair v8 knobs. Defaults (legacy/No-Go pairs): dir_mode='both', everything
# else None/1.0 -> identical to v7 behaviour. Forward-test pairs carry the
# BT-recommended toolkit settings.
#   dir_mode      : 'both' | 'long_only' | 'regime_short'
#   sma_period    : SMA window (H1 bars) for regime_short (else None)
#   mom_thr       : 24h ATR-norm return gate threshold (else None)
#   mom120_thr    : 120h ATR-norm return gate threshold (else None)
#   cull_frac     : worst-leg cull trigger as fraction of float_stop (else None)
#   taper         : lot taper ratio per level (else None = flat lot)
#   short_lot_mult: short base-lot multiplier (default 1.0)
#   tp_mult       : per-leg TP distance multiplier (default 1.0)
PAIR_CONFIG = {
    # ── Legacy / No-Go (kept for magic preservation; v7 behaviour, features OFF) ──
    'NZDUSD': {'magic': 20260030, 'tag': 'GRID_NZD', 'atr_mult': 2.0, 'max_levels': 7, 'ci_threshold': 61.8,
               'dir_mode': 'both', 'sma_period': None, 'mom_thr': None, 'mom120_thr': None,
               'cull_frac': None, 'taper': None, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
    'GBPJPY': {'magic': 20260031, 'tag': 'GRID_GBP', 'atr_mult': 1.5, 'max_levels': 7, 'ci_threshold': 61.8,
               'dir_mode': 'both', 'sma_period': None, 'mom_thr': None, 'mom120_thr': None,
               'cull_frac': None, 'taper': None, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
    'CHFJPY': {'magic': 20260032, 'tag': 'GRID_CHF', 'atr_mult': 1.0, 'max_levels': 3, 'ci_threshold': 65.0,
               'dir_mode': 'both', 'sma_period': None, 'mom_thr': None, 'mom120_thr': None,
               'cull_frac': None, 'taper': None, 'short_lot_mult': 1.0, 'tp_mult': 1.0},

    # ── Forward-test set (2026-06-13 recommended configs) ──
    # AUDCAD: R-SMA1200 + combo. atr1.0->1.5 (atr opt), lv5, ci65, fs-750k.
    'AUDCAD': {'magic': 20260034, 'tag': 'GRID_AUC', 'atr_mult': 1.5, 'max_levels': 5, 'ci_threshold': 65.0,
               'dir_mode': 'regime_short', 'sma_period': 1200, 'mom_thr': 2.0, 'mom120_thr': None,
               'cull_frac': 0.5, 'taper': 0.7, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
    # NZDJPY: long-only carry-grid + combo. v7 base atr1.5/lv7/ci61.8/fs-1.0M.
    'NZDJPY': {'magic': 20260033, 'tag': 'GRID_NZJ', 'atr_mult': 1.5, 'max_levels': 7, 'ci_threshold': 61.8,
               'dir_mode': 'long_only', 'sma_period': None, 'mom_thr': 2.0, 'mom120_thr': None,
               'cull_frac': 0.5, 'taper': 0.7, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
    # EURGBP: combo + soft short_lot0.5 + mom120=4 + tp0.8. atr1.5/lv5/ci65/fs-1.32M.
    'EURGBP': {'magic': 20260035, 'tag': 'GRID_EUG', 'atr_mult': 1.5, 'max_levels': 5, 'ci_threshold': 65.0,
               'dir_mode': 'both', 'sma_period': None, 'mom_thr': 2.0, 'mom120_thr': 4.0,
               'cull_frac': 0.5, 'taper': 0.7, 'short_lot_mult': 0.5, 'tp_mult': 0.8},
    # AUDNZD: R-SMA1200 + combo (correlated cross, marginal). atr1.5/lv5/ci65/fs-625k.
    'AUDNZD': {'magic': 20260036, 'tag': 'GRID_AUN', 'atr_mult': 1.5, 'max_levels': 5, 'ci_threshold': 65.0,
               'dir_mode': 'regime_short', 'sma_period': 1200, 'mom_thr': 2.0, 'mom120_thr': None,
               'cull_frac': 0.5, 'taper': 0.7, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
    # USDJPY: long-only carry-grid + combo. atr1.5/lv5/ci65/fs-876k.
    'USDJPY': {'magic': 20260037, 'tag': 'GRID_USJ', 'atr_mult': 1.5, 'max_levels': 5, 'ci_threshold': 65.0,
               'dir_mode': 'long_only', 'sma_period': None, 'mom_thr': 2.0, 'mom120_thr': None,
               'cull_frac': 0.5, 'taper': 0.7, 'short_lot_mult': 1.0, 'tp_mult': 1.0},
}

# ══════════════════════════════════════════
# Per-pair lot sizing (demo forward-test = 1.00; matches BT lot=1.0 so live is
# directly comparable. Real-money lot = account_equity / req_cap_99 [Step B]).
# ══════════════════════════════════════════
LOT_PER_PAIR = {
    'GBPJPY': 1.00, 'CHFJPY': 1.00, 'NZDUSD': 0.01,
    'NZDJPY': 1.00, 'AUDCAD': 1.00, 'EURGBP': 1.00, 'AUDNZD': 1.00, 'USDJPY': 1.00,
}

# Per-pair daily/weekly DD limits (coarse circuit breaker). Set loose enough not
# to pre-empt the BT-validated float_stop / cull / B48 exits (v7 lesson).
DD_DAY_PER_PAIR = {
    'GBPJPY':  -500000.0, 'CHFJPY':  -500000.0, 'NZDUSD':    -5000.0,
    'NZDJPY': -1000000.0, 'AUDCAD':  -750000.0, 'EURGBP': -1320000.0,
    'AUDNZD':  -625000.0, 'USDJPY':  -876000.0,
}
DD_WEEK_PER_PAIR = {
    'GBPJPY': -1500000.0, 'CHFJPY': -1500000.0, 'NZDUSD':   -15000.0,
    'NZDJPY': -2000000.0, 'AUDCAD': -1500000.0, 'EURGBP': -2640000.0,
    'AUDNZD': -1250000.0, 'USDJPY': -1750000.0,
}

# Per-pair float stop: basket unrealized loss per direction -> immediate close.
# JPY values (MT5 profit is account-currency=JPY). Non-JPY-quote pairs use the
# BT price-distance equivalent scaled by quote->JPY (EURGBP qj~190, AUDNZD qj~90).
FLOAT_STOP_PER_PAIR = {
    'GBPJPY': -1_500_000.0, 'CHFJPY': -1_500_000.0, 'NZDUSD':    -15_000.0,
    'NZDJPY': -1_000_000.0, 'AUDCAD':   -750_000.0, 'EURGBP': -1_320_000.0,
    'AUDNZD':   -625_000.0, 'USDJPY':   -876_000.0,
}

# ══════════════════════════════════════════
# Common constants
# ══════════════════════════════════════════
LOT             = 0.01       # overridden per-pair in main()
ATR_PERIOD      = 14
CI_THRESHOLD    = 61.8
CI_PERIOD       = 14
B48_HOURS       = 48
DD_DAY_JPY      = -5000.0    # overridden per-pair in main()
DD_WEEK_JPY     = -15000.0   # overridden per-pair in main()
FLOAT_STOP_JPY  = -15000.0   # overridden per-pair in main()
LOOP_INTERVAL   = 60
HB_CYCLES       = 30   # heartbeat every 30 cycles (~30 min)
MOM_WINDOW      = 24   # hours for 24h momentum gate
MOM120_WINDOW   = 120  # hours for long-term momentum gate

_DEAL_REASON_TP = getattr(mt5, 'DEAL_REASON_TP', 5)

# ══════════════════════════════════════════
# Runtime globals (set in main() from --pair / --broker)
# ══════════════════════════════════════════
MAGIC          = 20260030
STRATEGY_TAG   = 'GRID_NZD'
SYMBOL         = 'NZDUSD'
ATR_MULT       = 2.0
MAX_LEVELS     = 7
CI_TH          = CI_THRESHOLD
BROKER_KEY     = 'axiory'
CLOSE_ONLY     = False   # --close-only: manage exits only, never open new entries (drain mode)
# v8 feature globals
DIR_MODE       = 'both'
SMA_PERIOD     = None
MOM_THR        = None
MOM120_THR     = None
CULL_FRAC      = None
TAPER          = None
SHORT_LOT_MULT = 1.0
TP_MULT        = 1.0
H1_BARS        = ATR_PERIOD + 10   # bars to fetch; raised in main() if regime/mom120
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

def calc_ret_norm(df_h1: pd.DataFrame, window: int, atr: float):
    """ATR-normalized return over `window` hours, using last CLOSED bar (t-1).
    df index -1 is the forming bar -> reference iloc[-2]. Returns float or None."""
    if atr is None or atr <= 0 or len(df_h1) < window + 3:
        return None
    c = df_h1['close']
    return float((c.iloc[-2] - c.iloc[-2 - window]) / atr)

def calc_sma_closed(df_h1: pd.DataFrame, period: int):
    """SMA of close over last `period` CLOSED bars (excludes forming bar -1)."""
    if len(df_h1) < period + 1:
        return None
    return float(df_h1['close'].iloc[-(period + 1):-1].mean())

# ══════════════════════════════════════════
# Lot sizing (taper / direction tilt / broker rounding)
# ══════════════════════════════════════════
def _round_lot(symbol: str, vol: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return round(vol, 2)
    step = getattr(info, 'volume_step', 0.01) or 0.01
    vmin = getattr(info, 'volume_min', step) or step
    vmax = getattr(info, 'volume_max', vol) or vol
    v = round(round(vol / step) * step, 8)
    return max(vmin, min(vmax, v))

def lot_for_level(direction: str, level: int) -> float:
    """Level-k lot = base * (short_lot_mult if SHORT) * taper^(k-1), broker-rounded."""
    base = LOT * (SHORT_LOT_MULT if direction == 'SHORT' else 1.0)
    if TAPER:
        base = base * (TAPER ** (level - 1))
    return _round_lot(_rsym(), base)

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
    """Place market order. direction: 'LONG' or 'SHORT'. Returns True on success.
    Volume = lot_for_level (taper/tilt). TP = entry +/- grid_width*TP_MULT."""
    sym  = _rsym()
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        log('order_failed ' + direction + ' symbol_info=None')
        return False
    digits = info.digits
    vol    = lot_for_level(direction, level)
    tp_dist = grid_width * TP_MULT

    if direction == 'LONG':
        order_type = mt5.ORDER_TYPE_BUY
        entry      = tick.ask
        tp         = round(entry + tp_dist, digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        entry      = tick.bid
        tp         = round(entry - tp_dist, digits)

    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       sym,
        'volume':       vol,
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
        ' lot=' + str(vol) +
        ' price=' + str(round(entry, digits)) +
        ' grid_width=' + str(round(grid_width, digits)) +
        ' tp_mult=' + str(TP_MULT) +
        ' level=' + str(level) + '/' + str(MAX_LEVELS))
    return True

def close_positions(direction: str, positions: list, suffix: str = '_B48') -> tuple:
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
            'comment':      STRATEGY_TAG + suffix,
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

def close_single_position(p, suffix: str = '_CULL') -> tuple:
    """Close one position at market. Returns (ok, pnl, ticket)."""
    sym  = _rsym()
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        return False, 0.0, None
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
        'comment':      STRATEGY_TAG + suffix,
        'position':     p.ticket,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True, p.profit, p.ticket
    code = result.retcode if result else 'None'
    log('cull_close_failed ticket=' + str(p.ticket) + ' code=' + str(code))
    return False, 0.0, None

# ══════════════════════════════════════════
# TP close detection
# ══════════════════════════════════════════
def check_tp_closes(from_dt: datetime, skip_tickets: set) -> None:
    """Detect and log TP-hit closures since from_dt, skipping B48/cull-closed tickets."""
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
        if d.position_id in skip_tickets:
            continue
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
    log('grid_monitor v8 started  pair=' + SYMBOL +
        '  broker=' + BROKER_KEY +
        '  magic=' + str(MAGIC) +
        '  lot=' + str(LOT) +
        '  atr_mult=' + str(ATR_MULT) +
        '  max_levels=' + str(MAX_LEVELS) +
        '  ci_th=' + str(CI_TH) +
        '  dir=' + DIR_MODE +
        ('  sma=' + str(SMA_PERIOD) if DIR_MODE == 'regime_short' else '') +
        ('  mom=' + str(MOM_THR) if MOM_THR else '') +
        ('  mom120=' + str(MOM120_THR) if MOM120_THR else '') +
        ('  cull=' + str(CULL_FRAC) if CULL_FRAC else '') +
        ('  taper=' + str(TAPER) if TAPER else '') +
        ('  short_lot=' + str(SHORT_LOT_MULT) if SHORT_LOT_MULT != 1.0 else '') +
        ('  tp_mult=' + str(TP_MULT) if TP_MULT != 1.0 else '') +
        ('  CLOSE_ONLY(drain)' if CLOSE_ONLY else '') +
        '  float_stop=' + str(int(FLOAT_STOP_JPY)) +
        '  interval=' + str(LOOP_INTERVAL) + 's')

    state   = load_state()
    _cycle  = 0
    _last_tp_check      = datetime.now(timezone.utc) - timedelta(minutes=5)
    _last_filter_block  = 0

    while True:
        try:
            _cycle  += 1
            now_utc  = datetime.now(timezone.utc)
            today_s  = now_utc.strftime('%Y-%m-%d')
            week_s   = now_utc.strftime('%G-W%V')

            # ── Day / week reset ──
            if state['current_day'] != today_s:
                state['current_day']      = today_s
                state['day_realized_jpy'] = 0.0
            if state['current_week'] != week_s:
                state['current_week']      = week_s
                state['week_realized_jpy'] = 0.0

            # ── Realized PnL ──
            day_pnl  = _realized_jpy_since(_day_start_utc())
            week_pnl = _realized_jpy_since(_week_start_utc())
            state['day_realized_jpy']  = day_pnl
            state['week_realized_jpy'] = week_pnl

            # ── Fetch OHLCV ──
            sym   = _rsym()
            df_h1 = _get_h1(sym, H1_BARS)
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

            # ── v8 directional & momentum gates (computed once per cycle) ──
            allow_long  = True
            allow_short = (DIR_MODE != 'long_only')
            regime_note = ''
            if DIR_MODE == 'regime_short' and SMA_PERIOD:
                sma = calc_sma_closed(df_h1, SMA_PERIOD)
                cprev = df_h1['close'].iloc[-2] if len(df_h1) >= 2 else None
                if sma is not None and cprev is not None and cprev > sma:
                    allow_short = False   # up-regime: block new counter-trend shorts
                    regime_note = 'regime_up(no_short)'

            mom_long_ok = mom_short_ok = True
            if MOM_THR:
                r = calc_ret_norm(df_h1, MOM_WINDOW, atr)
                if r is not None:
                    mom_long_ok  = r > -MOM_THR
                    mom_short_ok = r < MOM_THR
            if MOM120_THR:
                r2 = calc_ret_norm(df_h1, MOM120_WINDOW, atr)
                if r2 is not None:
                    mom_long_ok  = mom_long_ok  and (r2 > -MOM120_THR)
                    mom_short_ok = mom_short_ok and (r2 < MOM120_THR)

            # ── Live mid price ──
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                log('loop_error tick_failed sym=' + sym)
                time.sleep(LOOP_INTERVAL)
                continue
            mid_price = (tick.ask + tick.bid) / 2.0

            # ── Positions ──
            longs, shorts = get_positions()
            long_count    = len(longs)
            short_count   = len(shorts)

            b48_closed_tickets: set = set()

            # ── Float stop: close direction if basket unrealized < threshold ──
            if longs:
                long_float = sum(p.profit for p in longs)
                if long_float < FLOAT_STOP_JPY:
                    n_cl, tot, tix = close_positions('LONG', longs, '_FS')
                    b48_closed_tickets |= tix
                    log('float_stop LONG unrealized=' + str(round(long_float)) +
                        ' JPY  closed=' + str(n_cl))
                    state['max_lv_reached_ts_long'] = None
                    longs, _ = get_positions()
                    long_count = len(longs)

            if shorts:
                short_float = sum(p.profit for p in shorts)
                if short_float < FLOAT_STOP_JPY:
                    _, shorts_now = get_positions()
                    n_cl, tot, tix = close_positions('SHORT', shorts_now, '_FS')
                    b48_closed_tickets |= tix
                    log('float_stop SHORT unrealized=' + str(round(short_float)) +
                        ' JPY  closed=' + str(n_cl))
                    state['max_lv_reached_ts_short'] = None
                    _, shorts = get_positions()
                    short_count = len(shorts)

            # ── B48 timer: Long ──
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

            # ── v8 worst-leg cull: basket unrealized <= cull_frac*float_stop ──
            if CULL_FRAC and FLOAT_STOP_JPY:
                cull_thr = CULL_FRAC * FLOAT_STOP_JPY   # e.g. 0.5 * -750k = -375k
                if long_count >= 2:
                    lf = sum(p.profit for p in longs)
                    if lf <= cull_thr:
                        worst = min(longs, key=lambda p: p.profit)
                        ok, pnl, _ = close_single_position(worst)
                        if ok:
                            log('cull LONG basket=' + str(round(lf)) +
                                ' JPY  worst_leg=' + str(round(pnl)) + ' JPY')
                            longs, _ = get_positions()
                            long_count = len(longs)
                            if long_count < MAX_LEVELS:
                                state['max_lv_reached_ts_long'] = None
                if short_count >= 2:
                    sf = sum(p.profit for p in shorts)
                    if sf <= cull_thr:
                        _, shorts_now = get_positions()
                        worst = min(shorts_now, key=lambda p: p.profit)
                        ok, pnl, _ = close_single_position(worst)
                        if ok:
                            log('cull SHORT basket=' + str(round(sf)) +
                                ' JPY  worst_leg=' + str(round(pnl)) + ' JPY')
                            _, shorts = get_positions()
                            short_count = len(shorts)
                            if short_count < MAX_LEVELS:
                                state['max_lv_reached_ts_short'] = None

            # ── TP close detection ──
            check_tp_closes(_last_tp_check, b48_closed_tickets)
            _last_tp_check = now_utc

            save_state(state)

            # ── Heartbeat ──
            ci_str = str(round(ci, 1)) if ci is not None else 'N/A'
            if _cycle % HB_CYCLES == 0:
                float_l = round(sum(p.profit for p in longs))  if longs  else 0
                float_s = round(sum(p.profit for p in shorts)) if shorts else 0
                gate_s = ''
                if not allow_short:
                    gate_s += ' short_off(' + (regime_note or DIR_MODE) + ')'
                if MOM_THR and not (mom_long_ok and mom_short_ok):
                    gate_s += ' mom_block(L' + str(int(mom_long_ok)) + '/S' + str(int(mom_short_ok)) + ')'
                log('heartbeat alive' +
                    ' long_pos='  + str(long_count)  + '/' + str(MAX_LEVELS) +
                    ' short_pos=' + str(short_count) + '/' + str(MAX_LEVELS) +
                    ' float_l=' + str(float_l) +
                    ' float_s=' + str(float_s) +
                    ' ci=' + ci_str + gate_s)

            # ── Filter check ──
            if ci is None:
                time.sleep(LOOP_INTERVAL)
                continue

            entry_blocked  = False
            block_reasons  = []
            dd_force_close = False
            if ci <= CI_TH:
                entry_blocked = True
                block_reasons.append('ci=' + ci_str + ' (threshold=' + str(CI_TH) + ')')
            if day_pnl < DD_DAY_JPY:
                entry_blocked = True
                dd_force_close = True
                block_reasons.append('day_pnl=' + str(round(day_pnl)) + ' JPY')
            if week_pnl < DD_WEEK_JPY:
                entry_blocked = True
                dd_force_close = True
                block_reasons.append('week_pnl=' + str(round(week_pnl)) + ' JPY')

            if dd_force_close:
                longs, shorts = get_positions()
                if longs:
                    n_cl, tot, _ = close_positions('LONG', longs, '_DD')
                    log('dd_force_close LONG positions=' + str(n_cl) +
                        ' total_pnl=' + ('+' if tot >= 0 else '') + str(round(tot)) + ' JPY' +
                        ' day=' + str(round(day_pnl)) + ' week=' + str(round(week_pnl)))
                    state['max_lv_reached_ts_long'] = None
                if shorts:
                    _, shorts_now = get_positions()
                    n_cl, tot, _ = close_positions('SHORT', shorts_now, '_DD')
                    log('dd_force_close SHORT positions=' + str(n_cl) +
                        ' total_pnl=' + ('+' if tot >= 0 else '') + str(round(tot)) + ' JPY' +
                        ' day=' + str(round(day_pnl)) + ' week=' + str(round(week_pnl)))
                    state['max_lv_reached_ts_short'] = None
                save_state(state)

            # Refresh positions after forced closes
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

            # ── v8 gates applied to entry decisions ──
            long_gated  = long_entry_needed  and allow_long  and mom_long_ok
            short_gated = short_entry_needed and allow_short and mom_short_ok

            # ── Log filter_block (throttled) ──
            if entry_blocked and (long_entry_needed or short_entry_needed):
                if _cycle - _last_filter_block >= HB_CYCLES:
                    for reason in block_reasons:
                        log('filter_block ' + reason)
                    _last_filter_block = _cycle

            # ── Execute entries (skipped entirely in CLOSE_ONLY drain mode) ──
            if CLOSE_ONLY:
                if long_count == 0 and short_count == 0 and _cycle % HB_CYCLES == 0:
                    log('close_only flat: no GBPJPY/grid positions remain '
                        '(safe to stop this daemon)')
            elif not entry_blocked:
                if long_gated:
                    place_order('LONG', grid_width, long_count + 1)
                    longs, _ = get_positions()
                    long_count = len(longs)

                if short_gated:
                    place_order('SHORT', grid_width, short_count + 1)

        except Exception as e:
            log('loop_error ' + str(e))

        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main() -> None:
    global MAGIC, STRATEGY_TAG, SYMBOL, ATR_MULT, MAX_LEVELS, CI_TH
    global BROKER_KEY, LOG_FILE, _STATE_FILE
    global LOT, DD_DAY_JPY, DD_WEEK_JPY, FLOAT_STOP_JPY
    global DIR_MODE, SMA_PERIOD, MOM_THR, MOM120_THR, CULL_FRAC, TAPER, SHORT_LOT_MULT, TP_MULT, H1_BARS
    global CLOSE_ONLY

    parser = argparse.ArgumentParser(description='Grid Strategy monitor v8 (multi-pair)')
    parser.add_argument('--pair', default='AUDCAD',
                        choices=list(PAIR_CONFIG.keys()),
                        help='trading pair')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['axiory', 'exness', 'oanda', 'oanda_demo'],
                        help='broker key')
    parser.add_argument('--close-only', action='store_true',
                        help='drain mode: manage exits (TP/B48/float-stop/cull) only, '
                             'never open new entries. Use to unwind a No-Go pair (e.g. GBPJPY).')
    args = parser.parse_args()
    CLOSE_ONLY = args.close_only

    # Apply pair config
    cfg          = PAIR_CONFIG[args.pair]
    SYMBOL       = args.pair
    MAGIC        = cfg['magic']
    STRATEGY_TAG = cfg['tag']
    ATR_MULT     = cfg['atr_mult']
    MAX_LEVELS   = cfg['max_levels']
    CI_TH        = cfg.get('ci_threshold', CI_THRESHOLD)
    BROKER_KEY   = args.broker

    # v8 feature knobs
    DIR_MODE       = cfg.get('dir_mode', 'both')
    SMA_PERIOD     = cfg.get('sma_period', None)
    MOM_THR        = cfg.get('mom_thr', None)
    MOM120_THR     = cfg.get('mom120_thr', None)
    CULL_FRAC      = cfg.get('cull_frac', None)
    TAPER          = cfg.get('taper', None)
    SHORT_LOT_MULT = cfg.get('short_lot_mult', 1.0)
    TP_MULT        = cfg.get('tp_mult', 1.0)

    # H1 bars to fetch: enough for ATR + the longest lookback in use
    needed = ATR_PERIOD + 10
    if MOM_THR:
        needed = max(needed, MOM_WINDOW + 5)
    if MOM120_THR:
        needed = max(needed, MOM120_WINDOW + 5)
    if DIR_MODE == 'regime_short' and SMA_PERIOD:
        needed = max(needed, SMA_PERIOD + 5)
    H1_BARS = needed

    # Per-pair lot, DD limits, float stop
    LOT            = LOT_PER_PAIR.get(SYMBOL, 0.01)
    DD_DAY_JPY     = DD_DAY_PER_PAIR.get(SYMBOL,  -5000.0)
    DD_WEEK_JPY    = DD_WEEK_PER_PAIR.get(SYMBOL, -15000.0)
    FLOAT_STOP_JPY = FLOAT_STOP_PER_PAIR.get(SYMBOL, -15000.0)

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
