"""
grid_dynamic_eval.py - 動的(状態適応)Grid 5案 vs 静的baseline(AUDCAD atr1.5)の比較検証。

ガードレール:
  - 適応ルールのHP(分位境界/写像値)は IS=2015-2021 のみで決め凍結 → OOS=2022-2026 と
    per-year WFO(2022-2025, frozenルールを各年に適用)で評価。
  - 合否=baselineを OOS PF・WFO中央・maxDD の全てで悪化させず、かつ1指標で有意改善。
  - 崖隣接スパイク禁止(採用候補は近傍も確認)。
  - 状態量は t-1 のみ(grid_dynamic_bt.atr_regime_prev / ci_prev は shift(1) 済)。

主対象=AUDCAD(唯一のGo)。副次でNZDJPYも1ケース確認。
真値=Dukascopy。エンジン=grid_dynamic_bt.run_backtest_dynamic(static整合テスト済)。

実行: python3 optimizer/grid_dynamic_eval.py
出力: grid_dynamic_eval_result.csv + console
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dynamic_bt as D

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_dynamic_eval_result.csv'

IS_WIN = ('2015-01-01', '2021-12-31')
OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]   # frozen-on-IS ルールの純OOS年
BACKBONE_ATR = 1.5                     # 静的最良 = backbone


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def win_mask(df, lo, hi):
    m = pd.Series(True, index=df.index)
    if lo:
        m &= df.index >= pd.Timestamp(lo, tz='UTC')
    if hi:
        m &= df.index <= pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)
    return m.to_numpy()


def run_win(pair, df, atr, ci, arrs, qj, lo=None, hi=None):
    m = win_mask(df, lo, hi)
    sub = df[m]
    if len(sub) < 300:
        return None
    a = {k: v[m] for k, v in arrs.items()}
    return D.run_backtest_dynamic(pair, sub, atr, ci,
                                  a['atr_mult'], a['ci_th'], a['maxlv'], a['fs'], a['lot'], qj)


def metrics(pair, df, atr, ci, arrs, qj):
    """full/IS/OOS + WFO(per OOS year) を返す。"""
    full = run_win(pair, df, atr, ci, arrs, qj)
    oos = run_win(pair, df, atr, ci, arrs, qj, *OOS_WIN)
    isr = run_win(pair, df, atr, ci, arrs, qj, *IS_WIN)
    wfo = []
    for y in WFO_YEARS:
        r = run_win(pair, df, atr, ci, arrs, qj, f'{y}-01-01', f'{y}-12-31')
        if r and r['n_tp'] >= 10:
            wfo.append(r['pf'])
    wfo = np.array(wfo, dtype=float)
    return {
        'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
        'full_nfs': full['n_fstop'], 'full_ntp': full['n_tp'],
        'is_pf': isr['pf'] if isr else float('nan'),
        'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
        'wfo_med': float(np.median(wfo)) if len(wfo) else float('nan'),
        'wfo_min': float(wfo.min()) if len(wfo) else float('nan'),
        'wfo_gt12': float((wfo > 1.2).mean()) if len(wfo) else float('nan'),
        'wfo_each': [round(x, 2) for x in wfo],
    }


def const_arrs(df, cfg):
    n = len(df)
    c = lambda v: np.full(n, float(v))
    return {'atr_mult': c(cfg['atr_mult']), 'ci_th': c(cfg['ci_threshold']),
            'maxlv': c(cfg['max_levels']), 'fs': c(cfg['float_stop']), 'lot': c(cfg['lot'])}


def regime_arrs(df, base_cfg, regime_buckets, mapping, field):
    """regime_buckets(int配列, -1=NaN) に対し mapping[regime]→fieldの値。
    NaN/未割当バケットは base_cfg のデフォルト。"""
    arrs = const_arrs(df, base_cfg)
    out = arrs[field].copy()
    nb = len(mapping)
    for b in range(nb):
        out[regime_buckets == b] = mapping[b]
    arrs[field] = out
    return arrs


def fmt_row(tag, m, base=None):
    def d(v, b):
        if base is None or b is None or np.isnan(v) or np.isnan(b):
            return ''
        return '+' if v >= b else '-'
    s = (f'{tag:26s} full PF={m["full_pf"]:.2f} net={m["full_net"]:>11,.0f} DD={m["full_dd"]:>9,.0f} '
         f'nFS={m["full_nfs"]:2d} | OOS PF={m["oos_pf"]:.2f}{d(m["oos_pf"], base["oos_pf"] if base else None)} '
         f'DD={m["oos_dd"]:>9,.0f} | WFO med={m["wfo_med"]:.2f}{d(m["wfo_med"], base["wfo_med"] if base else None)} '
         f'min={m["wfo_min"]:.2f} >1.2={m["wfo_gt12"]:.2f} {m["wfo_each"]}')
    return s


def verdict(m, base):
    """baseを全指標(OOS PF/WFO med/maxDD)で悪化させず1つで有意改善 → 採用候補。"""
    no_worse = (m['oos_pf'] >= base['oos_pf'] - 0.02 and
                m['wfo_med'] >= base['wfo_med'] - 0.02 and
                m['full_dd'] <= base['full_dd'] * 1.02)
    improved = (m['oos_pf'] >= base['oos_pf'] + 0.05 or
                m['wfo_med'] >= base['wfo_med'] + 0.05 or
                m['full_dd'] <= base['full_dd'] * 0.90)
    if no_worse and improved:
        return 'ADOPT候補'
    if no_worse:
        return '同等(改善なし)'
    return '非改善'


def main():
    pair = 'AUDCAD'
    df = load_duk(pair)
    atr = G.compute_atr_series(df)
    ci = G.compute_ci_series(df)
    qj = GI.V7_CONFIG[pair]['quote_jpy']
    base_cfg = {**GI.V7_CONFIG[pair], 'atr_mult': BACKBONE_ATR}  # 静的最良
    is_mask = win_mask(df, *IS_WIN)

    rows = []

    # ===== baseline =====
    base_m = metrics(pair, df, atr, ci, const_arrs(df, base_cfg), qj)
    print('=' * 110)
    print('BASELINE = 静的最良 AUDCAD atr1.5/ci65/lv5/fs-750k (これを risk-adjusted で超えねば動的化不要)')
    print('=' * 110)
    print(fmt_row('STATIC atr1.5 (baseline)', base_m))
    # 参考: 現行live atr1.0
    live_m = metrics(pair, df, atr, ci, const_arrs(df, GI.V7_CONFIG[pair]), qj)
    print(fmt_row('STATIC atr1.0 (live v7,ref)', live_m, base_m))
    rows.append({'case': 'baseline', 'tag': 'static_atr1.5', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})
    rows.append({'case': 'ref', 'tag': 'static_atr1.0_live', **{k: v for k, v in live_m.items() if k != 'wfo_each'}})

    # 特徴量(t-1)
    vol = D.atr_regime_prev(df, atr, period_price=True)   # realized vol proxy
    ci_p = D.ci_prev(df, ci)

    # ===== Case 1: vol-regime adaptive atr_mult =====
    print('\n' + '=' * 110)
    print('CASE 1: ボラ・レジーム適応 atr_mult (IS tercile vol → low/mid/high で atr_mult 切替)')
    print('=' * 110)
    thr3 = D.freeze_quantile_thresholds(vol, is_mask, [1/3, 2/3])
    buck3 = D.bucketize(vol, thr3)
    print(f'  IS vol terciles(price-norm ATR): {thr3[0]:.5f} / {thr3[1]:.5f}  | bucket分布(full): '
          f'{[int((buck3==b).sum()) for b in range(3)]}')
    c1_maps = {
        'widen_hi[1.5,1.5,2.0]': [1.5, 1.5, 2.0],
        'widen_hi[1.0,1.5,2.0]': [1.0, 1.5, 2.0],
        'narrow_hi[1.5,1.5,1.0]': [1.5, 1.5, 1.0],
        'narrow_hi[2.0,1.5,1.0]': [2.0, 1.5, 1.0],
        'mono[1.25,1.5,1.75]': [1.25, 1.5, 1.75],
    }
    c1_results = {}
    for tag, mp in c1_maps.items():
        arrs = regime_arrs(df, base_cfg, buck3, mp, 'atr_mult')
        m = metrics(pair, df, atr, ci, arrs, qj)
        c1_results[tag] = m
        rows.append({'case': 'c1_vol_atr', 'tag': tag, **{k: v for k, v in m.items() if k != 'wfo_each'}})
    # IS最良を選定
    best1 = max(c1_results, key=lambda t: c1_results[t]['is_pf'])
    for tag, m in c1_results.items():
        mark = ' <- IS最良' if tag == best1 else ''
        print(fmt_row(tag, m, base_m) + f'  [{verdict(m, base_m)}]{mark}')

    # ===== Case 2: vol-target float_stop & lot =====
    print('\n' + '=' * 110)
    print('CASE 2: ボラ・ターゲット lot/float_stop (lot ∝ 1/vol, fs ∝ lot で1イベントリスク一定). backbone atr1.5')
    print('=' * 110)
    vol_med_is = np.nanmedian(vol[is_mask])
    print(f'  IS vol median(target)={vol_med_is:.5f}')
    c2_results = {}
    for tag, (lo_c, hi_c) in {'clip[0.7,1.3]': (0.7, 1.3), 'clip[0.5,1.5]': (0.5, 1.5), 'clip[0.5,2.0]': (0.5, 2.0)}.items():
        scale = np.clip(vol_med_is / vol, lo_c, hi_c)
        scale = np.where(np.isnan(scale), 1.0, scale)
        arrs = const_arrs(df, base_cfg)
        arrs['lot'] = base_cfg['lot'] * scale
        arrs['fs'] = base_cfg['float_stop'] * scale   # fs(負)もlotに比例=levels-to-stop一定
        m = metrics(pair, df, atr, ci, arrs, qj)
        c2_results[tag] = m
        rows.append({'case': 'c2_voltarget', 'tag': tag, **{k: v for k, v in m.items() if k != 'wfo_each'}})
    for tag, m in c2_results.items():
        print(fmt_row(tag, m, base_m) + f'  [{verdict(m, base_m)}]')

    # ===== Case 3: regime-linked max_levels =====
    print('\n' + '=' * 110)
    print('CASE 3: レジーム連動 max_levels (高vol/トレンド→浅, 低vol/レンジ→深). backbone atr1.5')
    print('=' * 110)
    c3_results = {}
    # 3a: vol terciles -> deep in low vol
    for tag, mp in {'vol[7,5,3]': [7, 5, 3], 'vol[7,5,5]': [7, 5, 5], 'vol[5,5,3]': [5, 5, 3]}.items():
        arrs = regime_arrs(df, base_cfg, buck3, mp, 'maxlv')
        m = metrics(pair, df, atr, ci, arrs, qj)
        c3_results[tag] = m
        rows.append({'case': 'c3_maxlv', 'tag': tag, **{k: v for k, v in m.items() if k != 'wfo_each'}})
    # 3b: CI terciles -> trend(low CI) shallow, range(high CI) deep
    ci_thr3 = D.freeze_quantile_thresholds(ci_p, is_mask, [1/3, 2/3])
    ci_buck3 = D.bucketize(ci_p, ci_thr3)
    for tag, mp in {'ci[3,5,7]': [3, 5, 7], 'ci[5,5,7]': [5, 5, 7]}.items():
        arrs = regime_arrs(df, base_cfg, ci_buck3, mp, 'maxlv')
        m = metrics(pair, df, atr, ci, arrs, qj)
        c3_results[tag] = m
        rows.append({'case': 'c3_maxlv', 'tag': tag, **{k: v for k, v in m.items() if k != 'wfo_each'}})
    for tag, m in c3_results.items():
        print(fmt_row(tag, m, base_m) + f'  [{verdict(m, base_m)}]')

    # ===== Case 4: CI adaptive gate =====
    print('\n' + '=' * 110)
    print('CASE 4: CI適応ゲート (固定65 → IS CI分位点で相対化). backbone atr1.5')
    print('=' * 110)
    c4_results = {}
    for q in [0.5, 0.6, 0.7, 0.8]:
        thr = float(D.freeze_quantile_thresholds(ci_p, is_mask, [q])[0])
        arrs = const_arrs(df, base_cfg)
        arrs['ci_th'] = np.full(len(df), thr)
        m = metrics(pair, df, atr, ci, arrs, qj)
        tag = f'ci_q{int(q*100)}(={thr:.1f})'
        c4_results[tag] = m
        rows.append({'case': 'c4_ci', 'tag': tag, **{k: v for k, v in m.items() if k != 'wfo_each'}})
    for tag, m in c4_results.items():
        print(fmt_row(tag, m, base_m) + f'  [{verdict(m, base_m)}]')

    # ===== Case 5: control - rolling re-optimization (素朴WFO) =====
    print('\n' + '=' * 110)
    print('CASE 5(対照群): ローリング再最適化 IS=4yr→OOS=1yr (atr/lv/fs を毎fold再選定). 動的化の上限目安')
    print('=' * 110)
    run_rolling_wfo(pair, df, atr, ci, qj, base_m, rows)

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


def run_rolling_wfo(pair, df, atr, ci, qj, base_m, rows):
    """対照群: 各OOS年について直前4年で再最適化(atr/lv/fs)し当年適用。"""
    ATR_GRID = [1.0, 1.5, 2.0]
    LV_GRID = [3, 5, 7]
    FS_GRID = [-500_000.0, -750_000.0, -1_000_000.0, -1_500_000.0]
    base = GI.V7_CONFIG[pair]
    combos = [{**base, 'atr_mult': a, 'max_levels': lv, 'float_stop': fs}
              for a in ATR_GRID for lv in LV_GRID for fs in FS_GRID]

    def bt(cfg, lo, hi):
        m = win_mask(df, lo, hi)
        sub = df[m]
        if len(sub) < 200:
            return None
        return G.run_backtest(pair, cfg, sub, atr, ci)

    oos_pfs, oos_net = [], 0.0
    print(f'{"OOSyr":6s} {"sel(atr/lv/fs)":>16s} {"IS_pf":>6s} {"OOS_pf":>7s} {"OOS_net":>11s} {"OOS_DD":>10s}')
    for oy in range(2019, 2026):
        best = None
        for cfg in combos:
            r = bt(cfg, f'{oy-4}-01-01', f'{oy-1}-12-31')
            if r is None or r['total_pnl'] <= 0 or r['n_tp'] < 20:
                continue
            if best is None or r['pf'] > best[1]['pf']:
                best = (cfg, r)
        if best is None:
            print(f'{oy:6d}  [IS適格なし]')
            continue
        cfg, isr = best
        oos = bt(cfg, f'{oy}-01-01', f'{oy}-12-31')
        if oos is None:
            continue
        tag = f'{cfg["atr_mult"]}/{cfg["max_levels"]}/{cfg["float_stop"]/1e6:.2f}M'
        print(f'{oy:6d} {tag:>16s} {isr["pf"]:6.2f} {oos["pf"]:7.2f} {oos["total_pnl"]:11,.0f} {oos["max_dd"]:10,.0f}')
        if oy >= 2022:
            oos_pfs.append(oos['pf']); oos_net += oos['total_pnl']
        rows.append({'case': 'c5_rolling_wfo', 'tag': f'{oy}_{tag}', 'oos_pf': oos['pf'],
                     'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd']})
    if oos_pfs:
        arr = np.array(oos_pfs)
        print(f'  純OOS年(2022-2025) WFO: med={np.median(arr):.2f} min={arr.min():.2f} '
              f'>1.2={(arr>1.2).mean():.2f} net_sum={oos_net:,.0f}  vs baseline WFOmed={base_m["wfo_med"]:.2f}')


if __name__ == '__main__':
    main()
