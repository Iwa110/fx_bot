"""
test_grid_floatstop_static.py - Static-equivalence guard for grid_floatstop_bt.py

Purpose (prep task #1 of the grid AI-loop design, see
grid_loop_engineering_design.md sec 7.5): the core BT engine gains new
features (partial take-profit, ladder-depth asymmetric TP). This test freezes
the engine's behaviour BEFORE the change and asserts, after the change, that:

  1. [OFF]     with the new features absent from cfg (legacy configs), every
               metric is identical to the frozen baseline;
  2. [NEUTRAL] with the new features explicitly set to neutral values
               (tp_mult=1.0 / tp_level_mults all 1.0 / ptp off), results are
               still identical to the baseline;
  3. [ON]      with the features actually enabled, they demonstrably fire
               (n_ptp > 0, results differ from baseline) and conserve basic
               invariants.

Baseline data: real 1h CSVs under data/ (the 5 PAIR_CONFIG pairs) plus a
deterministic synthetic series (seeded), so the guard still runs where data/
is absent.

Usage:
  python optimizer/test_grid_floatstop_static.py --freeze   # run ONCE with the
        pre-change engine to write grid_floatstop_static_baseline.json
  python optimizer/test_grid_floatstop_static.py            # verify current
        engine against the frozen baseline (exit 0 = pass)
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import grid_floatstop_bt as G

BASELINE_JSON = str(Path(__file__).parent / 'grid_floatstop_static_baseline.json')

# Frozen copy of the engine configs at freeze time. Deliberately NOT a
# reference to G.PAIR_CONFIG so later edits to the engine's own table cannot
# silently move the goalposts of this test.
FROZEN_CONFIGS = {
    'GBPJPY': {'atr_mult': 1.5, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'CHFJPY': {'atr_mult': 2.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'NZDJPY': {'atr_mult': 1.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -500_000.0, 'quote_jpy': 1.0},
    'AUDCAD': {'atr_mult': 1.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -500_000.0, 'quote_jpy': 108.0},
    'NZDUSD': {'atr_mult': 2.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 0.01, 'max_levels': 7, 'float_stop': -15_000.0, 'quote_jpy': 155.0},
}

SYN_CFG = {'atr_mult': 1.0, 'ci_threshold': 61.8, 'b48_hours': 48,
           'lot': 1.0, 'max_levels': 5, 'float_stop': -500_000.0, 'quote_jpy': 1.0}

REL_TOL = 1e-9
ABS_TOL = 1e-6


def build_synthetic_df(seed=42, n_bars=18000, p0=150.0):
    """Deterministic hourly OHLC: random walk with alternating calm (range)
    and trending segments so the CI gate opens AND float-stops fire."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range('2020-01-01', periods=n_bars, freq='h', tz='UTC')
    seg = 24 * 30                                    # ~monthly regime blocks
    drift = np.repeat(rng.choice([-0.02, 0.0, 0.0, 0.0, 0.02],
                                 size=n_bars // seg + 1), seg)[:n_bars]
    vol = np.repeat(rng.choice([0.05, 0.10, 0.20],
                               size=n_bars // seg + 1), seg)[:n_bars]
    rets = drift + rng.standard_normal(n_bars) * vol
    close = p0 + np.cumsum(rets)
    close = np.maximum(close, 10.0)
    open_ = np.concatenate([[p0], close[:-1]])
    wick = np.abs(rng.standard_normal(n_bars)) * vol * 0.6
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close},
                        index=idx)


def load_data(pair):
    """G.load_data with a fallback for pandas>=3.0 (mixed tz strings in the
    CSVs raise under strict parsing; the engine on the VPS runs older pandas)."""
    try:
        df = G.load_data(pair)
    except ValueError:
        path = os.path.join(G.DATA_DIR, pair + '_1h.csv')
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True, format='mixed')
        df = df[['open', 'high', 'low', 'close']].sort_index().dropna()
    # De-dup timestamps: with duplicates, Series.get(ts) inside the engine
    # returns a sub-Series and the run is ill-defined either way.
    return df[~df.index.duplicated(keep='last')]


def serialize_result(res):
    out = {}
    for k, v in res.items():
        if k == 'monthly':
            out[k] = {mk: float(mv) for mk, mv in sorted(v.items())}
        elif k in ('fs_events', 'b48_events'):
            out[k] = [float(x) for x in v]
        elif isinstance(v, (int, np.integer)):
            out[k] = int(v)
        else:
            out[k] = float(v)
    return out


def run_case(pair, cfg, df):
    atr_series = G.compute_atr_series(df)
    ci_series = G.compute_ci_series(df)
    return serialize_result(G.run_backtest(pair, cfg, df, atr_series, ci_series))


def collect_baseline_cases():
    """(name, cfg, df) for every case in the frozen baseline."""
    cases = []
    for pair, cfg in FROZEN_CONFIGS.items():
        try:
            df = load_data(pair)
        except FileNotFoundError:
            print(f'[skip] {pair}_1h.csv not found')
            continue
        cases.append((f'real:{pair}', pair, cfg, df))
    cases.append(('syn:BASE', 'SYNTH', SYN_CFG, build_synthetic_df()))
    return cases


def close_enough(a, b):
    return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)


def compare_result(name, got, want):
    errs = []
    for k in want:
        if k not in got:
            errs.append(f'{name}: missing key {k}')
            continue
        g, w = got[k], want[k]
        if k == 'monthly':
            if sorted(g) != sorted(w):
                errs.append(f'{name}: monthly keys differ')
            else:
                for mk in w:
                    if not close_enough(g[mk], w[mk]):
                        errs.append(f'{name}: monthly[{mk}] {g[mk]} != {w[mk]}')
        elif k in ('fs_events', 'b48_events'):
            if len(g) != len(w):
                errs.append(f'{name}: {k} length {len(g)} != {len(w)}')
            else:
                for i, (gi, wi) in enumerate(zip(g, w)):
                    if not close_enough(gi, wi):
                        errs.append(f'{name}: {k}[{i}] {gi} != {wi}')
        elif isinstance(w, int):
            if int(g) != w:
                errs.append(f'{name}: {k} {g} != {w}')
        else:
            if not close_enough(g, w):
                errs.append(f'{name}: {k} {g} != {w}')
    return errs


def freeze():
    baseline = {}
    for name, pair, cfg, df in collect_baseline_cases():
        print(f'[freeze] {name} ...')
        baseline[name] = run_case(pair, cfg, df)
    with open(BASELINE_JSON, 'w') as f:
        json.dump(baseline, f, indent=1, sort_keys=True)
    print(f'Baseline written: {BASELINE_JSON} ({len(baseline)} cases)')


def verify():
    with open(BASELINE_JSON) as f:
        baseline = json.load(f)

    errs = []
    cases = collect_baseline_cases()
    case_by_name = {name: (pair, cfg, df) for name, pair, cfg, df in cases}

    # 1. [OFF] legacy cfg (new keys absent) must reproduce baseline exactly.
    for name, (pair, cfg, df) in case_by_name.items():
        if name not in baseline:
            print(f'[warn] {name} not in baseline (data set changed?) - skipped')
            continue
        print(f'[verify OFF] {name} ...')
        errs += compare_result(f'OFF/{name}', run_case(pair, cfg, df), baseline[name])

    # 2. [NEUTRAL] features present but at neutral values must also reproduce
    #    baseline (guards against off-by-default drift in the engine).
    neut_name = 'syn:BASE'
    if neut_name in baseline:
        pair, cfg, df = case_by_name[neut_name]
        ncfg = dict(cfg)
        ncfg.update({'tp_mult': 1.0, 'tp_level_mults': [1.0] * cfg['max_levels'],
                     'ptp_frac': None, 'ptp_mult': 0.5})
        print(f'[verify NEUTRAL] {neut_name} ...')
        errs += compare_result(f'NEUTRAL/{neut_name}', run_case(pair, ncfg, df),
                               baseline[neut_name])

    # 3. [ON] features enabled must fire and differ from baseline.
    if neut_name in baseline:
        pair, cfg, df = case_by_name[neut_name]
        base = baseline[neut_name]

        on_ptp = dict(cfg); on_ptp.update({'ptp_frac': 0.5, 'ptp_mult': 0.5})
        print('[verify ON ptp] syn:BASE ...')
        r = run_case(pair, on_ptp, df)
        if r.get('n_ptp', 0) <= 0:
            errs.append('ON/ptp: n_ptp did not fire (expected > 0)')
        if close_enough(r['total_pnl'], base['total_pnl']):
            errs.append('ON/ptp: total_pnl identical to baseline (feature inert)')
        if r['n_tp'] > base['n_tp'] + base['skip_count'] + r.get('n_ptp', 0):
            errs.append('ON/ptp: implausible n_tp explosion')

        on_asym = dict(cfg); on_asym.update({'tp_level_mults': [1.0, 1.0, 0.8, 0.8, 0.6]})
        print('[verify ON asym-tp] syn:BASE ...')
        r = run_case(pair, on_asym, df)
        if close_enough(r['total_pnl'], base['total_pnl']) and r['n_tp'] == base['n_tp']:
            errs.append('ON/asym-tp: results identical to baseline (feature inert)')

    if errs:
        print('\nFAIL: static-equivalence violations:')
        for e in errs:
            print('  -', e)
        return 1
    print('\nPASS: engine is static-equivalent with new features OFF/NEUTRAL, '
          'and features fire when ON.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--freeze', action='store_true',
                    help='write baseline from the CURRENT engine (run pre-change only)')
    args = ap.parse_args()
    if args.freeze:
        freeze()
        return 0
    return verify()


if __name__ == '__main__':
    sys.exit(main())
