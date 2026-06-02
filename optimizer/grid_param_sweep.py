"""
grid_param_sweep.py - Grid parameter optimization given B48-never-fires finding.

Context: in the 2yr float-stop BT, B48 (48h timer) fired n=0 for every pair -
float-stop always triggers first. So loss control is entirely float-stop and the
time-exit is dead. This sweep re-optimizes the levers that actually matter:

  - ci_threshold : entry filter (higher = only trade choppier/ranging regimes,
                   which is where grid wins; should avoid trend-driven float-stops)
  - atr_mult     : grid width
  - max_levels   : ladder depth (fewer = less accumulation before float-stop)
  - b48_hours    : test whether SHORTENING the timer makes time-exit fire before
                   float-stop in slow adverse grinds (24/36/48h)

float_stop kept at each pair's live FLOAT_STOP_PER_PAIR value.

ATR/CI series are computed once per pair; run_backtest is cfg-driven so every
combo reuses them.

Usage:
  python grid_param_sweep.py
"""

from pathlib import Path
from itertools import product

import pandas as pd

import grid_floatstop_bt as G

OUTPUT_CSV = str(Path(__file__).parent / 'grid_param_sweep_result.csv')

# Live baseline (from grid_monitor.py) for reference rows.
BASE = {
    'GBPJPY': dict(atr_mult=1.5, max_levels=7, float_stop=-1_500_000.0, lot=1.0, quote_jpy=1.0),
    'CHFJPY': dict(atr_mult=2.0, max_levels=7, float_stop=-1_500_000.0, lot=1.0, quote_jpy=1.0),
    'NZDJPY': dict(atr_mult=1.0, max_levels=7, float_stop=-500_000.0, lot=1.0, quote_jpy=1.0),
    'AUDCAD': dict(atr_mult=1.0, max_levels=7, float_stop=-500_000.0, lot=1.0, quote_jpy=108.0),
}

CI_GRID = [55.0, 60.0, 61.8, 65.0, 70.0]
ATR_GRID = [1.0, 1.5, 2.0, 2.5, 3.0]
LVL_GRID = [3, 5, 7]
B48_GRID = [24, 36, 48]


def main():
    rows = []
    for pair, base in BASE.items():
        df = G.load_data(pair)
        atr_series = G.compute_atr_series(df)
        ci_series = G.compute_ci_series(df)
        print(f'\n=== {pair} === ({df.index[0].date()}~{df.index[-1].date()}, {len(df)} bars)')

        for ci_th, atr_m, lvl, b48 in product(CI_GRID, ATR_GRID, LVL_GRID, B48_GRID):
            cfg = dict(base)
            cfg.update(ci_threshold=ci_th, atr_mult=atr_m, max_levels=lvl,
                       b48_hours=b48)
            res = G.run_backtest(pair, cfg, df, atr_series, ci_series)
            rows.append(dict(
                pair=pair, ci_th=ci_th, atr_mult=atr_m, max_levels=lvl, b48_h=b48,
                pf=res['pf'], total=res['total_pnl'], n_tp=res['n_tp'],
                n_b48=res['n_b48'], n_fstop=res['n_fstop'],
                worst=res['worst_event'], max_dd=res['max_dd'],
            ))

        # report: baseline row + top-5 by PF (require net>0 and >=20 TP for robustness)
        sub = [r for r in rows if r['pair'] == pair]
        b = base
        base_row = next((r for r in sub if r['ci_th'] == 61.8 and r['atr_mult'] == b['atr_mult']
                         and r['max_levels'] == b['max_levels'] and r['b48_h'] == 48), None)
        if base_row:
            print(f'  LIVE  ci=61.8 atr={b["atr_mult"]} lv={b["max_levels"]} b48=48 -> '
                  f'PF={base_row["pf"]:.3f} net={base_row["total"]:>12,.0f} '
                  f'n_tp={base_row["n_tp"]} n_b48={base_row["n_b48"]} '
                  f'n_fs={base_row["n_fstop"]} maxDD={base_row["max_dd"]:,.0f}')
        cand = [r for r in sub if r['total'] > 0 and r['n_tp'] >= 20]
        cand.sort(key=lambda r: (-r['pf'], r['max_dd']))
        print('  --- top configs (net>0, n_tp>=20) by PF ---')
        for r in cand[:5]:
            print(f'  ci={r["ci_th"]:<5} atr={r["atr_mult"]:<4} lv={r["max_levels"]} b48={r["b48_h"]:<3} '
                  f'-> PF={r["pf"]:.3f} net={r["total"]:>12,.0f} '
                  f'n_tp={r["n_tp"]:<4} n_b48={r["n_b48"]} n_fs={r["n_fstop"]} '
                  f'worst={r["worst"]:>11,.0f} maxDD={r["max_dd"]:>11,.0f}')
        if not cand:
            print('  (no net-positive config found)')

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved: {OUTPUT_CSV}  ({len(rows)} combos)')


if __name__ == '__main__':
    main()
