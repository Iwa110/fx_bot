"""
grid_candidate_validate.py  --  GBPJPY / NZDJPY / CHFJPY 正式検証

スクリーニングで有望だった3ペアを詳細検証:
  Part 1: IS(70%) grid search (9combo) + OOS(30%) validation
  Part 2: Walk-forward (WF1: IS50%→OOS前半, WF2: IS75%→OOS後半)
  Part 3: CI閾値スイープ (best IS combo固定)

確定フレームワーク: exit=B48 / use_adx=False / use_relaltr=False

Output: optimizer/grid_candidate_result.csv
        optimizer/grid_candidate_charts.png
"""

import itertools
import sys
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from grid_bt     import load_ref_rates, load_pair_data
from grid_bt_v2  import build_indicators, run_backtest

warnings.filterwarnings('ignore')

OUTPUT_DIR  = Path(__file__).parent
OUTPUT_CSV  = str(OUTPUT_DIR / 'grid_candidate_result.csv')
CHART_PATH  = str(OUTPUT_DIR / 'grid_candidate_charts.png')

PAIRS       = ['GBPJPY', 'NZDJPY', 'CHFJPY']
CI_TH_DEF   = 61.8
EXIT_H      = 48
IS_RATIO    = 0.70
MIN_TRADES  = 10

GM_LIST     = [1.0, 1.5, 2.0]
LV_LIST     = [3, 5, 7]
COMBOS      = list(itertools.product(GM_LIST, LV_LIST))  # 9

CI_TH_LIST  = [38.2, 45, 50, 55, 61.8, 65, 70, 75]


# ---------------------------------------------------------------
def run_one(df, conv, ind, gm, lv, ci_th=CI_TH_DEF):
    m, eq = run_backtest(
        df, conv, ind,
        grid_mult    = gm,
        max_levels   = int(lv),
        exit_type    = 'B',
        exit_param   = EXIT_H,
        use_ci       = True,
        use_adx      = False,
        use_relaltr  = False,
        ci_threshold = ci_th,
    )
    return m, (eq if eq else [])


def slice_data(df, conv, ind, s, e):
    return df.iloc[s:e], conv[s:e], {k: v[s:e] for k, v in ind.items()}


def pf_str(m, min_n=MIN_TRADES):
    if m and m['n_trades'] >= min_n:
        return f'{m["PF"]:.3f}'
    return 'nan'


# ---------------------------------------------------------------
def main():
    print('[INFO] Loading reference JPY rates...')
    ref_rates = load_ref_rates()

    all_rows     = []
    pair_results = {}

    for pair in PAIRS:
        print(f'\n{"="*65}')
        print(f'  [{pair}] 正式検証')
        print('='*65)

        df, conv = load_pair_data(pair, ref_rates)
        if df is None:
            print('  -> データなし、スキップ')
            continue

        n   = len(df)
        s70 = int(n * IS_RATIO)
        s50 = int(n * 0.50)
        s75 = int(n * 0.75)

        print(f'  bars={n}  {df.index[0].date()} ~ {df.index[-1].date()}')
        print(f'  IS (70%): {df.index[0].date()} ~ {df.index[s70-1].date()}  ({s70} bars)')
        print(f'  OOS(30%): {df.index[s70].date()} ~ {df.index[-1].date()}  ({n-s70} bars)')

        print('  computing indicators...', end=' ', flush=True)
        ind = build_indicators(df)
        print('done')

        df_is,  conv_is,  ind_is  = slice_data(df, conv, ind, 0,   s70)
        df_oos, conv_oos, ind_oos = slice_data(df, conv, ind, s70, n)

        # =====================================================
        # Part 1: IS grid search + OOS validation
        # =====================================================
        print(f'\n[Part 1] IS grid search (9 combos x IS/OOS/full)')
        print(f'  {"gm":>4} {"lv":>3} | {"IS_PF":>7} {"IS_n":>5} | '
              f'{"OOS_PF":>7} {"OOS_n":>5} {"decay":>7} | '
              f'{"full_PF":>8} {"full_n":>6}')
        print('  ' + '-'*65)

        best_is_pf  = -np.inf
        best_combo  = None
        is_results  = {}
        eq_map_is   = {}
        eq_map_oos  = {}
        eq_map_full = {}

        for gm, lv in COMBOS:
            m_is,   eq_is   = run_one(df_is,  conv_is,  ind_is,  gm, lv)
            m_oos,  eq_oos  = run_one(df_oos, conv_oos, ind_oos, gm, lv)
            m_full, eq_full = run_one(df,     conv,     ind,     gm, lv)

            is_results[(gm, lv)]  = (m_is, m_oos, m_full)
            eq_map_is[(gm, lv)]   = eq_is
            eq_map_oos[(gm, lv)]  = eq_oos
            eq_map_full[(gm, lv)] = eq_full

            is_pf  = m_is['PF']          if m_is  and m_is['n_trades']  >= MIN_TRADES else np.nan
            is_n   = m_is['n_trades']     if m_is  else 0
            oos_pf = m_oos['PF']          if m_oos and m_oos['n_trades'] >= MIN_TRADES else np.nan
            oos_n  = m_oos['n_trades']    if m_oos else 0
            oos_dd = m_oos['max_dd_pct']  if m_oos and m_oos['n_trades'] >= MIN_TRADES else np.nan
            full_pf= m_full['PF']         if m_full and m_full['n_trades'] >= MIN_TRADES else np.nan
            full_n = m_full['n_trades']   if m_full else 0
            decay  = round(oos_pf / is_pf, 3) if (not np.isnan(is_pf) and not np.isnan(oos_pf) and is_pf > 0) else np.nan

            marker = ''
            if not np.isnan(is_pf) and is_pf > best_is_pf:
                best_is_pf = is_pf
                best_combo = (gm, lv)
                marker = ' <-'

            is_pf_s  = f'{is_pf:.3f}'  if not np.isnan(is_pf)  else '  nan'
            oos_pf_s = f'{oos_pf:.3f}' if not np.isnan(oos_pf) else '  nan'
            decay_s  = f'{decay:.3f}'  if not np.isnan(decay)  else '  nan'
            full_pf_s= f'{full_pf:.3f}'if not np.isnan(full_pf)else '  nan'

            print(f'  {gm:>4.1f} {int(lv):>3} | '
                  f'{is_pf_s:>7} {is_n:>5} | '
                  f'{oos_pf_s:>7} {oos_n:>5} {decay_s:>7} | '
                  f'{full_pf_s:>8} {full_n:>6}{marker}')

            all_rows.append({
                'pair': pair, 'gm': gm, 'lv': lv,
                'full_PF':  full_pf, 'full_n':  full_n,
                'full_PnL': m_full['total_pnl_jpy'] if m_full else 0,
                'full_DD':  m_full['max_dd_pct']    if m_full else np.nan,
                'IS_PF':    is_pf,   'IS_n':   is_n,
                'IS_PnL':   m_is['total_pnl_jpy']   if m_is  else 0,
                'OOS_PF':   oos_pf,  'OOS_n':  oos_n,
                'OOS_PnL':  m_oos['total_pnl_jpy']  if m_oos and m_oos['n_trades'] >= MIN_TRADES else 0,
                'OOS_DD':   oos_dd,
                'PF_decay': decay,
                'is_best':  (gm, lv) == best_combo,
            })

        if best_combo is None:
            print('  -> 有効なISコンボなし')
            continue

        bg, bl = best_combo
        m_is_b, m_oos_b, m_full_b = is_results[best_combo]
        print(f'\n  IS best → gm={bg} lv={bl}  '
              f'IS_PF={pf_str(m_is_b)}  '
              f'OOS_PF={pf_str(m_oos_b)}  '
              f'OOS_n={m_oos_b["n_trades"] if m_oos_b else 0}  '
              f'OOS_DD={m_oos_b["max_dd_pct"]:.1f}%' if m_oos_b and m_oos_b['n_trades'] >= MIN_TRADES else
              f'\n  IS best → gm={bg} lv={bl}  IS_PF={pf_str(m_is_b)}  OOS_PF=nan')

        # =====================================================
        # Part 2: Walk-forward
        # =====================================================
        print(f'\n[Part 2] Walk-forward')
        wf_windows = [
            ('WF1', 0, s50, s50, s70),
            ('WF2', 0, s75, s75, n),
        ]
        wf_best_list = []

        for tag, is_s, is_e, oo_s, oo_e in wf_windows:
            dfi, ci_arr, idi = slice_data(df, conv, ind, is_s, is_e)
            dfo, co_arr, ido = slice_data(df, conv, ind, oo_s, oo_e)

            # find best IS
            best_wf_pf  = -np.inf
            best_wf_key = None
            for gm, lv in COMBOS:
                m, _ = run_one(dfi, ci_arr, idi, gm, lv)
                if m and m['n_trades'] >= MIN_TRADES and m['PF'] > best_wf_pf:
                    best_wf_pf  = m['PF']
                    best_wf_key = (gm, lv)

            if best_wf_key is None:
                print(f'  [{tag}] 有効なISコンボなし')
                wf_best_list.append((tag, None, None, None, None))
                continue

            wg, wl = best_wf_key
            m_wf_is,  _ = run_one(dfi, ci_arr, idi, wg, wl)
            m_wf_oos, _ = run_one(dfo, co_arr, ido, wg, wl)

            oos_pf = m_wf_oos['PF'] if m_wf_oos and m_wf_oos['n_trades'] >= MIN_TRADES else float('nan')
            oos_n  = m_wf_oos['n_trades'] if m_wf_oos else 0

            is_date_s  = df.index[is_s].date()
            is_date_e  = df.index[is_e-1].date()
            oo_date_s  = df.index[oo_s].date()
            oo_date_e  = df.index[oo_e-1].date()

            print(f'  [{tag}]  IS={is_date_s}~{is_date_e}  OOS={oo_date_s}~{oo_date_e}')
            print(f'    IS best: gm={wg} lv={wl}  '
                  f'IS_PF={pf_str(m_wf_is)}  IS_n={m_wf_is["n_trades"] if m_wf_is else 0}')
            print(f'    OOS    : PF={oos_pf:.3f}  n={oos_n}')

            wf_best_list.append((tag, wg, wl, m_wf_is, m_wf_oos))

        # =====================================================
        # Part 3: CI threshold sweep (best IS combo, full period)
        # =====================================================
        print(f'\n[Part 3] CI閾値スイープ (gm={bg} lv={bl}, full period)')
        print(f'  {"CI_th":>6} | {"PF":>7} {"n":>5} {"PnL/1k":>8}')
        print('  ' + '-'*30)

        ci_rows = []
        for ci_th in CI_TH_LIST:
            m, _ = run_one(df, conv, ind, bg, bl, ci_th=ci_th)
            n_v   = m['n_trades']          if m else 0
            pf_v  = m['PF']                if m and n_v >= MIN_TRADES else float('nan')
            pnl_v = m['total_pnl_jpy']/1000 if m and n_v >= MIN_TRADES else float('nan')
            marker = ' <-- default' if ci_th == 61.8 else ''
            pf_s  = f'{pf_v:.3f}' if not np.isnan(pf_v) else '  nan'
            pnl_s = f'{pnl_v:.1f}' if not np.isnan(pnl_v) else '   nan'
            print(f'  {ci_th:>6.1f} | {pf_s:>7} {n_v:>5} {pnl_s:>8}{marker}')
            ci_rows.append({'ci_th': ci_th, 'PF': pf_v, 'n': n_v, 'PnL_k': pnl_v})

        pair_results[pair] = {
            'best_combo':  best_combo,
            'is_results':  is_results,
            'eq_map_is':   eq_map_is,
            'eq_map_oos':  eq_map_oos,
            'ci_rows':     ci_rows,
            'wf_best':     wf_best_list,
        }

    # ---- Save CSV ----
    if all_rows:
        df_res = pd.DataFrame(all_rows)
        df_res.to_csv(OUTPUT_CSV, index=False)
        print(f'\n[INFO] Saved: {OUTPUT_CSV}  ({len(df_res)} rows)')

    # ---- Summary table ----
    print('\n' + '='*65)
    print('=== 候補ペア IS-best 結果サマリー ===')
    print(f'{"pair":>8} {"gm":>4} {"lv":>3} | {"IS_PF":>7} {"IS_n":>5} | '
          f'{"OOS_PF":>7} {"OOS_n":>5} {"OOS_DD%":>8} {"decay":>7}')
    print('-'*65)
    for pair, pr in pair_results.items():
        bc = pr['best_combo']
        if bc is None:
            continue
        bg, bl = bc
        m_is_b, m_oos_b, _ = pr['is_results'][bc]
        is_pf  = m_is_b['PF']         if m_is_b  else float('nan')
        is_n   = m_is_b['n_trades']   if m_is_b  else 0
        oos_pf = m_oos_b['PF']        if m_oos_b and m_oos_b['n_trades'] >= MIN_TRADES else float('nan')
        oos_n  = m_oos_b['n_trades']  if m_oos_b else 0
        oos_dd = m_oos_b['max_dd_pct']if m_oos_b and m_oos_b['n_trades'] >= MIN_TRADES else float('nan')
        decay  = oos_pf / is_pf if (not np.isnan(is_pf) and not np.isnan(oos_pf) and is_pf > 0) else float('nan')
        is_pf_s  = f'{is_pf:.3f}'  if not np.isnan(is_pf)  else '  nan'
        oos_pf_s = f'{oos_pf:.3f}' if not np.isnan(oos_pf) else '  nan'
        oos_dd_s = f'{oos_dd:.1f}' if not np.isnan(oos_dd) else ' nan'
        decay_s  = f'{decay:.3f}'  if not np.isnan(decay)  else '  nan'
        print(f'{pair:>8} {bg:>4.1f} {int(bl):>3} | '
              f'{is_pf_s:>7} {is_n:>5} | '
              f'{oos_pf_s:>7} {oos_n:>5} {oos_dd_s:>8} {decay_s:>7}')

    # ---- Chart ----
    n_pairs = len(pair_results)
    if n_pairs == 0:
        print('[WARN] No results to chart.')
        return

    fig = plt.figure(figsize=(15, 4.5 * n_pairs))
    gs  = gridspec.GridSpec(n_pairs, 3, figure=fig, hspace=0.65, wspace=0.4)

    for p_idx, (pair, pr) in enumerate(pair_results.items()):
        bc     = pr['best_combo']
        bg, bl = bc
        m_is_b, m_oos_b, _ = pr['is_results'][bc]
        eq_is   = pr['eq_map_is'][bc]
        eq_oos  = pr['eq_map_oos'][bc]

        # col 0: IS equity
        ax = fig.add_subplot(gs[p_idx, 0])
        if eq_is:
            ax.plot(eq_is, lw=0.9, color='steelblue')
        ax.axhline(0, color='gray', ls='--', lw=0.5)
        is_pf_v = m_is_b['PF'] if m_is_b else float('nan')
        is_n_v  = m_is_b['n_trades'] if m_is_b else 0
        is_dd_v = m_is_b['max_dd_pct'] if m_is_b else float('nan')
        ax.set_title(f'{pair}  IS (gm={bg} lv={bl})\n'
                     f'PF={is_pf_v:.3f}  n={is_n_v}  DD={is_dd_v:.1f}%', fontsize=8)
        ax.set_ylabel('Equity (JPY)'); ax.grid(True, alpha=0.3)

        # col 1: OOS equity
        ax = fig.add_subplot(gs[p_idx, 1])
        oos_pf_v = m_oos_b['PF']         if m_oos_b and m_oos_b['n_trades'] >= MIN_TRADES else float('nan')
        oos_n_v  = m_oos_b['n_trades']   if m_oos_b else 0
        oos_dd_v = m_oos_b['max_dd_pct'] if m_oos_b and m_oos_b['n_trades'] >= MIN_TRADES else float('nan')
        if eq_oos and not np.isnan(oos_pf_v):
            ax.plot(eq_oos, lw=0.9, color='tomato')
        ax.axhline(0, color='gray', ls='--', lw=0.5)
        oos_pf_s2 = f'{oos_pf_v:.3f}' if not np.isnan(oos_pf_v) else 'nan'
        oos_dd_s2 = f'{oos_dd_v:.1f}' if not np.isnan(oos_dd_v) else 'nan'
        ax.set_title(f'{pair}  OOS (gm={bg} lv={bl})\n'
                     f'PF={oos_pf_s2}  n={oos_n_v}  DD={oos_dd_s2}%', fontsize=8)
        ax.set_ylabel('Equity (JPY)'); ax.grid(True, alpha=0.3)

        # col 2: CI sweep
        ax = fig.add_subplot(gs[p_idx, 2])
        ci_df = pd.DataFrame(pr['ci_rows']).dropna(subset=['PF'])
        if not ci_df.empty:
            ax.plot(ci_df['ci_th'], ci_df['PF'], 'o-', color='forestgreen', lw=1.5, ms=5)
        ax.axvline(61.8, color='red',    ls='--', lw=1.2, label='61.8')
        ax.axhline(1.3,  color='orange', ls=':',  lw=1,   label='PF=1.3')
        ax.axhline(1.0,  color='gray',   ls=':',  lw=1)
        ax.set_title(f'{pair}  CI sweep (gm={bg} lv={bl}, full)', fontsize=8)
        ax.set_xlabel('CI threshold'); ax.set_ylabel('PF (full period)')
        ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    plt.suptitle(
        'Candidate Pairs Formal Validation  '
        '(B48 exit / IS-best combo / WF / CI sweep)',
        fontsize=11, y=1.01)
    plt.savefig(CHART_PATH, dpi=110, bbox_inches='tight')
    print(f'[INFO] Chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
