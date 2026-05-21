"""
grid_isoos.py  --  IS / OOS 分割検証  (B-type exit + CI filter 固定)

IS  (In-Sample)  : パラメータ最適化に使う学習期間
OOS (Out-of-Sample): 最適化後に "見ていないデータ" で性能確認する検証期間

分割:
  単純分割 : IS=70% / OOS=30%
  ウォークフォワード (WF):
    WF1 : IS=50% → OOS=25%(前半)
    WF2 : IS=75% → OOS=25%(後半)

固定設定:
  pair   = NZDUSD 1h
  filter = Choppiness Index(D1,14) > 61.8 のみ
  exit   = B (Time SL: max_levels 到達から N 時間で全決済)

最適化対象:
  grid_mult  : [0.5, 1.0, 1.5, 2.0]
  max_levels : [3, 5, 7]
  exit_hours : [24, 48, 72]

Output: optimizer/grid_isoos_result.csv
        optimizer/grid_isoos_equity_top3.png
"""

import itertools
import sys
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd

# Import core functions from grid_bt_v2
sys.path.insert(0, str(Path(__file__).parent))
from grid_bt_v2 import load_data, build_indicators, run_backtest

warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'grid_isoos_result.csv')
CHART_PATH = str(OUTPUT_DIR / 'grid_isoos_equity_top3.png')

# ---------------------------------------------------------------
# Fixed strategy flags (rf=ci)
# ---------------------------------------------------------------
USE_CI      = True
USE_ADX     = False
USE_RELALTR = False

# ---------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------
GRID_MULT_LIST  = [0.5, 1.0, 1.5, 2.0]
MAX_LEVELS_LIST = [3, 5, 7]
EXIT_HOURS_LIST = [24, 48, 72]

MIN_TRADES = 10     # lower threshold for short OOS windows

# ---------------------------------------------------------------
# Split helper
# ---------------------------------------------------------------

def slice_data(df, conv_arr, ind, start, end):
    """Return sliced (df, conv, ind) for index range [start:end]."""
    df_s   = df.iloc[start:end]
    conv_s = conv_arr[start:end]
    ind_s  = {k: v[start:end] for k, v in ind.items()}
    return df_s, conv_s, ind_s


def run_grid(df_s, conv_s, ind_s, label=''):
    """
    Run all param combos on the given data slice.
    Returns DataFrame with metrics + equity_cache dict.
    """
    combos = list(itertools.product(GRID_MULT_LIST, MAX_LEVELS_LIST, EXIT_HOURS_LIST))
    rows   = []
    eq_map = {}

    for gm, ml, eh in combos:
        m, eq = run_backtest(
            df_s, conv_s, ind_s,
            grid_mult   = gm,
            max_levels  = int(ml),
            exit_type   = 'B',
            exit_param  = eh,
            use_ci      = USE_CI,
            use_adx     = USE_ADX,
            use_relaltr = USE_RELALTR,
        )
        if m is None or m['n_trades'] < MIN_TRADES:
            row = {'grid_mult': gm, 'max_levels': int(ml), 'exit_h': eh,
                   'PF': np.nan, 'WR': np.nan, 'n_trades': 0,
                   'total_pnl_jpy': 0, 'max_dd_pct': np.nan, 'avg_hold_h': np.nan}
        else:
            row = {'grid_mult': gm, 'max_levels': int(ml), 'exit_h': eh, **m}
        rows.append(row)
        eq_map[(gm, int(ml), eh)] = eq if eq else []

    df_res = pd.DataFrame(rows)
    return df_res, eq_map


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    print('[INFO] Loading NZDUSD data...')
    df, conv_arr = load_data()
    n = len(df)
    print(f'[INFO] Total bars: {n}  '
          f'{df.index[0].date()} -> {df.index[-1].date()}')

    print('[INFO] Computing indicators on full dataset...')
    ind = build_indicators(df)

    # ---- Define split points ----
    s70  = int(n * 0.70)   # simple split IS end
    s50  = int(n * 0.50)   # WF1 IS end
    s75  = int(n * 0.75)   # WF2 IS end

    print(f'\n[INFO] Split points:')
    print(f'  Simple 70/30  IS end : {df.index[s70].date()}  (bar {s70})')
    print(f'  WF1   50%→IS  OOS    : {df.index[s50].date()} - {df.index[s70].date()}')
    print(f'  WF2   75%→IS  OOS    : {df.index[s75].date()} - {df.index[n-1].date()}')

    # =====================================================
    # Part 1: Simple 70/30 split
    # =====================================================
    print('\n' + '='*60)
    print('Part 1: Simple 70/30 split')
    print('='*60)

    df_is, conv_is, ind_is = slice_data(df, conv_arr, ind, 0,    s70)
    df_oo, conv_oo, ind_oo = slice_data(df, conv_arr, ind, s70,  n  )

    print(f'  IS  bars: {len(df_is)}  '
          f'({df_is.index[0].date()} -> {df_is.index[-1].date()})')
    print(f'  OOS bars: {len(df_oo)}  '
          f'({df_oo.index[0].date()} -> {df_oo.index[-1].date()})')

    print('[INFO] Running IS grid search...')
    is_df, is_eq = run_grid(df_is, conv_is, ind_is, 'IS')

    print('[INFO] Running OOS validation...')
    oo_df, oo_eq = run_grid(df_oo, conv_oo, ind_oo, 'OOS')

    # Merge IS + OOS side-by-side
    merged = is_df.rename(columns={c: f'IS_{c}' for c in
                                    ['PF', 'WR', 'n_trades', 'total_pnl_jpy',
                                     'max_dd_pct', 'avg_hold_h']}) \
                  .merge(
                      oo_df.rename(columns={c: f'OOS_{c}' for c in
                                            ['PF', 'WR', 'n_trades', 'total_pnl_jpy',
                                             'max_dd_pct', 'avg_hold_h']}),
                      on=['grid_mult', 'max_levels', 'exit_h']
                  )
    merged['PF_decay'] = (merged['OOS_PF'] / merged['IS_PF']).round(3)
    merged = merged.sort_values('IS_PF', ascending=False).reset_index(drop=True)

    merged.to_csv(OUTPUT_CSV, index=False)
    print(f'[INFO] Results saved: {OUTPUT_CSV}')

    # ---- Print full comparison ----
    pd.set_option('display.width', 180)
    pd.set_option('display.max_columns', 25)

    show_cols = ['grid_mult', 'max_levels', 'exit_h',
                 'IS_PF', 'OOS_PF', 'PF_decay',
                 'IS_WR', 'OOS_WR',
                 'IS_n_trades', 'OOS_n_trades',
                 'IS_total_pnl_jpy', 'OOS_total_pnl_jpy',
                 'IS_max_dd_pct', 'OOS_max_dd_pct',
                 'IS_avg_hold_h', 'OOS_avg_hold_h']

    print('\n=== IS/OOS 全36コンボ比較 (IS_PF降順) ===')
    print(merged[show_cols].to_string(index=False))

    # ---- IS top-5 → OOS focus ----
    top5 = merged.head(5)
    print('\n=== IS 上位5件の OOS 性能 ===')
    print(top5[show_cols].to_string(index=False))

    # PF_decay analysis
    robust  = merged[merged['PF_decay'] >= 0.7].dropna(subset=['IS_PF', 'OOS_PF'])
    passing = merged[(merged['OOS_PF'] >= 1.3) & (merged['OOS_n_trades'] >= MIN_TRADES)]
    print(f'\n[INFO] PF_decay >= 0.7 (IS→OOS劣化30%以内): {len(robust)} / {len(merged)} コンボ')
    print(f'[INFO] OOS PF >= 1.3                       : {len(passing)} コンボ')
    if not passing.empty:
        print(passing[show_cols].to_string(index=False))

    # =====================================================
    # Part 2: Walk-forward validation
    # =====================================================
    print('\n' + '='*60)
    print('Part 2: Walk-forward (2 expanding windows)')
    print('='*60)

    wf_rows = []
    windows = [
        ('WF1', 0,   s50, s50, s70,  'IS=50% OOS=前半25%'),
        ('WF2', 0,   s75, s75, n,    'IS=75% OOS=後半25%'),
    ]

    wf_best_params = {}
    for tag, is_s, is_e, oo_s, oo_e, desc in windows:
        df_wis, conv_wis, ind_wis = slice_data(df, conv_arr, ind, is_s, is_e)
        df_woo, conv_woo, ind_woo = slice_data(df, conv_arr, ind, oo_s, oo_e)
        print(f'\n[{tag}] {desc}')
        print(f'  IS : {df_wis.index[0].date()} - {df_wis.index[-1].date()}  '
              f'({len(df_wis)} bars)')
        print(f'  OOS: {df_woo.index[0].date()} - {df_woo.index[-1].date()}  '
              f'({len(df_woo)} bars)')

        wis_df, wis_eq = run_grid(df_wis, conv_wis, ind_wis)
        woo_df, woo_eq = run_grid(df_woo, conv_woo, ind_woo)

        # Best IS params
        best_row = wis_df.dropna(subset=['PF']).sort_values('PF', ascending=False).iloc[0]
        best_key = (best_row['grid_mult'], int(best_row['max_levels']),
                    int(best_row['exit_h']))
        wf_best_params[tag] = best_key

        # OOS with best params
        oo_match = woo_df[
            (woo_df['grid_mult']  == best_key[0]) &
            (woo_df['max_levels'] == best_key[1]) &
            (woo_df['exit_h']     == best_key[2])
        ]
        oo_r = oo_match.iloc[0] if len(oo_match) > 0 else {}

        print(f'  IS  best: gm={best_key[0]} lv={best_key[1]} eh={best_key[2]}  '
              f'PF={best_row["PF"]:.3f}  WR={best_row["WR"]:.3f}  '
              f'n={int(best_row["n_trades"])}  PnL={int(best_row["total_pnl_jpy"]):+,}')
        if len(oo_match) > 0:
            r = oo_match.iloc[0]
            print(f'  OOS same: '
                  f'PF={r["PF"]:.3f}  WR={r["WR"]:.3f}  '
                  f'n={int(r["n_trades"])}  PnL={int(r["total_pnl_jpy"]):+,}')
        else:
            print('  OOS same: n/a')

        wf_rows.append({
            'window': tag, 'description': desc,
            'IS_start': df_wis.index[0].date(),
            'IS_end':   df_wis.index[-1].date(),
            'OOS_start': df_woo.index[0].date(),
            'OOS_end':   df_woo.index[-1].date(),
            'best_gm': best_key[0],
            'best_lv': best_key[1],
            'best_eh': best_key[2],
            'IS_PF':  round(float(best_row['PF']), 3),
            'IS_n':   int(best_row['n_trades']),
            'IS_PnL': int(best_row['total_pnl_jpy']),
            'OOS_PF': round(float(r['PF']), 3) if len(oo_match) > 0 else None,
            'OOS_n':  int(r['n_trades'])        if len(oo_match) > 0 else None,
            'OOS_PnL': int(r['total_pnl_jpy'])  if len(oo_match) > 0 else None,
        })

    wf_df = pd.DataFrame(wf_rows)
    print('\n=== Walk-forward サマリー ===')
    print(wf_df[['window', 'IS_PF', 'IS_n', 'IS_PnL',
                 'OOS_PF', 'OOS_n', 'OOS_PnL',
                 'best_gm', 'best_lv', 'best_eh']].to_string(index=False))

    # =====================================================
    # Part 3: Equity curve chart (top 3 IS params)
    # =====================================================
    top3 = merged.head(3)
    fig = plt.figure(figsize=(14, 3.8 * 3))
    gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.55, wspace=0.35)

    for row_idx, (_, row) in enumerate(top3.iterrows()):
        key = (row['grid_mult'], int(row['max_levels']), int(row['exit_h']))
        eq_is  = is_eq.get(key, [])
        eq_oos = oo_eq.get(key, [])

        ax_is  = fig.add_subplot(gs[row_idx, 0])
        ax_oos = fig.add_subplot(gs[row_idx, 1])

        title_base = (f"gm={row['grid_mult']} lv={int(row['max_levels'])} "
                      f"eh={int(row['exit_h'])}h")

        if eq_is:
            ax_is.plot(eq_is, linewidth=0.9, color='steelblue')
        ax_is.set_title(f'{title_base}  [IS]\n'
                        f'PF={row["IS_PF"]}  WR={row["IS_WR"]}  '
                        f'n={int(row["IS_n_trades"])}  '
                        f'DD={row["IS_max_dd_pct"]}%', fontsize=7.5)
        ax_is.set_ylabel('Equity (JPY)'); ax_is.grid(True, alpha=0.3)
        ax_is.axhline(0, color='gray', lw=0.5, ls='--')

        if eq_oos:
            ax_oos.plot(eq_oos, linewidth=0.9, color='tomato')
        ax_oos.set_title(f'{title_base}  [OOS]\n'
                         f'PF={row["OOS_PF"]}  WR={row["OOS_WR"]}  '
                         f'n={int(row["OOS_n_trades"])}  '
                         f'DD={row["OOS_max_dd_pct"]}%  '
                         f'decay={row["PF_decay"]}', fontsize=7.5)
        ax_oos.set_ylabel('Equity (JPY)'); ax_oos.grid(True, alpha=0.3)
        ax_oos.axhline(0, color='gray', lw=0.5, ls='--')

    plt.suptitle('IS/OOS Equity Curves — Top 3 IS params  (NZDUSD / B-exit / CI filter)',
                 fontsize=10, y=1.01)
    plt.savefig(CHART_PATH, dpi=100, bbox_inches='tight')
    print(f'\n[INFO] Chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
