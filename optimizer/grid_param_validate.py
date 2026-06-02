"""
grid_param_validate.py - IS/OOS robustness check for grid param optimization.

The full-period sweep (grid_param_sweep.py) surfaced configs with PF=inf /
maxDD=0 / small n_tp. Those never took a single loss in 2yr = overfit risk.
This script splits the history in half (IS = first year, OOS = second year)
and ranks configs by robustness: net>0 AND PF>=1.2 in BOTH halves, preferring
configs whose loss control was actually exercised (n_b48+n_fstop > 0).

Reports per pair: LIVE config IS/OOS, and the most robust candidate.

Usage:
  python grid_param_validate.py
"""

from pathlib import Path
from itertools import product

import pandas as pd

import grid_floatstop_bt as G

OUTPUT_CSV = str(Path(__file__).parent / 'grid_param_validate_result.csv')

BASE = {
    'GBPJPY': dict(atr_mult=1.5, max_levels=7, float_stop=-1_500_000.0, lot=1.0, quote_jpy=1.0),
    'CHFJPY': dict(atr_mult=2.0, max_levels=7, float_stop=-1_500_000.0, lot=1.0, quote_jpy=1.0),
    'NZDJPY': dict(atr_mult=1.0, max_levels=7, float_stop=-500_000.0, lot=1.0, quote_jpy=1.0),
    'AUDCAD': dict(atr_mult=1.0, max_levels=7, float_stop=-500_000.0, lot=1.0, quote_jpy=108.0),
}

CI_GRID = [55.0, 60.0, 61.8, 65.0, 70.0]
ATR_GRID = [1.0, 1.5, 2.0, 2.5, 3.0]
LVL_GRID = [3, 5, 7]
B48 = 48  # b48_hours barely moved in full sweep; fix at live value


def split(df):
    mid = df.index[0] + (df.index[-1] - df.index[0]) / 2
    return df[df.index < mid], df[df.index >= mid]


def evalcfg(pair, cfg, df, atr_full, ci_full):
    atr = atr_full.reindex(df.index)
    ci = ci_full.reindex(df.index)
    return G.run_backtest(pair, cfg, df, atr, ci)


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
        for ci_th, atr_m, lvl in product(CI_GRID, ATR_GRID, LVL_GRID):
            cfg = dict(base); cfg.update(ci_threshold=ci_th, atr_mult=atr_m,
                                         max_levels=lvl, b48_hours=B48)
            ris = evalcfg(pair, cfg, df_is, atr_full, ci_full)
            roos = evalcfg(pair, cfg, df_oos, atr_full, ci_full)
            rec = dict(pair=pair, ci_th=ci_th, atr_mult=atr_m, max_levels=lvl,
                       is_pf=ris['pf'], is_net=ris['total_pnl'], is_ntp=ris['n_tp'],
                       is_loss_ev=ris['n_b48'] + ris['n_fstop'],
                       oos_pf=roos['pf'], oos_net=roos['total_pnl'], oos_ntp=roos['n_tp'],
                       oos_loss_ev=roos['n_b48'] + roos['n_fstop'],
                       oos_worst=roos['worst_event'], oos_dd=roos['max_dd'])
            results.append(rec); rows.append(rec)

        # LIVE reference
        b = base
        live = next((r for r in results if r['ci_th'] == 61.8 and r['atr_mult'] == b['atr_mult']
                     and r['max_levels'] == b['max_levels']), None)
        if live:
            print(f'  LIVE ci=61.8 atr={b["atr_mult"]} lv={b["max_levels"]}: '
                  f'IS PF={live["is_pf"]:.2f}/net={live["is_net"]:,.0f} | '
                  f'OOS PF={live["oos_pf"]:.2f}/net={live["oos_net"]:,.0f}')

        # Robust: net>0 both halves, PF>=1.2 both, loss control exercised in >=1 half,
        # decent sample. Rank by min(IS_pf,OOS_pf) capped (inf treated high but de-prioritized
        # if no loss events at all).
        def keyrobust(r):
            ispf = 99 if r['is_pf'] == float('inf') else r['is_pf']
            oospf = 99 if r['oos_pf'] == float('inf') else r['oos_pf']
            return min(ispf, oospf)
        cand = [r for r in results
                if r['is_net'] > 0 and r['oos_net'] > 0
                and r['is_pf'] >= 1.2 and r['oos_pf'] >= 1.2
                and (r['is_ntp'] + r['oos_ntp']) >= 40
                and (r['is_loss_ev'] + r['oos_loss_ev']) >= 1]  # exclude never-lost overfits
        cand.sort(key=keyrobust, reverse=True)
        print('  --- robust configs (net>0 & PF>=1.2 BOTH halves, loss-tested, n>=40) ---')
        if not cand:
            print('    (none - no config robust across IS/OOS with loss events)')
        for r in cand[:4]:
            ispf = 'inf' if r['is_pf'] == float('inf') else f'{r["is_pf"]:.2f}'
            oospf = 'inf' if r['oos_pf'] == float('inf') else f'{r["oos_pf"]:.2f}'
            print(f'    ci={r["ci_th"]:<5} atr={r["atr_mult"]:<4} lv={r["max_levels"]}: '
                  f'IS PF={ispf}/net={r["is_net"]:>11,.0f}/n={r["is_ntp"]} | '
                  f'OOS PF={oospf}/net={r["oos_net"]:>11,.0f}/n={r["oos_ntp"]} '
                  f'lossEv(IS+OOS)={r["is_loss_ev"]+r["oos_loss_ev"]} '
                  f'OOSworst={r["oos_worst"]:,.0f} OOSdd={r["oos_dd"]:,.0f}')

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved: {OUTPUT_CSV}  ({len(rows)} combos x IS/OOS)')


if __name__ == '__main__':
    main()
