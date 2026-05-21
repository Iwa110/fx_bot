"""
grid_ci_sweep.py  --  Choppiness Index 閾値スイープ  (NZDUSD)

固定設定:
  exit = B48  (max_levels 到達から 48h で全決済)
  use_adx = False, use_relaltr = False

スイープ:
  Part 1: CI_th × gm  (lv=7 固定)
    CI_th : [38.2, 45, 50, 55, 61.8, 65, 70, 75]
    gm    : [1.0, 1.5, 2.0]
    → full period + IS(70%)/OOS(30%) 並列表示

  Part 2: CI_th × lv  (gm=2.0 固定)
    CI_th : 上記と同じ
    lv    : [3, 5, 7]
    → full period のみ

Output: optimizer/grid_ci_sweep_result.csv
        optimizer/grid_ci_sweep.png
"""

import sys
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from grid_bt_v2 import load_data, build_indicators, run_backtest

warnings.filterwarnings('ignore')

OUTPUT_DIR  = Path(__file__).parent
OUTPUT_CSV  = str(OUTPUT_DIR / 'grid_ci_sweep_result.csv')
CHART_PATH  = str(OUTPUT_DIR / 'grid_ci_sweep.png')

# ---------------------------------------------------------------
# Sweep settings
# ---------------------------------------------------------------
CI_TH_LIST      = [38.2, 45, 50, 55, 61.8, 65, 70, 75]
GM_LIST         = [1.0, 1.5, 2.0]
LV_LIST         = [3, 5, 7]
FIXED_LV        = 7
FIXED_GM        = 2.0
FIXED_EH        = 48
IS_RATIO        = 0.70
MIN_TRADES      = 10


# ---------------------------------------------------------------
# Helper: single run returning flat metrics dict
# ---------------------------------------------------------------

def one_run(df, conv, ind, ci_th, gm, lv, label='full'):
    m, eq = run_backtest(
        df, conv, ind,
        grid_mult    = gm,
        max_levels   = int(lv),
        exit_type    = 'B',
        exit_param   = FIXED_EH,
        use_ci       = True,
        use_adx      = False,
        use_relaltr  = False,
        ci_threshold = ci_th,
    )
    if m is None or m['n_trades'] < MIN_TRADES:
        return {'PF': np.nan, 'WR': np.nan, 'n_trades': 0,
                'total_pnl_jpy': 0, 'max_dd_pct': np.nan,
                'avg_hold_h': np.nan, 'label': label}
    return {**m, 'label': label}


def ci_active_pct(ind, ci_th):
    """% of bars where CI > ci_th (filter passes)."""
    ci = ind['ci_d1']
    valid = ci[~np.isnan(ci)]
    return round(float((valid > ci_th).mean() * 100), 1)


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    print('[INFO] Loading NZDUSD...')
    df, conv_arr = load_data()
    n = len(df)
    print(f'[INFO] {n} bars  {df.index[0].date()} -> {df.index[-1].date()}')

    print('[INFO] Computing indicators...')
    ind = build_indicators(df)

    s70 = int(n * IS_RATIO)
    df_is,  conv_is,  ind_is  = (df.iloc[:s70],  conv_arr[:s70],
                                  {k: v[:s70] for k, v in ind.items()})
    df_oos, conv_oos, ind_oos = (df.iloc[s70:],  conv_arr[s70:],
                                  {k: v[s70:] for k, v in ind.items()})
    print(f'[INFO] IS : {df_is.index[0].date()} - {df_is.index[-1].date()} '
          f'({len(df_is)} bars)')
    print(f'[INFO] OOS: {df_oos.index[0].date()} - {df_oos.index[-1].date()} '
          f'({len(df_oos)} bars)')

    # =====================================================
    # Part 1: CI_th × gm  (lv=FIXED_LV)
    # =====================================================
    print(f'\n[Part 1] CI_th × gm  (lv={FIXED_LV}, eh={FIXED_EH}h)')
    rows_p1 = []
    total_p1 = len(CI_TH_LIST) * len(GM_LIST) * 3  # full + IS + OOS
    done = 0
    for ci_th in CI_TH_LIST:
        pct = ci_active_pct(ind, ci_th)
        for gm in GM_LIST:
            done += 3
            if done % 30 == 0:
                print(f'  ... {done}/{total_p1}')
            full = one_run(df,    conv_arr, ind,    ci_th, gm, FIXED_LV, 'full')
            is_r = one_run(df_is, conv_is,  ind_is, ci_th, gm, FIXED_LV, 'IS')
            oo_r = one_run(df_oos,conv_oos, ind_oos,ci_th, gm, FIXED_LV, 'OOS')
            rows_p1.append({
                'part':       1,
                'ci_th':      ci_th,
                'ci_pct':     pct,
                'gm':         gm,
                'lv':         FIXED_LV,
                'full_PF':    full['PF'],   'full_n': full['n_trades'],
                'full_PnL':   full['total_pnl_jpy'],
                'full_DD':    full['max_dd_pct'],
                'full_hold':  full['avg_hold_h'],
                'IS_PF':      is_r['PF'],   'IS_n': is_r['n_trades'],
                'IS_PnL':     is_r['total_pnl_jpy'],
                'OOS_PF':     oo_r['PF'],   'OOS_n': oo_r['n_trades'],
                'OOS_PnL':    oo_r['total_pnl_jpy'],
                'OOS_DD':     oo_r['max_dd_pct'],
            })

    # =====================================================
    # Part 2: CI_th × lv  (gm=FIXED_GM)
    # =====================================================
    print(f'\n[Part 2] CI_th × lv  (gm={FIXED_GM}, eh={FIXED_EH}h)')
    rows_p2 = []
    for ci_th in CI_TH_LIST:
        pct = ci_active_pct(ind, ci_th)
        for lv in LV_LIST:
            full = one_run(df, conv_arr, ind, ci_th, FIXED_GM, lv, 'full')
            rows_p2.append({
                'part':       2,
                'ci_th':      ci_th,
                'ci_pct':     pct,
                'gm':         FIXED_GM,
                'lv':         lv,
                'full_PF':    full['PF'],
                'full_n':     full['n_trades'],
                'full_PnL':   full['total_pnl_jpy'],
                'full_DD':    full['max_dd_pct'],
                'full_hold':  full['avg_hold_h'],
            })

    # ---- Save CSV ----
    all_rows = rows_p1 + rows_p2
    result_df = pd.DataFrame(all_rows)
    result_df.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[INFO] Saved: {OUTPUT_CSV}')

    # ---- Print Part 1 summary ----
    pd.set_option('display.width', 180)
    df1 = pd.DataFrame(rows_p1)
    print('\n=== Part 1: CI_th × gm  (lv=7, IS/OOS) ===')
    print(df1[['ci_th', 'ci_pct', 'gm',
               'IS_PF', 'IS_n', 'IS_PnL',
               'OOS_PF', 'OOS_n', 'OOS_PnL', 'OOS_DD',
               'full_PF', 'full_n', 'full_hold']].to_string(index=False))

    # ---- Print Part 2 summary ----
    df2 = pd.DataFrame(rows_p2)
    print('\n=== Part 2: CI_th × lv  (gm=2.0, full) ===')
    # Pivot: ci_th × lv for full_PF
    piv_pf = df2.pivot(index='ci_th', columns='lv', values='full_PF').round(3)
    piv_n  = df2.pivot(index='ci_th', columns='lv', values='full_n')
    print('PF:')
    print(piv_pf.to_string())
    print('\nn_trades:')
    print(piv_n.to_string())

    # ---- Charts ----
    fig, axes = plt.subplots(3, 2, figsize=(14, 13))
    fig.suptitle(f'CI Threshold Sweep  (NZDUSD / B{FIXED_EH}h exit)',
                 fontsize=11, y=1.01)

    colors = {'1.0': '#2196F3', '1.5': '#FF9800', '2.0': '#4CAF50'}
    lv_colors = {3: '#E91E63', 5: '#9C27B0', 7: '#00BCD4'}

    # [0,0] IS PF vs CI_th by gm
    ax = axes[0, 0]
    for gm in GM_LIST:
        sub = df1[df1['gm'] == gm].sort_values('ci_th')
        ax.plot(sub['ci_th'], sub['IS_PF'], 'o-', label=f'gm={gm}',
                color=colors[str(gm)], linewidth=1.5, markersize=5)
    ax.axhline(1.3, color='red', ls='--', lw=1, label='PF=1.3')
    ax.axhline(1.0, color='gray', ls=':', lw=1)
    ax.set_title(f'IS PF vs CI_th  (lv={FIXED_LV})')
    ax.set_xlabel('CI threshold'); ax.set_ylabel('PF (IS)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # [0,1] OOS PF vs CI_th by gm
    ax = axes[0, 1]
    for gm in GM_LIST:
        sub = df1[df1['gm'] == gm].sort_values('ci_th')
        ax.plot(sub['ci_th'], sub['OOS_PF'], 'o-', label=f'gm={gm}',
                color=colors[str(gm)], linewidth=1.5, markersize=5)
    ax.axhline(1.3, color='red', ls='--', lw=1, label='PF=1.3')
    ax.axhline(1.0, color='gray', ls=':', lw=1)
    ax.set_title(f'OOS PF vs CI_th  (lv={FIXED_LV})')
    ax.set_xlabel('CI threshold'); ax.set_ylabel('PF (OOS)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # [1,0] full_PnL vs CI_th by gm
    ax = axes[1, 0]
    for gm in GM_LIST:
        sub = df1[df1['gm'] == gm].sort_values('ci_th')
        ax.plot(sub['ci_th'], sub['full_PnL'] / 1000, 'o-', label=f'gm={gm}',
                color=colors[str(gm)], linewidth=1.5, markersize=5)
    ax.axhline(0, color='gray', ls=':', lw=1)
    ax.set_title(f'Full PnL (千円) vs CI_th  (lv={FIXED_LV})')
    ax.set_xlabel('CI threshold'); ax.set_ylabel('Total PnL (千JPY)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # [1,1] n_trades vs CI_th (% ci_active on twin axis)
    ax  = axes[1, 1]
    ax2 = ax.twinx()
    sub0 = df1[df1['gm'] == FIXED_GM].sort_values('ci_th')
    ax.bar(sub0['ci_th'], sub0['full_n'], width=2.5, alpha=0.6,
           color='steelblue', label='n_trades (full)')
    ax2.plot(sub0['ci_th'], sub0['ci_pct'], 'ro-', lw=1.5, ms=5,
             label='CI active %')
    ax.set_title(f'n_trades & CI active%  (gm={FIXED_GM}, lv={FIXED_LV})')
    ax.set_xlabel('CI threshold')
    ax.set_ylabel('n_trades', color='steelblue')
    ax2.set_ylabel('CI active % of bars', color='red')
    ax.legend(loc='upper left', fontsize=8)
    ax2.legend(loc='upper right', fontsize=8)
    ax.grid(True, alpha=0.3)

    # [2,0] Part 2: full PF vs CI_th by lv
    ax = axes[2, 0]
    for lv in LV_LIST:
        sub = df2[df2['lv'] == lv].sort_values('ci_th')
        ax.plot(sub['ci_th'], sub['full_PF'], 'o-', label=f'lv={lv}',
                color=lv_colors[lv], linewidth=1.5, markersize=5)
    ax.axhline(1.3, color='red', ls='--', lw=1, label='PF=1.3')
    ax.axhline(1.0, color='gray', ls=':', lw=1)
    ax.set_title(f'Full PF vs CI_th  (gm={FIXED_GM})')
    ax.set_xlabel('CI threshold'); ax.set_ylabel('PF (full)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # [2,1] Part 2: full PnL vs CI_th by lv
    ax = axes[2, 1]
    for lv in LV_LIST:
        sub = df2[df2['lv'] == lv].sort_values('ci_th')
        ax.plot(sub['ci_th'], sub['full_PnL'] / 1000, 'o-', label=f'lv={lv}',
                color=lv_colors[lv], linewidth=1.5, markersize=5)
    ax.axhline(0, color='gray', ls=':', lw=1)
    ax.set_title(f'Full PnL (千円) vs CI_th  (gm={FIXED_GM})')
    ax.set_xlabel('CI threshold'); ax.set_ylabel('Total PnL (千JPY)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHART_PATH, dpi=110, bbox_inches='tight')
    print(f'[INFO] Chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
