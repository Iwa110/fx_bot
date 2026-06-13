"""
grid_dukas_stress_wfo.py - Step A: Dukascopy 11年 テールストレス + WFO + 感応度 + ギャップ分布。

真値 = Dukascopy 1h (Step 0 で確定)。エンジン = grid_floatstop_bt.run_backtest (確定ロジック,
float-stop は intrabar adverse extreme 検知＝ギャップ貫通を保守的に許容, next-bar fill, flag t-1)。

出力(4ブロック):
  A1. 主要テール局面 (実在イベント窓) 別の PF/maxDD/worst/nFS  (合成注入なし)
  A2. WFO: IS=4yr → OOS=1yr ローリング再最適化。各foldで選定パラメータとOOS PFを記録し
      パラメータ安定性(期間で飛ぶ=過適合)を検出。
  A3. パラメータ感応度: v7近傍で atr_mult/ci_th/max_levels/float_stop を ±1段振り、PFの崖を検出。
  A4. float-stop ギャップ貫通分布: 全FSイベントの worst単発/float_stop 比 = 設定超過率。

実行: python3 optimizer/grid_dukas_stress_wfo.py
出力: grid_dukas_stress_events.csv / grid_dukas_wfo.csv / grid_dukas_sensitivity.csv /
      grid_dukas_gap_dist.csv + console
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent
PAIRS = ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD']

# 実在テール局面 (合成注入は不要 = データ内に実在)
EVENTS = [
    ('2016_Brexit',        '2016-06-20', '2016-07-08'),
    ('2016_GBP_flash',     '2016-10-06', '2016-10-10'),
    ('2018_VolXmas',       '2018-12-01', '2018-12-31'),
    ('2019_JPY_flash',     '2019-01-02', '2019-01-04'),
    ('2020_COVID',         '2020-02-20', '2020-04-15'),
    ('2022_UKgilt_BoE',    '2022-09-20', '2022-10-20'),
    ('2022_JPY_interv',    '2022-09-20', '2022-11-10'),
    ('2024_carry_unwind',  '2024-07-25', '2024-08-12'),
    ('2024_JPY_interv',    '2024-04-25', '2024-05-10'),
]


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


def bt(pair, cfg, df, atr, ci, lo=None, hi=None):
    sub = slice_win(df, lo, hi)
    if len(sub) < 200:
        return None
    return G.run_backtest(pair, cfg, sub, atr, ci) | {'bars': len(sub)}


# ---------- A1: event stress ----------
def run_events(dfs, atrs, cis):
    rows = []
    print('=== A1. 実在テール局面ストレス (v7構成 / Dukascopy 11年) ===')
    for pair in PAIRS:
        print(f'\n--- {pair} ---')
        print(f'{"event":18s} {"PF":>6s} {"net":>12s} {"maxDD":>12s} {"worst":>12s} {"nTP":>4s} {"nFS":>4s} {"nB48":>4s}')
        for name, lo, hi in EVENTS:
            r = bt(pair, GI.V7_CONFIG[pair], dfs[pair], atrs[pair], cis[pair], lo, hi)
            if r is None:
                continue
            print(f'{name:18s} {r["pf"]:6.2f} {r["total_pnl"]:12,.0f} {r["max_dd"]:12,.0f} '
                  f'{r["worst_event"]:12,.0f} {r["n_tp"]:4d} {r["n_fstop"]:4d} {r["n_b48"]:4d}')
            rows.append({'pair': pair, 'event': name, 'pf': r['pf'], 'net': r['total_pnl'],
                         'maxDD': r['max_dd'], 'worst': r['worst_event'],
                         'nTP': r['n_tp'], 'nFS': r['n_fstop'], 'nB48': r['n_b48'], 'bars': r['bars']})
    pd.DataFrame(rows).to_csv(OUT / 'grid_dukas_stress_events.csv', index=False)
    return rows


# ---------- A2: WFO ----------
ATR_GRID = [1.0, 1.5, 2.0]
LV_GRID = [3, 5, 7]
FS_GRID_JPY = [-500_000.0, -1_000_000.0, -1_500_000.0]
CI_FIX = 61.8


def param_grid(pair):
    qj = GI.V7_CONFIG[pair]['quote_jpy']
    combos = []
    for a in ATR_GRID:
        for lv in LV_GRID:
            for fs in FS_GRID_JPY:
                combos.append({'atr_mult': a, 'ci_threshold': CI_FIX, 'b48_hours': 48,
                               'lot': 1.0, 'max_levels': lv, 'float_stop': fs, 'quote_jpy': qj})
    return combos


def run_wfo(dfs, atrs, cis):
    rows = []
    print('\n=== A2. WFO (IS=4yr rolling → OOS=1yr, 各foldで再最適化) ===')
    print('  IS選定基準: net>0 かつ n_tp>=20 の中で PF最大')
    oos_years = list(range(2019, 2026))   # 2019..2025
    for pair in PAIRS:
        print(f'\n--- {pair} ---')
        print(f'{"OOSyr":6s} {"IS_pf":>6s} {"sel(atr/lv/fs)":>16s} {"OOS_pf":>7s} {"OOS_net":>12s} '
              f'{"OOS_DD":>11s} {"OOS_worst":>11s} {"nFS":>4s}')
        combos = param_grid(pair)
        for oy in oos_years:
            is_lo = f'{oy-4}-01-01'
            is_hi = f'{oy-1}-12-31'
            best = None
            for cfg in combos:
                r = bt(pair, cfg, dfs[pair], atrs[pair], cis[pair], is_lo, is_hi)
                if r is None or r['total_pnl'] <= 0 or r['n_tp'] < 20:
                    continue
                if best is None or r['pf'] > best[1]['pf']:
                    best = (cfg, r)
            if best is None:
                print(f'{oy:6d}  [IS適格構成なし]')
                rows.append({'pair': pair, 'oos_year': oy, 'is_qualified': 0})
                continue
            cfg, isr = best
            oos = bt(pair, cfg, dfs[pair], atrs[pair], cis[pair], f'{oy}-01-01', f'{oy}-12-31')
            if oos is None:
                continue
            tag = f'{cfg["atr_mult"]}/{cfg["max_levels"]}/{cfg["float_stop"]/1e6:.2f}M'
            print(f'{oy:6d} {isr["pf"]:6.2f} {tag:>16s} {oos["pf"]:7.2f} {oos["total_pnl"]:12,.0f} '
                  f'{oos["max_dd"]:11,.0f} {oos["worst_event"]:11,.0f} {oos["n_fstop"]:4d}')
            rows.append({'pair': pair, 'oos_year': oy, 'is_qualified': 1,
                         'is_pf': isr['pf'], 'sel_atr': cfg['atr_mult'], 'sel_lv': cfg['max_levels'],
                         'sel_fs': cfg['float_stop'], 'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'],
                         'oos_dd': oos['max_dd'], 'oos_worst': oos['worst_event'], 'oos_nfs': oos['n_fstop']})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / 'grid_dukas_wfo.csv', index=False)
    # 集計: OOS PF中央値, 適格fold率, パラメータ安定性
    print('\n  --- WFO 集計 (OOS) ---')
    print(f'{"pair":7s} {"folds":>5s} {"qual":>4s} {"OOS_pf_med":>10s} {"OOS_pf_min":>10s} {"OOSnet_sum":>12s} {"param_stable?":>13s}')
    summ = []
    for pair in PAIRS:
        sub = df[(df['pair'] == pair) & (df['is_qualified'] == 1)]
        if len(sub) == 0:
            print(f'{pair:7s}  [適格fold無し]'); continue
        stable = (sub['sel_atr'].nunique() == 1 and sub['sel_lv'].nunique() == 1 and sub['sel_fs'].nunique() == 1)
        atrset = '/'.join(map(str, sorted(sub['sel_atr'].unique())))
        lvset = '/'.join(map(str, sorted(sub['sel_lv'].unique())))
        print(f'{pair:7s} {len(sub):5d} {len(sub):4d} {sub["oos_pf"].median():10.2f} '
              f'{sub["oos_pf"].min():10.2f} {sub["oos_net"].sum():12,.0f} '
              f'{("YES" if stable else "NO atr["+atrset+"] lv["+lvset+"]"):>13s}')
        summ.append({'pair': pair, 'oos_folds': len(sub), 'oos_pf_med': round(sub['oos_pf'].median(), 3),
                     'oos_pf_min': round(sub['oos_pf'].min(), 3), 'oos_net_sum': sub['oos_net'].sum(),
                     'oos_pf_gt1_frac': round((sub['oos_pf'] > 1.0).mean(), 2),
                     'oos_pf_gt12_frac': round((sub['oos_pf'] > 1.2).mean(), 2),
                     'param_stable': stable, 'sel_atr_set': atrset, 'sel_lv_set': lvset})
    pd.DataFrame(summ).to_csv(OUT / 'grid_dukas_wfo_summary.csv', index=False)
    return rows


# ---------- A3: sensitivity ----------
def run_sensitivity(dfs, atrs, cis):
    rows = []
    print('\n=== A3. パラメータ感応度 (v7近傍 ±1段, full 11yr) ===')
    for pair in PAIRS:
        base = GI.V7_CONFIG[pair]
        b = bt(pair, base, dfs[pair], atrs[pair], cis[pair])
        print(f'\n--- {pair}  v7 base PF={b["pf"]:.2f} net={b["total_pnl"]:,.0f} ---')
        variants = []
        for a in sorted(set([base['atr_mult']] + ATR_GRID)):
            variants.append(('atr_mult', a, {**base, 'atr_mult': a}))
        for lv in sorted(set([base['max_levels']] + LV_GRID)):
            variants.append(('max_levels', lv, {**base, 'max_levels': lv}))
        for ci in [61.8, 65.0]:
            variants.append(('ci_threshold', ci, {**base, 'ci_threshold': ci}))
        for fmul in [0.5, 1.0, 1.5]:
            fs = round(base['float_stop'] * fmul, 0)
            variants.append(('float_stop', fs, {**base, 'float_stop': fs}))
        print(f'  {"param":13s} {"value":>12s} {"PF":>6s} {"net":>12s} {"maxDD":>12s} {"worst":>12s} {"nFS":>4s}')
        for pname, val, cfg in variants:
            r = bt(pair, cfg, dfs[pair], atrs[pair], cis[pair])
            mark = ' <-v7' if (val == base.get(pname)) else ''
            print(f'  {pname:13s} {val:12} {r["pf"]:6.2f} {r["total_pnl"]:12,.0f} '
                  f'{r["max_dd"]:12,.0f} {r["worst_event"]:12,.0f} {r["n_fstop"]:4d}{mark}')
            rows.append({'pair': pair, 'param': pname, 'value': val, 'pf': r['pf'],
                         'net': r['total_pnl'], 'maxDD': r['max_dd'], 'worst': r['worst_event'],
                         'nFS': r['n_fstop'], 'is_v7': val == base.get(pname)})
    pd.DataFrame(rows).to_csv(OUT / 'grid_dukas_sensitivity.csv', index=False)
    return rows


# ---------- A4: gap penetration distribution ----------
def run_gap_dist(dfs, atrs, cis):
    rows = []
    print('\n=== A4. float-stop ギャップ貫通分布 (full 11yr v7, worst単発/float_stop比) ===')
    print(f'{"pair":7s} {"fs_set":>10s} {"nFS":>4s} {"med|FS|":>10s} {"max|FS|":>10s} '
          f'{"med_ratio":>9s} {"max_ratio":>9s} {">1.0x":>6s} {">1.5x":>6s}')
    for pair in PAIRS:
        cfg = GI.V7_CONFIG[pair]
        r = bt(pair, cfg, dfs[pair], atrs[pair], cis[pair])
        ev = np.array(r['fs_events'], dtype=float)
        fs = cfg['float_stop']
        if len(ev) == 0:
            print(f'{pair:7s} {fs:10,.0f}    0  (FS発火なし)')
            rows.append({'pair': pair, 'fs_set': fs, 'n_fs': 0})
            continue
        ratio = ev / fs  # both negative → positive ratio; >1 = 設定超過(貫通)
        print(f'{pair:7s} {fs:10,.0f} {len(ev):4d} {np.median(-ev):10,.0f} {(-ev).max():10,.0f} '
              f'{np.median(ratio):9.2f} {ratio.max():9.2f} {(ratio>1.0).mean()*100:5.0f}% {(ratio>1.5).mean()*100:5.0f}%')
        rows.append({'pair': pair, 'fs_set': fs, 'n_fs': len(ev),
                     'med_abs_fs': round(np.median(-ev), 0), 'max_abs_fs': round((-ev).max(), 0),
                     'med_ratio': round(float(np.median(ratio)), 3), 'max_ratio': round(float(ratio.max()), 3),
                     'p95_abs_fs': round(float(np.percentile(-ev, 95)), 0),
                     'p99_abs_fs': round(float(np.percentile(-ev, 99)), 0),
                     'frac_gt_1x': round(float((ratio > 1.0).mean()), 3),
                     'frac_gt_1_5x': round(float((ratio > 1.5).mean()), 3)})
    pd.DataFrame(rows).to_csv(OUT / 'grid_dukas_gap_dist.csv', index=False)
    print('  ratio>1.0 = 単発損が float_stop 設定を超過(ギャップ貫通) → Step Bの証拠金はこの分布で算定')
    return rows


def main():
    dfs, atrs, cis = {}, {}, {}
    for p in PAIRS:
        dfs[p] = load_duk(p)
        atrs[p] = G.compute_atr_series(dfs[p])
        cis[p] = G.compute_ci_series(dfs[p])
    run_events(dfs, atrs, cis)
    run_wfo(dfs, atrs, cis)
    run_sensitivity(dfs, atrs, cis)
    run_gap_dist(dfs, atrs, cis)
    print('\nsaved: grid_dukas_stress_events.csv / grid_dukas_wfo.csv / grid_dukas_wfo_summary.csv'
          ' / grid_dukas_sensitivity.csv / grid_dukas_gap_dist.csv')


if __name__ == '__main__':
    main()
