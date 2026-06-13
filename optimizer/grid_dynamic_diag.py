"""
grid_dynamic_diag.py - 動的Grid検証の2つの確認:
  (a) AUDCAD Case3(vol連動max_levels)の full maxDD 増加が IS テール年(2018/2020)由来かを per-year で診断。
  (b) NZDJPY(No-Go, 静的wfoMin0.78と惜しい)に最有望の動的構造(vol連動max_levels)を適用しGo化可否を1ケース確認。
真値=Dukascopy。t-1 凍結マッピング(IS=2015-2021)。
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G, grid_insensitivity as GI, grid_dynamic_bt as D
from grid_dynamic_eval import (load_duk, win_mask, run_win, const_arrs, regime_arrs,
                               IS_WIN, OOS_WIN)

YEARS = list(range(2016, 2026))


def per_year(pair, df, atr, ci, arrs, qj):
    out = {}
    for y in YEARS:
        r = run_win(pair, df, atr, ci, arrs, qj, f'{y}-01-01', f'{y}-12-31')
        out[y] = r
    return out


def main():
    # ---- (a) AUDCAD baseline vs Case3 vol[5,5,3] / vol[7,5,5] per-year DD ----
    p = 'AUDCAD'; df = load_duk(p); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    qj = GI.V7_CONFIG[p]['quote_jpy']
    base_cfg = {**GI.V7_CONFIG[p], 'atr_mult': 1.5}
    is_mask = win_mask(df, *IS_WIN)
    vol = D.atr_regime_prev(df, atr, period_price=True)
    thr3 = D.freeze_quantile_thresholds(vol, is_mask, [1/3, 2/3]); buck3 = D.bucketize(vol, thr3)

    base_arrs = const_arrs(df, base_cfg)
    dyn_arrs = regime_arrs(df, base_cfg, buck3, [5, 5, 3], 'maxlv')
    print('=== (a) AUDCAD per-year: baseline atr1.5 vs Case3 vol-maxlv[5,5,3] ===')
    print(f'{"year":5s} | {"base_PF":>7s} {"base_DD":>10s} {"base_FS":>3s} | {"dyn_PF":>7s} {"dyn_DD":>10s} {"dyn_FS":>3s}')
    by = per_year(p, df, atr, ci, base_arrs, qj)
    dy = per_year(p, df, atr, ci, dyn_arrs, qj)
    for y in YEARS:
        b, d = by[y], dy[y]
        if not b or not d: continue
        print(f'{y:5d} | {b["pf"]:7.2f} {b["max_dd"]:10,.0f} {b["n_fstop"]:3d} | {d["pf"]:7.2f} {d["max_dd"]:10,.0f} {d["n_fstop"]:3d}')

    # ---- (b) NZDJPY: static baseline vs dynamic vol-maxlv ----
    print('\n=== (b) NZDJPY No-Go救済可否: 静的v7 vs 動的vol-maxlv ===')
    p2 = 'NZDJPY'; df2 = load_duk(p2); atr2 = G.compute_atr_series(df2); ci2 = G.compute_ci_series(df2)
    qj2 = GI.V7_CONFIG[p2]['quote_jpy']
    bcfg2 = GI.V7_CONFIG[p2]  # atr1.5/ci61.8/lv7/fs-1.0M
    is_mask2 = win_mask(df2, *IS_WIN)
    vol2 = D.atr_regime_prev(df2, atr2, period_price=False)  # JPYペアはprice正規化不要
    thr3b = D.freeze_quantile_thresholds(vol2, is_mask2, [1/3, 2/3]); buck3b = D.bucketize(vol2, thr3b)

    def show(tag, arrs, dfx, atrx, cix, qjx, px):
        full = run_win(px, dfx, atrx, cix, arrs, qjx)
        oos = run_win(px, dfx, atrx, cix, arrs, qjx, *OOS_WIN)
        isr = run_win(px, dfx, atrx, cix, arrs, qjx, *IS_WIN)
        wfo = []
        for y in [2022, 2023, 2024, 2025]:
            r = run_win(px, dfx, atrx, cix, arrs, qjx, f'{y}-01-01', f'{y}-12-31')
            if r and r['n_tp'] >= 10: wfo.append(r['pf'])
        wfo = np.array(wfo)
        print(f'{tag:28s} full PF={full["pf"]:.2f} net={full["total_pnl"]:>11,.0f} DD={full["max_dd"]:>10,.0f} | '
              f'IS={isr["pf"]:.2f} OOS={oos["pf"]:.2f} | WFOmed={np.median(wfo):.2f} min={wfo.min():.2f} '
              f'>1.2={(wfo>1.2).mean():.2f} {[round(x,2) for x in wfo]}')

    show('static v7 (lv7)', const_arrs(df2, bcfg2), df2, atr2, ci2, qj2, p2)
    for mp in ([7, 5, 3], [7, 5, 5], [5, 5, 3]):
        show(f'dyn vol-maxlv{mp}', regime_arrs(df2, bcfg2, buck3b, mp, 'maxlv'), df2, atr2, ci2, qj2, p2)


if __name__ == '__main__':
    main()
