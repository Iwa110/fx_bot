"""mc_capital.py - month-block-bootstrap required capital (Step B, adapted).

Method mirrors optimizer/grid_stepb_recompute.py (itself following
grid_sizing_ruin.py): monthly realized PnL is block-bootstrapped
(default block=3 months, N_MC=20000, horizon=60 months) to get a maxDD
distribution; required capital = max(MC maxDD at the 99th percentile,
worst single forced-exit event inflated by a gap/slippage buffer).

Important methodology note (carried over from CLAUDE.md 2026-06-15 entry):
monthly PnL must be reindexed onto the FULL CALENDAR MONTH RANGE with idle
months filled as 0 before bootstrapping - grid_floatstop_bt.py's `monthly`
dict only contains months where a close actually happened, so using it
as-is compresses the timeline and overstates activity / understates
idle-month drag. calendar_reindex() below does this.

This module reads the core BT's *output* dicts (monthly / fs_events /
b48_events) - it does not import or modify grid_floatstop_bt.py itself.
"""

import numpy as np
import pandas as pd


def calendar_reindex(monthly_dict):
    """monthly_dict: {'YYYY-MM': pnl}. Returns a numpy array covering every
    calendar month from the first to the last key, zero-filled for idle
    months, in chronological order."""
    if not monthly_dict:
        return np.array([])
    keys = sorted(monthly_dict)
    start = pd.Period(keys[0], freq='M')
    end = pd.Period(keys[-1], freq='M')
    full_range = pd.period_range(start, end, freq='M')
    return np.array([monthly_dict.get(str(p), 0.0) for p in full_range], dtype=float)


def block_bootstrap_maxdd(monthly_arr, horizon_months, n_mc, block, seed=42):
    rng = np.random.default_rng(seed)
    n = len(monthly_arr)
    if n < block:
        raise ValueError(f'monthly series too short ({n} months) for block={block}')
    n_blocks = int(np.ceil(horizon_months / block))
    maxdds = np.empty(n_mc)
    finals = np.empty(n_mc)
    starts = rng.integers(0, n - block + 1, size=(n_mc, n_blocks))
    for i in range(n_mc):
        seq = np.concatenate([monthly_arr[s:s + block] for s in starts[i]])[:horizon_months]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        dd = peak[1:] - eq
        maxdds[i] = dd.max()
        finals[i] = eq[-1]
    return maxdds, finals


def required_capital(monthly_dict, single_event_pnls, cfg, gap_buffer=None):
    """single_event_pnls: iterable of per-event realized PnL for forced exits
    (float-stop / B48 events, JPY). Only negative values matter for the
    worst-single-loss floor.

    Returns dict with req_cap_99/999, mc_dd_med/99/999, worst_single,
    worst_gap, p_loss_5yr (probability the 60mo bootstrap path ends
    negative), ruin_at_req99.
    """
    mc_cfg = cfg['mc_capital']
    gap_buffer = gap_buffer if gap_buffer is not None else mc_cfg['gap_buffer_default']

    monthly_arr = calendar_reindex(monthly_dict)
    maxdds, finals = block_bootstrap_maxdd(
        monthly_arr, mc_cfg['horizon_months'], mc_cfg['n_mc'], mc_cfg['block_months'], mc_cfg['seed'])

    dd_med = float(np.percentile(maxdds, 50))
    dd99 = float(np.percentile(maxdds, 99))
    dd999 = float(np.percentile(maxdds, 99.9))

    singles = np.array([p for p in single_event_pnls if p < 0], dtype=float)
    worst_single = float(-singles.min()) if len(singles) else 0.0
    worst_gap = worst_single * gap_buffer

    req99 = max(dd99, worst_gap)
    req999 = max(dd999, worst_gap)
    ruin_at_req99 = float((maxdds > req99).mean())
    p_loss_5yr = float((finals < 0).mean())

    return {
        'n_months': len(monthly_arr),
        'mc_dd_med': round(dd_med, 0), 'mc_dd99': round(dd99, 0), 'mc_dd999': round(dd999, 0),
        'worst_single': round(worst_single, 0), 'gap_buffer': gap_buffer,
        'worst_gap': round(worst_gap, 0),
        'req_cap_99': round(req99, 0), 'req_cap_999': round(req999, 0),
        'ruin_at_req99': round(ruin_at_req99, 4), 'p_loss_5yr': round(p_loss_5yr, 4),
    }
