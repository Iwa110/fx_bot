"""
grid_dd_reduction_plateau.py - 併用案(mom+cull+taper)の近傍プラトー検証。
cull_frac×taper の格子で full/IS/OOS/WFO/DD/worst を確認し、
推奨点(cull0.5/taper0.7)が崖スパイクでないことを確かめる。
実行: .venv_dukas/bin/python optimizer/grid_dd_reduction_plateau.py
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_dd_reduction_plateau.csv'


def sweep(pair, cfg):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    rows = []
    print(f'=== {pair}: mom2.0 + cull×taper 格子 ===')
    for cull in [0.4, 0.5, 0.6]:
        for tap in [0.6, 0.7, 0.85, 1.0]:
            kw = {'mom_thr': 2.0, 'cull_frac': cull}
            if tap < 1.0: kw['taper'] = tap
            m = D.metrics(cfg, df, atr, ci, r24, **kw)
            D.show(f'cull{cull}/tap{tap}', m)
            rows.append({'pair': pair, 'cull': cull, 'taper': tap,
                         **{k: v for k, v in m.items() if k != 'wfo_each'}})
    # mom感応度 (cull0.5/tap0.7 固定)
    print(f'--- {pair}: mom_thr 感応度 (cull0.5/tap0.7) ---')
    for mt in [1.5, 2.0, 2.5, 3.0]:
        m = D.metrics(cfg, df, atr, ci, r24, mom_thr=mt, cull_frac=0.5, taper=0.7)
        D.show(f'mom{mt}', m)
        rows.append({'pair': pair, 'cull': 0.5, 'taper': 0.7, 'mom': mt,
                     **{k: v for k, v in m.items() if k != 'wfo_each'}})
    return rows


def main():
    rows = sweep('AUDCAD', D.AUDCAD)
    rows += sweep('EURGBP', D.EURGBP)
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
