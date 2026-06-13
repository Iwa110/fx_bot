"""
grid_entry_filter_validate.py - エントリー改善案の頑健性検証(過適合の罠を潰す)。
  V1 トレード数: 各案の full/IS/OOS/WFO の n_tp を出し、高PFが薄標本でないか確認。
  V2 CI崖スキャン: ci 65..70 を1刻みで。既知のci67.5スパイク警告と同型か(nFS急減=崖)を判定。
  V3 モメンタム・ゲートの構造性: F1 mom gate を全4ペアの各v7構成へ適用。AUDCAD専用curve-fitか
     「トレンドに逆らって追加しない」普遍効果かを判定(全ペアで方向一致改善なら構造的)。
真値=Dukascopy。t-1。エンジン=grid_entry_filter_bt.run_bt。
実行: python3 optimizer/grid_entry_filter_validate.py
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G, grid_insensitivity as GI
import grid_entry_filter_bt as EF

DATA = Path(__file__).resolve().parent.parent / 'data'
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]


def load(p): return EF.load_duk(p)


def fullwin(cfg, df, atr, ci, ret24, thr, lo=None, hi=None):
    m = EF.win_mask(df, lo, hi); sub = df[m]
    if len(sub) < 200: return None
    return EF.run_bt(cfg, sub, atr, ci, ret24[m], thr)


def v1_v2():
    p = 'AUDCAD'; df = load(p); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    ret24 = EF.ret24_series(df, atr); base = {**GI.V7_CONFIG[p], 'atr_mult': 1.5}

    print('=== V1: トレード数(n_tp)で薄標本チェック ===')
    print(f'{"variant":18s} {"full_ntp":>8s} {"IS_ntp":>7s} {"OOS_ntp":>8s} {"2022":>5s} {"2023":>5s} {"2024":>5s} {"2025":>5s}')
    def ntps(cfg, thr):
        full = fullwin(cfg, df, atr, ci, ret24, thr)
        isr = fullwin(cfg, df, atr, ci, ret24, thr, *IS_WIN)
        oos = fullwin(cfg, df, atr, ci, ret24, thr, *OOS_WIN)
        yr = [fullwin(cfg, df, atr, ci, ret24, thr, f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
        return full, isr, oos, yr
    for tag, cfg, thr in [('baseline', base, None), ('F1 mom2.0', base, 2.0),
                          ('F2 ci67', {**base, 'ci_threshold': 67.0}, None),
                          ('F3 mom2.0+ci67', {**base, 'ci_threshold': 67.0}, 2.0)]:
        full, isr, oos, yr = ntps(cfg, thr)
        print(f'{tag:18s} {full["n_tp"]:8d} {isr["n_tp"]:7d} {oos["n_tp"]:8d} '
              + ' '.join(f'{(y["n_tp"] if y else 0):5d}' for y in yr))

    print('\n=== V2: CI崖スキャン (ci 65..70, full 11yr). nFS急減=崖隣接スパイク(既知ci67.5型) ===')
    print(f'{"ci":>4s} {"full_PF":>7s} {"full_net":>11s} {"OOS_PF":>7s} {"nFS":>4s} {"nTP":>5s}')
    for c in [65, 66, 67, 68, 69, 70]:
        cfg = {**base, 'ci_threshold': float(c)}
        full = fullwin(cfg, df, atr, ci, ret24, None)
        oos = fullwin(cfg, df, atr, ci, ret24, None, *OOS_WIN)
        print(f'{c:4d} {full["pf"]:7.2f} {full["total_pnl"]:11,.0f} {oos["pf"]:7.2f} {full["n_fstop"]:4d} {full["n_tp"]:5d}')

    print('\n=== V2b: モメンタム閾値プラトー (mom 1.0..3.5, full+OOS) ===')
    print(f'{"thr":>4s} {"full_PF":>7s} {"OOS_PF":>7s} {"full_DD":>9s} {"nFS":>4s} {"nTP":>5s}')
    for thr in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]:
        full = fullwin(base, df, atr, ci, ret24, thr)
        oos = fullwin(base, df, atr, ci, ret24, thr, *OOS_WIN)
        print(f'{thr:4.1f} {full["pf"]:7.2f} {oos["pf"]:7.2f} {full["max_dd"]:9,.0f} {full["n_fstop"]:4d} {full["n_tp"]:5d}')


def v3():
    print('\n=== V3: モメンタム・ゲートの構造性 (全4ペア各v7構成に mom2.0 適用) ===')
    print('  普遍的に full/OOS PF改善 & nFS減 なら「トレンドに逆らい追加しない」構造効果(AUDCAD curve-fitでない)')
    print(f'{"pair":7s} {"base_PF":>7s} {"mom_PF":>7s} {"base_OOS":>8s} {"mom_OOS":>8s} '
          f'{"base_nFS":>8s} {"mom_nFS":>7s} {"base_net":>11s} {"mom_net":>11s}')
    for p in ['AUDCAD', 'GBPJPY', 'CHFJPY', 'NZDJPY']:
        df = load(p); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        ret24 = EF.ret24_series(df, atr); cfg = GI.V7_CONFIG[p]
        b = fullwin(cfg, df, atr, ci, ret24, None)
        mth = fullwin(cfg, df, atr, ci, ret24, 2.0)
        bo = fullwin(cfg, df, atr, ci, ret24, None, *OOS_WIN)
        mo = fullwin(cfg, df, atr, ci, ret24, 2.0, *OOS_WIN)
        print(f'{p:7s} {b["pf"]:7.2f} {mth["pf"]:7.2f} {bo["pf"]:8.2f} {mo["pf"]:8.2f} '
              f'{b["n_fstop"]:8d} {mth["n_fstop"]:7d} {b["total_pnl"]:11,.0f} {mth["total_pnl"]:11,.0f}')


if __name__ == '__main__':
    v1_v2()
    v3()
