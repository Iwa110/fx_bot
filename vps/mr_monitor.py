"""
mr_monitor.py - Correlation-cross 4h Mean-Reversion, 3-tier unequal-split monitor v2

Strategy (BT: optimizer/dynamic_lot_mr_bt.run_bt_tiered3; transfer validation:
optimizer/mr_tiered_transfer_bt.py; AUDCAD plan: optimizer/audcad_mr_deployment_plan.md):
  TF = H4. z = (close - SMA40) / SD40 on the last CLOSED bar (t-1, no lookahead).
  3-tier UNEQUAL split, ONE cluster at a time (no concurrent opposite cluster):
    Tier1 |z|>=2.0 -> 0.2 lot ; Tier2 |z|>=2.5 -> 0.3 ; Tier3 |z|>=3.0 -> 0.5
    (max total exposure = 1.0 lot * LOT_SCALE). short if z>0, long if z<0.
  High-vol lot throttle: at the Tier1 signal bar, if ATR percentile (t-1, over
    ATR_LOOKBACK bars) >= VOL_TH, multiply ALL tier lots by VOL_MULT (do NOT skip
    the entry -> keep the edge, compress exposure when risk is high). The throttle
    factor is fixed at Tier1 and applied to every leg (matches BT).
  Exit depends on the per-pair regression speed (T_reg, BT Part1):
    construction A (whole basket at once)  -- slow-reverting pairs:
      TP : price returns to SMA40 (z=0).  SL: hard |z|>=Z_STOP or time MAX_HOLD_BARS.
    construction B (deepest-tier partial)  -- fast-reverting pairs:
      Tier3 leg(s) partial-close at |z|<=PARTIAL_Z first; the remaining T1/T2 then
      exit at SMA40 (z=0). Same hard-stop / time-stop applies to the whole basket.
  Entries/adds are evaluated ONCE per newly-closed H4 bar (next-bar-open analog).
  Exits are checked every cycle against the live tick (responsive).

Confirmed per-pair configs (BT IS=2015-21 / OOS=2022-26 / yearly WFO):
  AUDCAD : magic 20260050 / MR_AC / exit A / z_stop 4.5 / vol_th 0.70
           OOS PF 2.60, wfoMin 1.81 (Tier1, deployment plan).
  CADCHF : magic 20260051 / MR_CC / exit B / z_stop 4.0 / vol_th 0.90
           OOS PF 1.26, IS 1.28, wfoMin 0.91 -- fastest regression (T_reg med 19),
           partial-TP (B) optimal; pairs with AUDCAD inverse-vol => Sharpe 1.09/MaxDD151
           (Pareto improvement over AUDCAD-alone 1.03/222). Tier2 diversifier.
  (AUDNZD marginal / EURGBP rejected: IS<->OOS sign reversal. Not deployed.)

Brokers: demo axiory/exness (forward-test first). Live refused until LIVE_LOT_SCALE>0
  (and live lot must be sized from that pair's own MC95 DD, not AUDCAD's).
State: mr_monitor_state_{PAIR}_{broker}.json   Log: mr_log_{PAIR}_{broker}.txt

Run (per the plan, 4h cadence; loop is light so a 5-min poll is fine):
  python mr_monitor.py --pair AUDCAD --broker axiory
  python mr_monitor.py --pair CADCHF --broker axiory
"""

import sys
import os
import time
import argparse
import json
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# Strategy constants (frozen = BT confirmed)
# ══════════════════════════════════════════
TIER_ZS         = [2.0, 2.5, 3.0]   # tier entry |z| thresholds
TIER_LOTS       = [0.2, 0.3, 0.5]   # unequal split (sum=1.0 max exposure)
SMA_N           = 40                # MA/SD window (H4 bars)
ATR_N           = 14
ATR_LOOKBACK    = 500               # ATR percentile window
Z_TP            = 0.0               # take-profit z (0 = MA)
MAX_HOLD_BARS   = 48                # time stop (H4 bars = 8 days)
VOL_MULT        = 0.5               # lot multiplier when high-vol
PARTIAL_Z_DEF   = 1.5              # construction B: Tier3 partial-TP z level
H4_SECONDS      = 4 * 3600

# Per-pair (set as runtime globals in main from the selected PAIR_CONFIG entry).
Z_STOP          = 4.5               # hard-stop |z| (AUDCAD default; overridden per-pair)
VOL_TH          = 0.70              # ATR percentile throttle threshold (overridden per-pair)
EXIT_MODE       = 'A'              # 'A' whole-basket / 'B' deepest-tier partial-TP
PARTIAL_Z       = PARTIAL_Z_DEF

LOOP_INTERVAL   = 300               # 5-min poll (4h strategy; exits stay responsive)
HB_CYCLES       = 12                # heartbeat every ~1h

# Per-pair confirmed configs (BT: optimizer/mr_tiered_transfer_bt.py). exit_mode/z_stop/
# vol_th are pair-specific; tiers (0.2/0.3/0.5) and SMA/ATR/hold are shared & frozen.
PAIR_CONFIG = {
    'AUDCAD': {'magic': 20260050, 'tag': 'MR_AC', 'exit_mode': 'A',
               'z_stop': 4.5, 'vol_th': 0.70, 'partial_z': 1.5},
    'CADCHF': {'magic': 20260051, 'tag': 'MR_CC', 'exit_mode': 'B',
               'z_stop': 4.0, 'vol_th': 0.90, 'partial_z': 1.5},
}

# Lot scale. Demo forward-test = 1.0 (=> tier lots 0.2/0.3/0.5, BT-comparable).
# Live = explicit, sized from MC95 DD (plan §3: lot_scale = max_DD_yen / 430,000).
# 0.0 => refuse live until the demo forward-test gate (plan §5) is cleared.
LOT_SCALE_DEMO  = 1.0
LIVE_LOT_SCALE  = 0.0

# ══════════════════════════════════════════
# Runtime globals (set in main())
# ══════════════════════════════════════════
MAGIC        = 20260050
STRATEGY_TAG = 'MR_AC'
SYMBOL       = 'AUDCAD'
BROKER_KEY   = 'axiory'
LOT_SCALE    = LOT_SCALE_DEMO
CLOSE_ONLY   = False
H4_BARS      = ATR_LOOKBACK + SMA_N + 20
_SYMBOL_MAP: dict = {}

def _rsym() -> str:
    return _SYMBOL_MAP.get(SYMBOL, SYMBOL)

# ══════════════════════════════════════════
# Logging / state
# ══════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(_BASE_DIR, 'mr_log.txt')

def log(msg: str) -> None:
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = ts + '  ' + STRATEGY_TAG + '  ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

_STATE_FILE = os.path.join(_BASE_DIR, 'mr_monitor_state.json')
_STATE_DEFAULTS = {
    'tmul':            1.0,    # high-vol throttle factor fixed at Tier1
    'tier1_bar_iso':   '',     # H4 bar (UTC) of Tier1 entry (fallback for time-stop)
    'last_eval_bar':   '',     # last closed H4 bar we evaluated entry/add on
    'n_filled_max':    0,      # highest tier count reached this cluster (monotonic =
                               # BT next_tier counter; survives B partial-close so a
                               # closed Tier3 is never re-added).
}

def load_state() -> dict:
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        for k, v in _STATE_DEFAULTS.items():
            s.setdefault(k, v)
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
# Data / indicators (H4, closed-bar only)
# ══════════════════════════════════════════
def get_h4(symbol: str, n: int):
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, n)
    if bars is None or len(bars) < SMA_N + 5:
        return None
    df = pd.DataFrame(bars)
    df['datetime'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df.sort_values('datetime').reset_index(drop=True)

def compute_indicators(df):
    """Return dict of t-1 (last CLOSED bar) indicators, or None.
    The forming bar (iloc[-1]) is excluded -> matches BT (signal on closed bar)."""
    closed = df.iloc[:-1]                       # drop the forming H4 bar
    if len(closed) < ATR_LOOKBACK + 2:
        # still allow once we have >= SMA_N + a bit; ATR pct needs history though
        if len(closed) < SMA_N + 2:
            return None
    c = closed['close']
    sma = float(c.iloc[-SMA_N:].mean())
    sd  = float(c.iloc[-SMA_N:].std(ddof=0))
    if sd <= 0:
        return None
    z = (float(c.iloc[-1]) - sma) / sd
    # ATR (Wilder ewm) + percentile over last ATR_LOOKBACK closed bars
    h, l = closed['high'], closed['low']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1.0 / ATR_N, adjust=False).mean()
    atr = float(atr_series.iloc[-1])
    look = atr_series.iloc[-ATR_LOOKBACK:].dropna()
    atr_pct = float((look.iloc[-1] >= look).mean()) if len(look) >= 20 else float('nan')
    return {'sma': sma, 'sd': sd, 'z': z, 'atr': atr, 'atr_pct': atr_pct,
            'bar_iso': closed['datetime'].iloc[-1].isoformat(),
            'close': float(c.iloc[-1])}

# ══════════════════════════════════════════
# Positions / orders
# ══════════════════════════════════════════
def get_cluster():
    """Return (side, positions) for this magic. side in {'long','short',None}.
    By construction only one side is held at a time."""
    sym = _rsym()
    pos = mt5.positions_get(symbol=sym)
    if not pos:
        return None, []
    mine = [p for p in pos if p.magic == MAGIC]
    longs  = [p for p in mine if p.type == mt5.ORDER_TYPE_BUY]
    shorts = [p for p in mine if p.type == mt5.ORDER_TYPE_SELL]
    if longs and not shorts:
        return 'long', longs
    if shorts and not longs:
        return 'short', shorts
    if longs and shorts:                        # should not happen -> log, manage larger
        log('WARN both sides held longs=%d shorts=%d' % (len(longs), len(shorts)))
        return ('long', longs) if len(longs) >= len(shorts) else ('short', shorts)
    return None, []

def _round_lot(symbol: str, vol: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return round(vol, 2)
    step = getattr(info, 'volume_step', 0.01) or 0.01
    vmin = getattr(info, 'volume_min', step) or step
    vmax = getattr(info, 'volume_max', vol) or vol
    v = round(round(vol / step) * step, 8)
    return max(vmin, min(vmax, v))

def tier_lot(tier_idx: int, tmul: float) -> float:
    return _round_lot(_rsym(), TIER_LOTS[tier_idx] * LOT_SCALE * tmul)

def place_tier(side: str, tier_idx: int, tmul: float, z: float) -> bool:
    """Open one tier leg at market (tp=0/sl=0 -> basket exits managed in code)."""
    sym  = _rsym()
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        log('order_failed tier%d symbol_info/tick=None' % (tier_idx + 1))
        return False
    vol = tier_lot(tier_idx, tmul)
    if vol <= 0:
        log('order_skip tier%d vol<=0 (LOT_SCALE=%s)' % (tier_idx + 1, LOT_SCALE))
        return False
    if side == 'long':
        order_type, price = mt5.ORDER_TYPE_BUY, tick.ask
    else:
        order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
    req = {
        'action': mt5.TRADE_ACTION_DEAL, 'symbol': sym, 'volume': vol,
        'type': order_type, 'price': price, 'tp': 0.0, 'sl': 0.0,
        'deviation': 20, 'magic': MAGIC,
        'comment': STRATEGY_TAG + '_T' + str(tier_idx + 1),
        'type_time': mt5.ORDER_TIME_GTC, 'type_filling': mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        log('order_failed tier%d %s code=%s' %
            (tier_idx + 1, side, res.retcode if res else 'None'))
        return False
    log('entry T%d %s lot=%s price=%s z=%.2f tmul=%.2f thr=%.1f' %
        (tier_idx + 1, side, vol, round(price, info.digits), z, tmul, TIER_ZS[tier_idx]))
    return True

def close_cluster(positions: list, suffix: str) -> tuple:
    """Close all legs at market. Returns (closed_count, total_pnl)."""
    sym = _rsym()
    closed, tot = 0, 0.0
    for p in positions:
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            continue
        is_long = (p.type == mt5.ORDER_TYPE_BUY)
        ctype = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_long else tick.ask
        req = {
            'action': mt5.TRADE_ACTION_DEAL, 'symbol': sym, 'volume': p.volume,
            'type': ctype, 'price': price, 'deviation': 20, 'magic': MAGIC,
            'comment': STRATEGY_TAG + suffix, 'position': p.ticket,
            'type_time': mt5.ORDER_TIME_GTC, 'type_filling': mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            tot += p.profit
            closed += 1
        else:
            log('close_failed ticket=%s code=%s' % (p.ticket, res.retcode if res else 'None'))
    return closed, tot

# ══════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════
def bars_held(positions) -> int:
    """H4 bars since the earliest leg opened (robust to daemon restarts)."""
    if not positions:
        return 0
    t0 = min(p.time for p in positions)         # epoch seconds (UTC)
    return int((datetime.now(timezone.utc).timestamp() - t0) // H4_SECONDS)

def main_loop():
    state = load_state()
    cycle = 0
    log('start  symbol=%s magic=%d lot_scale=%s tiers=%s/%s exit=%s z_stop=%.1f '
        'vol_th=%.2f partial_z=%.1f close_only=%s' %
        (SYMBOL, MAGIC, LOT_SCALE, TIER_ZS, TIER_LOTS, EXIT_MODE, Z_STOP,
         VOL_TH, PARTIAL_Z, CLOSE_ONLY))
    while True:
        cycle += 1
        try:
            df = get_h4(_rsym(), H4_BARS)
            if df is None:
                log('data_fetch_failed h4')
                time.sleep(LOOP_INTERVAL)
                continue
            ind = compute_indicators(df)
            if ind is None:
                log('indicator_warmup (insufficient closed bars)')
                time.sleep(LOOP_INTERVAL)
                continue
            sma, sd, z = ind['sma'], ind['sd'], ind['z']
            side, positions = get_cluster()
            n_filled = len(positions)
            tick = mt5.symbol_info_tick(_rsym())
            if tick is None:
                time.sleep(LOOP_INTERVAL)
                continue

            # ── EXIT MANAGEMENT (every cycle, responsive) ──
            if side is not None and n_filled > 0:
                if side == 'short':
                    px = tick.ask                       # cost to close a short
                    z_live = (px - sma) / sd
                    hit_tp = px <= sma                  # returned to MA
                    hit_stop = z_live >= Z_STOP
                    hit_ptp = z_live <= PARTIAL_Z       # reverted to within partial band
                else:
                    px = tick.bid
                    z_live = (px - sma) / sd
                    hit_tp = px >= sma
                    hit_stop = z_live <= -Z_STOP
                    hit_ptp = z_live >= -PARTIAL_Z
                held = bars_held(positions)

                # construction B: partial-close the deepest tier (T3) before full exit.
                # Only fires while T3 is open; degrades to construction A if T3 absent.
                if (EXIT_MODE == 'B' and not hit_stop and held < MAX_HOLD_BARS
                        and hit_ptp):
                    t3 = [p for p in positions
                          if str(getattr(p, 'comment', '')).endswith('_T3')]
                    if t3:
                        nclosed, pnl = close_cluster(t3, '_PTP')
                        if nclosed:
                            log('partial_tp T3 %s legs=%d z=%.2f pnl=%.0f' %
                                (side, nclosed, z_live, pnl))
                            t3_tk = {p.ticket for p in t3}
                            positions = [p for p in positions if p.ticket not in t3_tk]
                            n_filled = len(positions)

                reason = None
                if hit_stop:
                    reason = '_ZSTOP'
                elif hit_tp:
                    reason = '_TP'
                elif held >= MAX_HOLD_BARS:
                    reason = '_TIME'
                if reason and positions:
                    nclosed, pnl = close_cluster(positions, reason)
                    log('exit%s %s legs=%d held=%dbar z=%.2f pnl=%.0f' %
                        (reason, side, nclosed, held, z_live, pnl))
                    state['tmul'] = 1.0
                    state['tier1_bar_iso'] = ''
                    state['n_filled_max'] = 0
                    save_state(state)
                    side, positions, n_filled = None, [], 0

            # ── ENTRY / ADD (once per newly-closed H4 bar) ──
            new_bar = (state.get('last_eval_bar', '') != ind['bar_iso'])
            if new_bar and not CLOSE_ONLY:
                if side is None:
                    # flat -> Tier1 signal?
                    new_side = 'short' if z >= TIER_ZS[0] else ('long' if z <= -TIER_ZS[0] else None)
                    if new_side is not None:
                        ap = ind['atr_pct']
                        tmul = VOL_MULT if (not np.isnan(ap) and ap >= VOL_TH) else 1.0
                        if place_tier(new_side, 0, tmul, z):
                            state['tmul'] = tmul
                            state['tier1_bar_iso'] = ind['bar_iso']
                            state['n_filled_max'] = 1
                else:
                    # in cluster -> deeper tier reached? Use the monotonic counter
                    # (not the live leg count) so a B-partial-closed Tier3 is never re-added.
                    nxt = int(state.get('n_filled_max', n_filled) or n_filled)
                    if nxt < len(TIER_ZS):
                        thr = TIER_ZS[nxt]
                        deeper = (side == 'short' and z >= thr) or (side == 'long' and z <= -thr)
                        if deeper:
                            tmul = float(state.get('tmul', 1.0))
                            if place_tier(side, nxt, tmul, z):
                                state['n_filled_max'] = nxt + 1
                state['last_eval_bar'] = ind['bar_iso']
                save_state(state)

            # ── Heartbeat ──
            if cycle % HB_CYCLES == 0:
                ap = ind['atr_pct']
                log('heartbeat alive side=%s legs=%d z=%.2f sma=%.5f atr_pct=%s held=%dbar' %
                    (side or 'flat', n_filled, z, sma,
                     ('%.2f' % ap) if not np.isnan(ap) else 'na',
                     bars_held(positions)))

        except Exception as e:
            log('loop_error ' + str(e))
        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main():
    global MAGIC, STRATEGY_TAG, SYMBOL, BROKER_KEY, LOT_SCALE
    global LOG_FILE, _STATE_FILE, CLOSE_ONLY
    global EXIT_MODE, Z_STOP, VOL_TH, PARTIAL_Z

    ap = argparse.ArgumentParser(description='Correlation-cross 4h mean-reversion tier3 monitor v2')
    ap.add_argument('--pair', default='AUDCAD', choices=list(PAIR_CONFIG.keys()))
    ap.add_argument('--broker', default=BROKER_KEY,
                    choices=['axiory', 'exness', 'oanda', 'oanda_demo', 'oanda_live'])
    ap.add_argument('--close-only', action='store_true',
                    help='drain mode: manage exits only, open no new entries')
    args = ap.parse_args()
    CLOSE_ONLY = args.close_only

    cfg = PAIR_CONFIG[args.pair]
    SYMBOL, MAGIC, STRATEGY_TAG = args.pair, cfg['magic'], cfg['tag']
    EXIT_MODE = cfg.get('exit_mode', 'A')
    Z_STOP = cfg.get('z_stop', 4.5)
    VOL_TH = cfg.get('vol_th', 0.70)
    PARTIAL_Z = cfg.get('partial_z', PARTIAL_Z_DEF)
    BROKER_KEY = args.broker

    # Demo (BT-comparable lot_scale=1.0) vs live (explicit, sized from MC95 DD).
    if is_live_broker(BROKER_KEY):
        LOT_SCALE = LIVE_LOT_SCALE
        if LOT_SCALE <= 0:
            log('LIVE refuse: LIVE_LOT_SCALE not set for %s (clear demo forward-test '
                'first, then size from MC95 DD per plan §3) broker=%s' % (SYMBOL, BROKER_KEY))
            return
    else:
        LOT_SCALE = LOT_SCALE_DEMO

    LOG_FILE    = os.path.join(_BASE_DIR, 'mr_log_' + SYMBOL + '_' + BROKER_KEY + '.txt')
    _STATE_FILE = os.path.join(_BASE_DIR,
                               'mr_monitor_state_' + SYMBOL + '_' + BROKER_KEY + '.json')

    if not connect_mt5(BROKER_KEY):
        log('MT5 init failed  broker=' + BROKER_KEY)
        return
    try:
        acc = mt5.account_info()
        if acc is None:
            log('account_info failed')
            return
        log('connected  broker=%s login=%s lot_scale=%s' % (BROKER_KEY, acc.login, LOT_SCALE))
        _SYMBOL_MAP.update(build_symbol_map([SYMBOL], BROKER_KEY))
        rsym = _rsym()
        if not mt5.symbol_select(rsym, True):
            log('symbol_select failed for %s (%s)' % (rsym, mt5.last_error()))
        main_loop()
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
