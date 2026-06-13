"""
grid_pair_screen.py  --  全ペア B48+CI スクリーニング

確定フレームワーク: exit=B48 / CI_th=61.8 / use_adx=False / use_relaltr=False
スイープ: grid_mult x max_levels (9コンボ) x 全16ペア
評価: full-period + IS(70%)/OOS(30%)

Output: optimizer/grid_pair_screen_result.csv
        optimizer/grid_pair_screen.png
"""

import itertools
import sys
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from grid_bt   import load_ref_rates, load_pair_data, PAIRS   # multi-pair loader
from grid_bt_v2 import build_indicators, run_backtest          # BT engine

warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'grid_pair_screen_result.csv')
CHART_PATH = str(OUTPUT_DIR / 'grid_pair_screen.png')

# ---------------------------------------------------------------
# Fixed
# ---------------------------------------------------------------
CI_TH    = 61.8
EXIT_H   = 48
IS_RATIO = 0.70
MIN_OOS  = 10      # OOS最低トレード数

# ---------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------
GM_LIST = [1.0, 1.5, 2.0]
LV_LIST = [3, 5, 7]
COMBOS  = list(itertools.product(GM_LIST, LV_LIST))  # 9


def ci_pct(ind):
    """CI > 61.8 となるバーの割合 (%)."""
    ci = ind['ci_d1']
    v  = ci[~np.isnan(ci)]
    return round(float((v > CI_TH).mean() * 100), 1) if len(v) else 0.0


def run_one(df, conv, ind, gm, lv):
    m, eq = run_backtest(
        df, conv, ind,
        grid_mult    = gm,
        max_levels   = int(lv),
        exit_type    = 'B',
        exit_param   = EXIT_H,
        use_ci       = True,
        use_adx      = False,
        use_relaltr  = False,
        ci_threshold = CI_TH,
    )
    return m  # None if too few trades


def main():
    print('[INFO] Loading reference JPY rates...')
    ref_rates = load_ref_rates()

    IS_SPLIT = IS_RATIO
    rows      = []
    skipped   = []

    for pair in PAIRS:
        df, conv = load_pair_data(pair, ref_rates)
        if df is None:
            skipped.append(pair)
            continue

        n    = len(df)
        s70  = int(n * IS_SPLIT)
        df_is,  conv_is  = df.iloc[:s70],   conv[:s70]
        df_oos, conv_oos = df.iloc[s70:],   conv[s70:]

        print(f'\n[{pair:8s}] bars={n}  '
              f'IS={df_is.index[0].date()}~{df_is.index[-1].date()}  '
              f'OOS={df_oos.index[0].date()}~{df_oos.index[-1].date()}')

        # indicators (reuse for all combos)
        ind_full = build_indicators(df)
        ind_is   = {k: v[:s70] for k, v in ind_full.items()}
        ind_oos  = {k: v[s70:] for k, v in ind_full.items()}

        pct = ci_pct(ind_full)
        print(f'  CI active {pct}%')

        # IS grid search
        best_is_pf  = -np.inf
        best_combo  = None
        is_results  = {}

        for gm, lv in COMBOS:
            m_is = run_one(df_is, conv_is, ind_is, gm, lv)
            is_results[(gm, lv)] = m_is
            if m_is and m_is['PF'] > best_is_pf:
                best_is_pf = m_is['PF']
                best_combo = (gm, lv)

        if best_combo is None:
            print(f'  -> no IS result (CI too strict?)')
            skipped.append(pair)
            continue

        # store all combos (full period + IS + OOS)
        for gm, lv in COMBOS:
            m_full = run_one(df,    conv,    ind_full, gm, lv)
            m_is   = is_results[(gm, lv)]
            m_oos  = run_one(df_oos, conv_oos, ind_oos, gm, lv)

            rows.append({
                'pair':     pair,
                'gm':       gm,
                'lv':       lv,
                'ci_pct':   pct,
                'is_best':  (gm, lv) == best_combo,
                # full
                'full_PF':  m_full['PF']           if m_full else np.nan,
                'full_n':   m_full['n_trades']      if m_full else 0,
                'full_PnL': m_full['total_pnl_jpy'] if m_full else 0,
                'full_DD':  m_full['max_dd_pct']    if m_full else np.nan,
                'full_hold':m_full['avg_hold_h']    if m_full else np.nan,
                # IS
                'IS_PF':    m_is['PF']           if m_is else np.nan,
                'IS_n':     m_is['n_trades']      if m_is else 0,
                'IS_PnL':   m_is['total_pnl_jpy'] if m_is else 0,
                # OOS
                'OOS_PF':   m_oos['PF']           if m_oos and m_oos['n_trades'] >= MIN_OOS else np.nan,
                'OOS_n':    m_oos['n_trades']      if m_oos else 0,
                'OOS_PnL':  m_oos['total_pnl_jpy'] if m_oos and m_oos['n_trades'] >= MIN_OOS else 0,
                'OOS_DD':   m_oos['max_dd_pct']    if m_oos and m_oos['n_trades'] >= MIN_OOS else np.nan,
                'OOS_hold': m_oos['avg_hold_h']    if m_oos and m_oos['n_trades'] >= MIN_OOS else np.nan,
            })

        bg, bl = best_combo
        m_b_oos = run_one(df_oos, conv_oos, ind_oos, bg, bl)
        oos_pf  = m_b_oos['PF'] if m_b_oos and m_b_oos['n_trades'] >= MIN_OOS else float('nan')
        oos_n   = m_b_oos['n_trades'] if m_b_oos else 0
        print(f'  IS best: gm={bg} lv={bl}  IS_PF={best_is_pf:.3f}  '
              f'OOS_PF={oos_pf:.3f}  OOS_n={oos_n}')

    # ---- Save CSV ----
    df_res = pd.DataFrame(rows)
    df_res.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[INFO] Saved: {OUTPUT_CSV}  ({len(df_res)} rows)')
    if skipped:
        print(f'[INFO] Skipped: {skipped}')

    # ---- Per-pair best summary ----
    pd.set_option('display.width', 200)
    pd.set_option('display.float_format', lambda x: f'{x:.3f}')

    # Best combo per pair: highest IS_PF
    best = (df_res.sort_values('IS_PF', ascending=False)
                  .groupby('pair', sort=False)
                  .first()
                  .reset_index()
                  .sort_values('OOS_PF', ascending=False))

    print('\n=== ペア別 ベストIS→OOS (IS_PF基準で選択) ===')
    cols = ['pair', 'gm', 'lv', 'ci_pct',
            'IS_PF', 'IS_n', 'IS_PnL',
            'OOS_PF', 'OOS_n', 'OOS_PnL', 'OOS_DD', 'OOS_hold',
            'full_PF', 'full_PnL']
    print(best[cols].to_string(index=False))

    # Qualifying
    qual = best[
        (best['OOS_PF'] >= 1.3) &
        (best['OOS_n']  >= MIN_OOS)
    ]
    print(f'\n=== OOS PF>=1.3 & n>={MIN_OOS} ===')
    print(qual[cols].to_string(index=False) if not qual.empty else '  (none)')

    # Also: best OOS_PF regardless of IS (OOS-best)
    oos_best = (df_res.dropna(subset=['OOS_PF'])
                      .sort_values('OOS_PF', ascending=False)
                      .groupby('pair', sort=False)
                      .first()
                      .reset_index()
                      .sort_values('OOS_PF', ascending=False))
    print('\n=== ペア別 OOS_PF最大値 (参考: IS選択なし) ===')
    print(oos_best[cols].to_string(index=False))

    # ---- Chart ----
    pairs_sorted = best.sort_values('OOS_PF', ascending=True)
    pairs_sorted = pairs_sorted.dropna(subset=['OOS_PF'])

    fig, axes = plt.subplots(1, 2, figsize=(15, 7))

    # Bar: IS vs OOS PF per pair
    ax = axes[0]
    y   = range(len(pairs_sorted))
    h   = 0.35
    ax.barh([i + h/2 for i in y], pairs_sorted['IS_PF'],  h, label='IS PF',  color='steelblue',  alpha=0.8)
    ax.barh([i - h/2 for i in y], pairs_sorted['OOS_PF'], h, label='OOS PF', color='tomato', alpha=0.8)
    ax.axvline(1.3, color='red',  ls='--', lw=1, label='PF=1.3')
    ax.axvline(1.0, color='gray', ls=':',  lw=1, label='PF=1.0')
    ax.set_yticks(list(y))
    ax.set_yticklabels(pairs_sorted['pair'].tolist(), fontsize=9)
    ax.set_xlabel('Profit Factor')
    ax.set_title('IS vs OOS PF by Pair\n(IS-best params, B48+CI=61.8)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3, axis='x')

    # Scatter: OOS_n vs OOS_PF (bubble = OOS_PnL)
    ax = axes[1]
    df_plot = pairs_sorted.dropna(subset=['OOS_PF', 'OOS_n'])
    colors_scatter = ['tomato' if p < 1.0 else ('orange' if p < 1.3 else 'green')
                      for p in df_plot['OOS_PF']]
    sc = ax.scatter(df_plot['OOS_n'], df_plot['OOS_PF'],
                    c=colors_scatter, s=100, zorder=3, edgecolors='k', lw=0.5)
    for _, row in df_plot.iterrows():
        ax.annotate(row['pair'], (row['OOS_n'], row['OOS_PF']),
                    textcoords='offset points', xytext=(5, 3), fontsize=7.5)
    ax.axhline(1.3, color='red',  ls='--', lw=1, label='PF=1.3')
    ax.axhline(1.0, color='gray', ls=':',  lw=1, label='PF=1.0')
    ax.set_xlabel('OOS n_trades')
    ax.set_ylabel('OOS PF')
    ax.set_title('OOS: PF vs n_trades\n(green=PF≥1.3, orange=1.0~1.3, red=<1.0)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    plt.suptitle('Multi-Pair Grid Screening  (B48 / CI>61.8 / gm×lv sweep)',
                 fontsize=11)
    plt.tight_layout()
    plt.savefig(CHART_PATH, dpi=110, bbox_inches='tight')
    print(f'\n[INFO] Chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
