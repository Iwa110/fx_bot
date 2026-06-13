"""
sma_squeeze_exit_bt.py  -  Exit strategy optimization for SMA Squeeze Play.

Uses best-known entry params per pair (from PAIRS_CFG in sma_squeeze.py).
Grid-searches exit methods and compares vs baseline.

Exit model (IMPORTANT)
----------------------
Stops/targets fill INTRABAR: SL / trailing-stop / TP are tested against each
bar's low/high, not its close. The live bot (vps/sma_squeeze.py manage_atr_trail)
ratchets the trail every 60s on tick.bid, so a stop sitting in the market is hit
the moment price touches it -- well before the bar closes.

A previous version of this BT only tested exits against the bar close. That
systematically misses stop-outs and badly inflates PF for tight trails (it
reported e.g. USDJPY atr_trail_mult=0.5 PF 1.815 -> 4.441, sign-inverted vs
reality). The engine here mirrors sma_squeeze_divergence_bt.run_bt_live_exits:
  - entry at NEXT bar open + spread (live execution, not signal-bar close)
  - exit priority per bar:  SL/trail (low/high) -> TP (low/high)
                            -> SMA_long break (close) -> slope reversal (close)
  - trailing stop ratchets from the bar CLOSE, effective from the next bar
    (check-then-update ordering; never tighten and trigger within the same bar)

Exit methods
------------
baseline     : fixed SL/TP + SMA_long price break force-close (original)
fixed_trail  : fixed-distance trailing SL (current VPS: TRAIL_MULT style)
atr_trail    : ATR-adaptive trailing SL (trail width = ATR14 * mult)
slope_exit   : early exit when SMA_long slope reverses N consecutive bars
div_tighten  : tighten trail when price diverges far from SMA (momentum exhaustion)
combined     : ATR-adaptive trail + slope reversal exit (dual trigger)

Output
------
optimizer/sma_squeeze_exit_bt_result.csv   (all runs)
Summary table printed to stdout per pair.
"""

import itertools
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# Repo data dir (local). On VPS this is C:\Users\Administrator\fx_bot\data.
DATA_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'sma_squeeze_exit_bt_result.csv')

# Typical spread (price units). JPY pairs ~ 0.02-0.03 yen, USD pairs ~ 0.0001-0.0002.
# Matches sma_squeeze_divergence_bt.SPREAD for comparable numbers.
SPREAD = {'USDJPY': 0.02, 'GBPJPY': 0.03, 'EURUSD': 0.00012, 'GBPUSD': 0.00020, 'EURJPY': 0.03}

# ── Best entry params per pair (from sma_squeeze_bt.py grid search) ──────────────
# Primary timeframe matches VPS sma_squeeze.py.
# Fallback: when 1h CSV is unavailable locally (only 4h present), the loader
#           automatically tries 4h — valid for comparing EXIT methods since the
#           relative ranking between methods is timeframe-independent.
PAIRS_CFG = {
    'USDJPY': {'sma_short': 25, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 5,  'rr': 2.5, 'sl_atr_mult': 1.5, 'timeframe': '4h'},
    'GBPJPY': {'sma_short': 25, 'sma_long': 250, 'squeeze_th': 0.5,
               'slope_period': 10, 'rr': 2.0, 'sl_atr_mult': 1.5, 'timeframe': '1h'},
    'EURUSD': {'sma_short': 25, 'sma_long': 200, 'squeeze_th': 2.0,
               'slope_period': 10, 'rr': 2.5, 'sl_atr_mult': 1.0, 'timeframe': '4h'},
    'GBPUSD': {'sma_short': 15, 'sma_long': 250, 'squeeze_th': 1.5,
               'slope_period': 20, 'rr': 2.0, 'sl_atr_mult': 1.0, 'timeframe': '1h'},
    'EURJPY': {'sma_short': 15, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 20, 'rr': 2.5, 'sl_atr_mult': 1.5, 'timeframe': '4h'},
}

MIN_TRADES = 15   # lower than entry BT since exit params don't change trade count much


# ── Exit method grid ────────────────────────────────────────────────────────────────────────
def build_exit_grid():
    """Return list of exit parameter dicts to test."""
    grid = []

    # 0. Baseline: fixed SL/TP, no trailing
    grid.append({'method': 'baseline'})

    # 1. Fixed-distance trailing (mirrors current VPS TRAIL_MULT logic)
    #    trail_dist = original_sl_dist * trail_mult
    #    keep_tp: True = TP acts as profit cap; False = rely purely on trail
    for tm in [0.5, 0.75, 1.0, 1.5, 2.0]:
        grid.append({'method': 'fixed_trail', 'trail_mult': tm, 'keep_tp': True})
        grid.append({'method': 'fixed_trail', 'trail_mult': tm, 'keep_tp': False})

    # 2. ATR-adaptive trailing
    #    trail_dist = ATR14[current_bar] * atr_trail_mult
    #    Widens in volatile conditions, tightens in calm periods.
    #    trail_start_mult: trail only activates when profit >= ATR * trail_start_mult.
    #      0.0 = immediate (v4 behavior), same value as atr_trail_mult = BE-equivalent.
    for atm in [0.5, 0.75, 1.0, 1.5, 2.0]:
        grid.append({'method': 'atr_trail', 'atr_trail_mult': atm, 'trail_start_mult': 0.0, 'keep_tp': True})
        grid.append({'method': 'atr_trail', 'atr_trail_mult': atm, 'trail_start_mult': 0.0, 'keep_tp': False})
    # trail_start_mult = atr_trail_mult -> first update at BE (v4.2 default)
    for atm in [0.5, 0.75, 1.0, 1.5, 2.0]:
        grid.append({'method': 'atr_trail', 'atr_trail_mult': atm, 'trail_start_mult': atm, 'keep_tp': True})
        grid.append({'method': 'atr_trail', 'atr_trail_mult': atm, 'trail_start_mult': atm, 'keep_tp': False})

    # 3. Slope reversal exit
    #    Force-close when SMA_long direction reverses for N consecutive bars.
    #    Earlier signal than waiting for price to cross SMA_long.
    for seb in [1, 2, 3, 5]:
        grid.append({'method': 'slope_exit', 'slope_exit_bars': seb})

    # 4. Divergence-based trail tighten (momentum exhaustion signal)
    #    When |SMA_short - SMA_long| / SMA_long > div_exit_th%:
    #      price has moved far from MA => momentum likely exhausted
    #      switch to tight trailing (sl_dist * tight_mult) to lock profits
    for det, tm2 in itertools.product([0.5, 1.0, 1.5, 2.0], [0.3, 0.5, 0.7]):
        grid.append({'method': 'div_tighten', 'div_exit_th': det, 'tight_mult': tm2})

    # 5. Combined: ATR-adaptive trail AND slope reversal exit (dual trigger)
    for atm, seb in itertools.product([0.75, 1.0, 1.5], [2, 3, 5]):
        grid.append({'method': 'combined',
                     'atr_trail_mult': atm, 'trail_start_mult': 0.0, 'slope_exit_bars': seb, 'keep_tp': True})
        grid.append({'method': 'combined',
                     'atr_trail_mult': atm, 'trail_start_mult': 0.0, 'slope_exit_bars': seb, 'keep_tp': False})
        grid.append({'method': 'combined',
                     'atr_trail_mult': atm, 'trail_start_mult': atm, 'slope_exit_bars': seb, 'keep_tp': True})
        grid.append({'method': 'combined',
                     'atr_trail_mult': atm, 'trail_start_mult': atm, 'slope_exit_bars': seb, 'keep_tp': False})

    return grid


# ── Data loading ───────────────────────────────────────────────────────────────────────────────
def load_csv(pair, tf):
    """Load 1h OHLC CSV from the repo data dir; resample to 4h by fixed 4-bar
    blocks to mirror the live resample('4h').

    Columns come in mixed case across legacy/new rows; they are lower-cased and
    de-duplicated. dropna(subset=['close']) yields the effective common period
    (~2024-04-24 .. 2026-04-24). high/low are required for intrabar exit checks.
    """
    path = os.path.join(DATA_DIR, f'{pair}_1h.csv')
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0)
    df.columns = [c.lower().strip() for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    need = ['open', 'high', 'low', 'close']
    if not all(c in df.columns for c in need):
        return None
    df = df[need].dropna(subset=['close']).reset_index(drop=True)
    if tf == '4h':
        idx = df.index // 4
        df = df.groupby(idx).agg({'open': 'first', 'high': 'max',
                                  'low': 'min', 'close': 'last'}).reset_index(drop=True)
    return df


# ── Indicators ────────────────────────────────────────────────────────────────────────────────────
def calc_atr14(df):
    hl  = df['high'] - df['low']
    hc  = (df['high'] - df['close'].shift()).abs()
    lc  = (df['low']  - df['close'].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def calc_adx14(df):
    high, low, close = df['high'], df['low'], df['close']
    pdm  = high.diff()
    ndm  = low.diff().mul(-1)
    pdm  = pdm.where((pdm > ndm) & (pdm > 0), 0.0)
    ndm  = ndm.where((ndm > pdm) & (ndm > 0), 0.0)
    hl   = high - low
    hc   = (high - close.shift()).abs()
    lc   = (low  - close.shift()).abs()
    tr   = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr  = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    pdi  = 100 * pdm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    ndi  = 100 * ndm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()


def calc_max_dd(equity):
    eq   = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd   = float((eq - peak).min())
    return -dd if dd < 0 else 0.0


# ── Core backtest ─────────────────────────────────────────────────────────────────────────────────
def run_backtest_exit(df, cfg, exit_params, spread=0.0):
    """
    Bar-by-bar backtest with pluggable exit strategy.

    Entry logic: identical to sma_squeeze_bt.py (ADX filter, squeeze ratio,
                 slope monotonicity, SMA crossover confirmation), executed at the
                 NEXT bar open + half-spread (mirrors live execution).

    Exit flow per bar (priority order, check-then-update):
      1. SL / trailing-stop hit  -- INTRABAR via bar low/high
      2. Fixed TP hit            -- INTRABAR via bar high/low (if keep_tp)
      3. Force-close: price crosses SMA_long in opposite direction (bar close)
      4. Slope exit: SMA_long slope reverses for N bars (slope_exit, combined; bar close)
      5. If still open: ratchet trailing SL from the bar CLOSE (effective next bar)
         (fixed_trail, atr_trail, div_tighten, combined)

    A resting stop fills the instant price touches it, so steps 1-2 use the bar
    range, not the close. Half the spread is paid on entry and on each exit.

    Parameters
    ----------
    df          : OHLC DataFrame
    cfg         : entry params dict (sma_short, sma_long, ...)
    exit_params : exit method dict, e.g. {'method': 'atr_trail', 'atr_trail_mult': 1.0, 'keep_tp': True}
    spread      : round-trip spread in price units (half paid on entry, half on exit)
    """
    method           = exit_params.get('method', 'baseline')
    trail_mult       = exit_params.get('trail_mult', 1.0)
    atr_trail_mult   = exit_params.get('atr_trail_mult', 1.0)
    trail_start_mult = exit_params.get('trail_start_mult', 0.0)
    keep_tp          = exit_params.get('keep_tp', True)
    slope_exit_bars  = exit_params.get('slope_exit_bars', 3)
    div_exit_th      = exit_params.get('div_exit_th', 1.0)
    tight_mult       = exit_params.get('tight_mult', 0.5)

    sma_short    = cfg['sma_short']
    sma_long     = cfg['sma_long']
    squeeze_th   = cfg['squeeze_th']
    slope_period = cfg['slope_period']
    rr           = cfg['rr']
    sl_atr_mult  = cfg['sl_atr_mult']

    close_a = df['close'].values
    open_a  = df['open'].values
    high_a  = df['high'].values
    low_a   = df['low'].values
    sma_s   = df['close'].rolling(sma_short).mean().values
    sma_l   = df['close'].rolling(sma_long).mean().values
    atr14   = calc_atr14(df).values
    adx14   = calc_adx14(df).values
    n       = len(df)
    half    = spread / 2.0

    warmup   = max(sma_long, slope_period, 28) + 2
    trades   = []
    equity   = [0.0]
    in_trade = False

    # trade state
    t_dir        = ''
    t_entry      = 0.0
    t_sl_dist    = 0.0   # original SL distance at entry (always positive)
    t_tp_dist    = 0.0   # original TP distance (always positive)
    t_current_sl = 0.0   # current (possibly trailed) SL level

    for i in range(warmup, n):
        c     = close_a[i]
        o     = open_a[i]
        hi    = high_a[i]
        lo    = low_a[i]
        sl_v  = sma_l[i]
        ss_v  = sma_s[i]
        atr_v = atr14[i]
        adx_v = adx14[i]

        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            continue

        # ── Manage open position ──────────────────────────────────────────────────────
        if in_trade:
            pnl  = None
            is_long = (t_dir == 'long')

            # Priority 1: SL / trailing-stop hit -- INTRABAR via bar low/high.
            #   A resting stop fills the moment price touches it; testing only the
            #   close misses these stop-outs and inflates PF (the bug being fixed).
            #   pnl can be positive when the trail has ratcheted into profit.
            if is_long and lo <= t_current_sl:
                pnl = (t_current_sl - t_entry) - half
            elif (not is_long) and hi >= t_current_sl:
                pnl = (t_entry - t_current_sl) - half

            # Priority 2: Fixed TP hit -- INTRABAR via bar high/low.
            if pnl is None and keep_tp:
                if is_long and hi >= t_entry + t_tp_dist:
                    pnl = t_tp_dist - half
                elif (not is_long) and lo <= t_entry - t_tp_dist:
                    pnl = t_tp_dist - half

            # Priority 3: SMA_long price break (force-close) -- bar close.
            if pnl is None and ((is_long and c < sl_v) or (not is_long and c > sl_v)):
                pnl = ((c - t_entry) if is_long else (t_entry - c)) - half

            # Priority 4: Slope reversal exit -- bar close.
            if pnl is None and method in ('slope_exit', 'combined'):
                seg_start = i - slope_exit_bars + 1
                if seg_start >= 0:
                    seg   = sma_l[seg_start: i + 1]
                    diffs = np.diff(seg)
                    if not np.any(np.isnan(seg)):
                        reversed_slope = (is_long and bool(np.all(diffs < 0))) or \
                                         (not is_long and bool(np.all(diffs > 0)))
                        if reversed_slope:
                            pnl = ((c - t_entry) if is_long else (t_entry - c)) - half

            # Record closed trade
            if pnl is not None:
                trades.append(pnl)
                equity.append(equity[-1] + pnl)
                in_trade = False
                # Fall through to entry check (no `continue` -- same-bar re-entry possible)
            else:
                # Priority 5: ratchet trailing SL from the bar CLOSE (effective next bar)
                if method == 'fixed_trail':
                    td = t_sl_dist * trail_mult
                    if is_long:
                        t_current_sl = max(t_current_sl, c - td)
                    else:
                        t_current_sl = min(t_current_sl, c + td)

                elif method in ('atr_trail', 'combined'):
                    # trail_start_mult: hold trail until profit >= ATR * trail_start_mult
                    profit_now = (c - t_entry) if is_long else (t_entry - c)
                    if trail_start_mult <= 0.0 or profit_now >= atr_v * trail_start_mult:
                        td = atr_v * atr_trail_mult
                        if is_long:
                            t_current_sl = max(t_current_sl, c - td)
                        else:
                            t_current_sl = min(t_current_sl, c + td)

                elif method == 'div_tighten':
                    div_rate = abs(ss_v - sl_v) / abs(sl_v) * 100.0 if sl_v != 0.0 else 0.0
                    if div_rate > div_exit_th:
                        td = t_sl_dist * tight_mult
                        if is_long:
                            t_current_sl = max(t_current_sl, c - td)
                        else:
                            t_current_sl = min(t_current_sl, c + td)
                # baseline & slope_exit: SL stays fixed (no trailing)

                continue   # still in trade, skip entry

        # ── Entry checks ─────────────────────────────────────────────────────────────────────────
        if adx_v <= 20.0:
            continue

        div_rate_entry = abs(ss_v - sl_v) / sl_v * 100.0 if sl_v != 0.0 else 999.0
        if div_rate_entry > squeeze_th:
            continue

        slp_start = i - slope_period + 1
        if slp_start < 0:
            continue
        slp   = sma_l[slp_start: i + 1]
        if np.any(np.isnan(slp)):
            continue
        diffs  = np.diff(slp)
        rising  = bool(np.all(diffs > 0))
        falling = bool(np.all(diffs < 0))
        if not (rising or falling):
            continue

        prev_c = close_a[i - 1]
        prev_s = sma_s[i - 1]
        if np.isnan(prev_s) or np.isnan(prev_c):
            continue

        sl_dist = atr_v * sl_atr_mult
        tp_dist = sl_dist * rr
        if sl_dist == 0.0:
            continue

        direction = None
        if rising  and c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            direction = 'long'
        elif falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            direction = 'short'

        if direction is None:
            continue

        # Open trade at NEXT bar open + half-spread (live execution)
        if i + 1 >= n:
            continue
        is_long_new  = (direction == 'long')
        entry_px     = open_a[i + 1] + half * (1 if is_long_new else -1)
        t_dir        = direction
        t_entry      = entry_px
        t_sl_dist    = sl_dist
        t_tp_dist    = tp_dist
        # Initial SL level
        t_current_sl = (entry_px - sl_dist) if is_long_new else (entry_px + sl_dist)
        in_trade     = True

    # End-of-data: close any open position at last close
    if in_trade:
        c   = close_a[-1]
        pnl = ((c - t_entry) if t_dir == 'long' else (t_entry - c)) - half
        trades.append(pnl)
        equity.append(equity[-1] + pnl)

    if len(trades) < MIN_TRADES:
        return None

    arr  = np.array(trades)
    wins = arr[arr > 0]
    loss = arr[arr <= 0]
    gp   = float(wins.sum())  if len(wins) > 0 else 0.0
    gl   = float(abs(loss.sum())) if len(loss) > 0 else 0.0
    pf   = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)

    # Average trade metrics
    avg_win  = float(wins.mean())  if len(wins) > 0 else 0.0
    avg_loss = float(abs(loss.mean())) if len(loss) > 0 else 0.0
    rr_real  = avg_win / avg_loss if avg_loss > 0 else 0.0

    return {
        'PF':       round(pf,   3),
        'win_rate': round(len(wins) / len(arr), 3),
        'n_trades': len(arr),
        'max_dd':   round(calc_max_dd(equity), 6),
        'avg_win':  round(avg_win, 6),
        'avg_loss': round(avg_loss, 6),
        'real_rr':  round(rr_real, 3),
    }


# ── Main ────────────────────────────────────────────────────────────────────────────────────────────
def main():
    exit_grid = build_exit_grid()
    total = len(PAIRS_CFG) * len(exit_grid)
    print(f'[INFO] {len(PAIRS_CFG)} pairs x {len(exit_grid)} exit configs = {total} runs')

    # Load data
    df_cache = {}
    for pair, cfg in PAIRS_CFG.items():
        tf = cfg['timeframe']
        df = load_csv(pair, tf)
        df_cache[pair] = df
        status = f'{len(df)} rows' if df is not None else 'NOT FOUND'
        print(f'  {pair} {tf}: {status}')

    all_results = []
    baseline_pf = {}   # store baseline PF per pair for delta calculation

    print()
    for pair, cfg in PAIRS_CFG.items():
        df = df_cache.get(pair)
        if df is None:
            print(f'[SKIP] {pair}: no data')
            continue

        sp = SPREAD.get(pair, 0.0)
        pair_rows = []
        for ep in exit_grid:
            m = run_backtest_exit(df, cfg, ep, spread=sp)
            if m is None:
                continue
            row = {
                'pair':             pair,
                'method':           ep['method'],
                'trail_mult':       ep.get('trail_mult', ''),
                'atr_trail_mult':   ep.get('atr_trail_mult', ''),
                'trail_start_mult': ep.get('trail_start_mult', ''),
                'keep_tp':          ep.get('keep_tp', ''),
                'slope_exit_bars':  ep.get('slope_exit_bars', ''),
                'div_exit_th':      ep.get('div_exit_th', ''),
                'tight_mult':       ep.get('tight_mult', ''),
                'PF':               m['PF'],
                'win_rate':         m['win_rate'],
                'n_trades':         m['n_trades'],
                'max_dd':           m['max_dd'],
                'avg_win':          m['avg_win'],
                'avg_loss':         m['avg_loss'],
                'real_rr':          m['real_rr'],
            }
            if ep['method'] == 'baseline':
                baseline_pf[pair] = m['PF']
            pair_rows.append(row)
            all_results.append(row)

        base = baseline_pf.get(pair, 0.0)
        pair_rows.sort(key=lambda x: x['PF'], reverse=True)

        print(f'=== {pair}  baseline PF={base:.3f} ===')
        print(f'  {"Method":<20} {"Params":<35} {"PF":>6} {"WR":>6} {"n":>4} {"realRR":>7} {"dPF":>7}')
        print(f'  {"-"*20} {"-"*35} {"-"*6} {"-"*6} {"-"*4} {"-"*7} {"-"*7}')
        for r in pair_rows[:12]:
            params = _fmt_params(r)
            dpf    = r['PF'] - base
            mark   = ' *' if dpf > 0.1 else ''
            print(f'  {r["method"]:<20} {params:<35} {r["PF"]:>6.3f} {r["win_rate"]:>6.1%} '
                  f'{r["n_trades"]:>4} {r["real_rr"]:>7.2f} {dpf:>+7.3f}{mark}')
        print()

    if all_results:
        df_out = (pd.DataFrame(all_results)
                    .sort_values(['pair', 'PF'], ascending=[True, False])
                    .reset_index(drop=True))
        df_out.to_csv(OUTPUT_CSV, index=False)
        print(f'[INFO] {len(df_out)} rows -> {OUTPUT_CSV}')

    # ── Cross-pair winner summary ──────────────────────────────────────────────────────────────────
    if all_results:
        print('\n=== WINNER PER PAIR (top exit method) ===')
        for pair in PAIRS_CFG:
            rows = [r for r in all_results if r['pair'] == pair]
            if not rows:
                continue
            rows.sort(key=lambda x: x['PF'], reverse=True)
            base = baseline_pf.get(pair, 0.0)
            w    = rows[0]
            print(f'  {pair}: {w["method"]} {_fmt_params(w)}  '
                  f'PF={w["PF"]:.3f} (+{w["PF"]-base:.3f} vs baseline)')


def _fmt_params(r):
    parts = []
    if r['trail_mult'] != '':
        parts.append(f'trail={r["trail_mult"]}')
    if r['atr_trail_mult'] != '':
        parts.append(f'atr_mult={r["atr_trail_mult"]}')
    if r.get('trail_start_mult', '') != '':
        parts.append(f'start={r["trail_start_mult"]}')
    if r['keep_tp'] != '':
        parts.append(f'tp={"Y" if r["keep_tp"] else "N"}')
    if r['slope_exit_bars'] != '':
        parts.append(f'slope={r["slope_exit_bars"]}')
    if r['div_exit_th'] != '':
        parts.append(f'div={r["div_exit_th"]}')
    if r['tight_mult'] != '':
        parts.append(f'tight={r["tight_mult"]}')
    return ' '.join(parts) if parts else '-'


if __name__ == '__main__':
    main()
