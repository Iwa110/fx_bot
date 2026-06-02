"""
grid_floatstop_sweep.py - Joint optimization INCLUDING the float_stop value.

grid_param_sweep.py held float_stop at each pair's live value and swept
ci/atr/max_levels. This script adds float_stop to the joint grid and re-checks,
with IS/OOS robustness, whether:
  (a) a better float_stop exists than the live / v6 value, and
  (b) freeing float_stop changes the best ci/atr/max_levels.

float_stop is pair-specific (scales with grid_width*lot). Per-pair JPY ranges
chosen around each pair's gw/level (GBPJPY~50k, CHFJPY~54k, NZDJPY~17.6k,
AUDCAD~20k per level at 1.0 lot).

Ranking: net>0 & PF>=1.2 in BOTH IS/OOS halves, loss control exercised
(n_b48+n_fstop>0 to exclude never-lost overfits), n>=40. Rank by min(IS,OOS) PF.

Usage:
  python grid_floatstop_sweep.py
"""

from pathlib import Path
from itertools import product

import pandas as pd

import grid_floatstop_bt as G

OUTPUT_CSV = str(Path(__file__).parent / 'grid_floatstop_sweep_result.csv')

# Base (lot, quote_jpy) per pair; v6 reference config for comparison.
BASE = {
    'GBPJPY': dict(lot=1.0, quote_jpy=1.0,   v6=dict(ci=61.8, atr=1.5, lv=7, fs=-1_500_000.0)),
    'CHFJPY': dict(lot=1.0, quote_jpy=1.0,   v6=dict(ci=65.0, atr=1.0, lv=3, fs=-1_500_000.0)),
    'NZDJPY': dict(lot=1.0, quote_jpy=1.0,   v6=dict(ci=65.0, atr=1.5, lv=5, fs=-500_000.0)),
    'AUDCAD': dict(lot=1.0, quote_jpy=108.0, v6=dict(ci=65.0, atr=1.0, lv=3, fs=-500_000.0)),
}

CI_GRID = [61.8, 65.0, 70.0]
ATR_GRID = [1.0, 1.5, 2.0, 3.0]
LVL_GRID = [3, 5, 7]
# float_stop ranges per pair (JPY, 1.0 lot). Tight -> loose.
FS_GRID = {
    'GBPJPY': [-500_000.0, -750_000.0, -1_000_000.0, -1_500_000.0, -2_000_000.0, -3_000_000.0],
    'CHFJPY': [-300_000.0, -500_000.0, -750_000.0, -1_000_000.0, -1_500_000.0, -2_000_000.0],
    'NZDJPY': [-150_000.0, -250_000.0, -350_000.0, -500_000.0, -750_000.0, -1_000_000.0],
    'AUDCAD': [-150_000.0, -250_000.0, -350_000.0, -500_000.0, -750_000.0, -1_000_000.0],
}
B48 = 48


def split(df):
    mid = df.index[0] + (df.index[-1] - df.index[0]) / 2
    return df[df.index < mid], df[df.index >= mid]


def ev(pair, cfg, df, atr_full, ci_full):
    return G.run_backtest(pair, cfg, df, atr_full.reindex(df.index), ci_full.reindex(df.index))


def main():
    rows = []
    for pair, base in BASE.items():
        df = G.load_data(pair)
        atr_full = G.compute_atr_series(df)
        ci_full = G.compute_ci_series(df)
        df_is, df_oos = split(df)
        print(f'\n=== {pair} === IS {df_is.index[0].date()}~{df_is.index[-1].date()} | '
              f'OOS {df_oos.index[0].date()}~{df_oos.index[-1].date()}')

        results = []
        for ci_th, atr_m, lvl, fs in product(CI_GRID, ATR_GRID, LVL_GRID, FS_GRID[pair]):
            cfg = dict(lot=base['lot'], quote_jpy=base['quote_jpy'],
                       ci_threshold=ci_th, atr_mult=atr_m, max_levels=lvl,
                       float_stop=fs, b48_hours=B48)
            ris = ev(pair, cfg, df_is, atr_full, ci_full)
            roos = ev(pair, cfg, df_oos, atr_full, ci_full)
            rec = dict(pair=pair, ci_th=ci_th, atr_mult=atr_m, max_levels=lvl, float_stop=fs,
                       is_pf=ris['pf'], is_net=ris['total_pnl'], is_ntp=ris['n_tp'],
                       is_lev=ris['n_b48'] + ris['n_fstop'],
                       oos_pf=roos['pf'], oos_net=roos['total_pnl'], oos_ntp=roos['n_tp'],
                       oos_lev=roos['n_b48'] + roos['n_fstop'],
                       oos_worst=roos['worst_event'], oos_dd=roos['max_dd'])
            results.append(rec); rows.append(rec)

        # v6 reference (full-period evaluated, for context)
        v6 = base['v6']
        v6cfg = dict(lot=base['lot'], quote_jpy=base['quote_jpy'], ci_threshold=v6['ci'],
                     atr_mult=v6['atr'], max_levels=v6['lv'], float_stop=v6['fs'], b48_hours=B48)
        rfull = ev(pair, v6cfg, df, atr_full, ci_full)
        print(f'  v6   ci={v6["ci"]} atr={v6["atr"]} lv={v6["lv"]} fs={v6["fs"]:,.0f}: '
              f'FULL PF={rfull["pf"]:.2f} net={rfull["total_pnl"]:,.0f} '
              f'worst={rfull["worst_event"]:,.0f} dd={rfull["max_dd"]:,.0f}')

        def keyr(r):
            i = 99 if r['is_pf'] == float('inf') else r['is_pf']
            o = 99 if r['oos_pf'] == float('inf') else r['oos_pf']
            return min(i, o)
        cand = [r for r in results
                if r['is_net'] > 0 and r['oos_net'] > 0
                and r['is_pf'] >= 1.2 and r['oos_pf'] >= 1.2
                and (r['is_ntp'] + r['oos_ntp']) >= 40
                and (r['is_lev'] + r['oos_lev']) >= 1]
        cand.sort(key=keyr, reverse=True)
        print('  --- top configs incl. float_stop (robust IS/OOS, loss-tested) ---')
        if not cand:
            print('    (none)')
        for r in cand[:6]:
            ip = 'inf' if r['is_pf'] == float('inf') else f'{r["is_pf"]:.2f}'
            op = 'inf' if r['oos_pf'] == float('inf') else f'{r["oos_pf"]:.2f}'
            print(f'    ci={r["ci_th"]:<5} atr={r["atr_mult"]:<4} lv={r["max_levels"]} '
                  f'fs={r["float_stop"]:>11,.0f} -> '
                  f'IS PF={ip}/n={r["is_ntp"]} | OOS PF={op}/n={r["oos_ntp"]} '
                  f'lev={r["is_lev"]+r["oos_lev"]} OOSworst={r["oos_worst"]:,.0f} OOSdd={r["oos_dd"]:,.0f}')

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved: {OUTPUT_CSV}  ({len(rows)} combos x IS/OOS)')


if __name__ == '__main__':
    main()
