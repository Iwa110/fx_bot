"""
grid_ci_optimize.py - 通貨ペアごとの CI閾値(ci_threshold)最適化検証。

問い: グリッドのエントリーゲート `ci > ci_threshold` をペア別に最適化すると PF を改善できるか。
過適合を避けるため full 11年だけでなく IS/OOS と WFO-OOS(CIのみ可変・他はv7固定)で評価する。

真値=Dukascopy 11年。エンジン=grid_floatstop_bt.run_backtest(確定v7, ci_thresholdのみ差替)。
ATR/CI系列はペア毎に1度だけ計算(CI閾値変更は系列に不変=ゲート比較のみ変わる)。

評価:
  1) full 11年: PF/net/maxDD/nTP/nFS  (各CIで)
  2) IS(2015-2021) / OOS(2022-2026): IS最良CIがOOSで通用するか
  3) WFO-OOS(2019-2025の7年, 各年をOOSとしv7+当該CIで評価): OOS PF中央値/最小/>1.2率
     → 「ペア別CIを固定採用したときのOOS頑健性」を直接測る

実行: python3 optimizer/grid_ci_optimize.py
出力: grid_ci_optimize_result.csv + console
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_ci_optimize_result.csv'
PAIRS = ['AUDCAD', 'GBPJPY', 'NZDJPY', 'CHFJPY']
CI_GRID = [50.0, 52.5, 55.0, 57.5, 60.0, 61.8, 62.5, 65.0, 67.5, 70.0]
IS_WIN = ('2015-01-01', '2021-12-31')
OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_OOS_YEARS = list(range(2019, 2026))   # 2019..2025


def load_duk(pair):
    df = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    return df.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def slice_win(df, lo, hi):
    s = df
    if lo:
        s = s[s.index >= pd.Timestamp(lo, tz='UTC')]
    if hi:
        s = s[s.index <= pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)]
    return s


def run(pair, ci_th, df, atr, ci, lo=None, hi=None):
    cfg = {**GI.V7_CONFIG[pair], 'ci_threshold': ci_th}
    sub = slice_win(df, lo, hi)
    if len(sub) < 300:
        return None
    return G.run_backtest(pair, cfg, sub, atr, ci)


def main():
    rows = []
    for pair in PAIRS:
        df = load_duk(pair)
        atr = G.compute_atr_series(df)
        ci = G.compute_ci_series(df)
        cur = GI.V7_CONFIG[pair]['ci_threshold']
        print(f'\n========== {pair}  (現行 ci_threshold={cur}) ==========')
        print(f'{"CI":>6s} {"full_PF":>7s} {"full_net":>12s} {"maxDD":>11s} {"nTP":>5s} {"nFS":>4s} '
              f'{"IS_PF":>6s} {"OOS_PF":>7s} {"OOS_net":>11s} {"wfoOOSmed":>9s} {"wfoMin":>7s} {">1.2":>5s}')
        for cth in CI_GRID:
            full = run(pair, cth, df, atr, ci)
            isr = run(pair, cth, df, atr, ci, *IS_WIN)
            oos = run(pair, cth, df, atr, ci, *OOS_WIN)
            # WFO-OOS (CI固定でのOOS頑健性)
            wfo_pfs = []
            for y in WFO_OOS_YEARS:
                r = run(pair, cth, df, atr, ci, f'{y}-01-01', f'{y}-12-31')
                if r and r['n_tp'] >= 10:
                    wfo_pfs.append(r['pf'])
            wfo_pfs = np.array(wfo_pfs, dtype=float)
            wfo_med = np.median(wfo_pfs) if len(wfo_pfs) else float('nan')
            wfo_min = wfo_pfs.min() if len(wfo_pfs) else float('nan')
            wfo_gt12 = (wfo_pfs > 1.2).mean() if len(wfo_pfs) else float('nan')
            mark = ' <-cur' if cth == cur else ''
            print(f'{cth:6.1f} {full["pf"]:7.2f} {full["total_pnl"]:12,.0f} {full["max_dd"]:11,.0f} '
                  f'{full["n_tp"]:5d} {full["n_fstop"]:4d} {isr["pf"]:6.2f} {oos["pf"]:7.2f} '
                  f'{oos["total_pnl"]:11,.0f} {wfo_med:9.2f} {wfo_min:7.2f} {wfo_gt12:5.2f}{mark}')
            rows.append({'pair': pair, 'ci_threshold': cth, 'is_current': cth == cur,
                         'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_maxDD': full['max_dd'],
                         'full_nTP': full['n_tp'], 'full_nFS': full['n_fstop'],
                         'is_pf': isr['pf'], 'is_net': isr['total_pnl'],
                         'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_maxDD': oos['max_dd'],
                         'wfo_oos_pf_med': round(float(wfo_med), 3), 'wfo_oos_pf_min': round(float(wfo_min), 3),
                         'wfo_oos_gt12_frac': round(float(wfo_gt12), 3)})

    rdf = pd.DataFrame(rows)
    rdf.to_csv(OUT, index=False)

    # ---- 要約: 各ペアで IS最良CI を選び OOS / WFO で現行と比較 ----
    print('\n\n=== 要約: ペア別CI最適化の OOS有効性 (IS最良CI vs 現行CI) ===')
    print(f'{"pair":7s} {"curCI":>6s} {"cur_OOSpf":>9s} {"cur_wfoMed":>10s} | '
          f'{"ISbestCI":>8s} {"isb_OOSpf":>9s} {"isb_wfoMed":>10s} {"isb_OOSnet":>11s} | {"判定"}')
    for pair in PAIRS:
        sub = rdf[rdf.pair == pair]
        cur = sub[sub.is_current].iloc[0]
        isbest = sub.loc[sub.is_pf.idxmax()]
        improve = (isbest['oos_pf'] >= cur['oos_pf']) and (isbest['wfo_oos_pf_med'] >= cur['wfo_oos_pf_med'])
        verdict = ('改善(OOS&WFO共に維持/向上)' if improve else 'IS最良はOOSで非改善=過適合注意')
        print(f'{pair:7s} {cur["ci_threshold"]:6.1f} {cur["oos_pf"]:9.2f} {cur["wfo_oos_pf_med"]:10.2f} | '
              f'{isbest["ci_threshold"]:8.1f} {isbest["oos_pf"]:9.2f} {isbest["wfo_oos_pf_med"]:10.2f} '
              f'{isbest["oos_net"]:11,.0f} | {verdict}')

    print('\n  (注) full_PF最大CIは過適合の罠。判定軸は OOS_PF と wfoOOSmed の同時維持/向上。')
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
