"""
grid_dukas_reconfirm.py - Dukascopy 長期1h上で Grid v7 を再現確認＋長期プレビュー。

目的:
  1. 確定済みv7 PF (yfinance 1h上: GBPJPY1.96/AUDCAD4.01/NZDJPY2.36/CHFJPY1.51) を
     Dukascopy 1h の「同一2年窓 (2024-04-25..2026-04-24)」で再現できるか確認
     (BID側/クォート差で多少ズレるが、大きく乖離しないことを確かめる)。
  2. 同じv7構成を「全長期 (2015〜) / 主要テール年度別」で回し、PF/maxDD/worst単発/
     float-stop発動回数を出す = 本物のテール局面でのGrid生存プレビュー。

エンジンは grid_floatstop_bt.run_backtest (確定ロジック) を流用。configは v7
(grid_insensitivity.V7_CONFIG)。データだけ Dukascopy ローダに差し替える。

実行: python3 optimizer/grid_dukas_reconfirm.py
出力: grid_dukas_reconfirm_result.csv + console
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_dukas_reconfirm_result.csv'

# 確定2年窓 (yfinance版と同じ範囲) と長期/年度窓
WIN_2YR = ('2024-04-25', '2026-04-24')


def load_dukas(pair):
    path = DATA / f'{pair}_1h_dukas.csv'
    df = pd.read_csv(path)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    df = df.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()
    return df


def run_window(pair, cfg, df, lo=None, hi=None):
    sub = df
    if lo is not None:
        sub = sub[sub.index >= pd.Timestamp(lo, tz='UTC')]
    if hi is not None:
        sub = sub[sub.index <= pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)]
    if len(sub) < 500:
        return None
    atr = G.compute_atr_series(sub)
    ci = G.compute_ci_series(sub)
    res = G.run_backtest(pair, cfg, sub, atr, ci)
    res['bars'] = len(sub)
    res['from'] = sub.index[0].date()
    res['to'] = sub.index[-1].date()
    return res


def main():
    rows = []
    pairs = GI.GRID_PAIRS
    # 1) v7 PF (yfinance) と Dukascopy 2年窓の比較
    YF_PF = {'GBPJPY': 1.96, 'AUDCAD': 4.01, 'NZDJPY': 2.36, 'CHFJPY': 1.51}
    print('=== (1) v7 PF 再現確認: yfinance確定 vs Dukascopy 同一2年窓 ===')
    print(f'{"pair":8s} {"yf_PF":>6s} {"duk_PF":>7s} {"duk_net":>13s} {"maxDD":>12s} {"worst":>12s} {"nFS":>4s} {"bars":>6s}')
    dfs = {}
    for pair in pairs:
        try:
            df = load_dukas(pair)
        except FileNotFoundError:
            print(f'{pair:8s}  [data未取得: {pair}_1h_dukas.csv]'); continue
        dfs[pair] = df
        r = run_window(pair, GI.V7_CONFIG[pair], df, *WIN_2YR)
        if r is None:
            print(f'{pair:8s}  [2年窓データ不足]'); continue
        print(f'{pair:8s} {YF_PF[pair]:6.2f} {r["pf"]:7.2f} {r["total_pnl"]:13,.0f} '
              f'{r["max_dd"]:12,.0f} {r["worst_event"]:12,.0f} {r["n_fstop"]:4d} {r["bars"]:6d}')
        rows.append({'scope': '2yr_dukas', 'pair': pair, 'yf_pf': YF_PF[pair], **{k: r[k] for k in
                    ('pf', 'total_pnl', 'max_dd', 'worst_event', 'n_tp', 'n_fstop', 'n_b48', 'from', 'to', 'bars')}})

    # 2) 全長期 + 年度別 (テール年に注目)
    print('\n=== (2) 長期プレビュー (v7構成 / Dukascopy) ===')
    windows = [('FULL', None, None)] + [(str(y), f'{y}-01-01', f'{y}-12-31')
                                        for y in range(2015, 2027)]
    for pair in pairs:
        if pair not in dfs:
            continue
        print(f'\n--- {pair} ---')
        print(f'{"win":6s} {"PF":>6s} {"net":>14s} {"maxDD":>13s} {"worst":>13s} {"nTP":>5s} {"nFS":>4s} {"nB48":>4s} {"span":>23s}')
        for name, lo, hi in windows:
            r = run_window(pair, GI.V7_CONFIG[pair], dfs[pair], lo, hi)
            if r is None:
                continue
            print(f'{name:6s} {r["pf"]:6.2f} {r["total_pnl"]:14,.0f} {r["max_dd"]:13,.0f} '
                  f'{r["worst_event"]:13,.0f} {r["n_tp"]:5d} {r["n_fstop"]:4d} {r["n_b48"]:4d} '
                  f'{str(r["from"])+"~"+str(r["to"]):>23s}')
            rows.append({'scope': name, 'pair': pair, **{k: r[k] for k in
                        ('pf', 'total_pnl', 'max_dd', 'worst_event', 'n_tp', 'n_fstop', 'n_b48', 'from', 'to', 'bars')}})

    if rows:
        pd.DataFrame(rows).to_csv(OUT, index=False)
        print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
