"""
grid_live_margin_concurrency.py - 「全ペア同時満玉」が史実でどれだけ起きたかの測定。

問い: 実口座 lot は「3本同時に満玉ラダー」を暗黙の worst case に証拠金安全化している。
それが 11年 Dukascopy でどの程度実在したか(=満玉前提が過大に安全側か)を定量化する。

方法: 確定 live 3本(AUDCAD/CADCHF/EURGBP)を grid_joint_exposure_cap の joint エンジンで
統一タイムライン上に同時シミュレート(cap=None=standalone一致)。各バーで全ペアの open
ラダーの必要証拠金(25倍・live lot・概算レート)を合算し、その時系列の分布を出す。

出力: 各バー合算証拠金の分布(max/p99.99/.../median)・自己資本50万での維持率・
全3本が同時に深いラダーを持つバー割合・ペア別 最大同時ラダー深度。

⚠️ 証拠金は base 通貨額÷25。base->JPY は概算固定レートを使用(下記)。concurrency の
分布(=どれだけ同時に建つか)が主眼で、実運用の安全は実行時ガードが実レートで担保。

実行: .venv_dukas/bin/python optimizer/grid_live_margin_concurrency.py
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
import grid_joint_exposure_cap as EC

OUT = Path(__file__).resolve().parent / 'grid_live_margin_concurrency_result.csv'

LIVE_PAIRS = ['AUDCAD', 'CADCHF', 'EURGBP']
LIVE_LOT   = {'AUDCAD': 0.15, 'CADCHF': 0.05, 'EURGBP': 0.05}
# base 通貨 100k 単位 * base->JPY 概算レート / 25倍 = 1.0lot の必要証拠金(JPY)
MARGIN_PER_LOT_25X = {'AUDCAD': 100_000 * 96 / 25,    # base AUD, AUDJPY~96
                      'CADCHF': 100_000 * 107 / 25,   # base CAD, CADJPY~107
                      'EURGBP': 100_000 * 162 / 25}   # base EUR, EURJPY~162
EQUITY = 500_000.0
GUARD_LEVEL = 150.0


def main():
    defs = [d for d in EC.build_defs() if d[0] in LIVE_PAIRS]
    # joint エンジンを instrument: run_joint と同手順で per-bar 合算証拠金を記録
    pairs = {}
    for pair, cfg, kwfn in defs:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr); kw = kwfn(df, atr)
        st = EC.PairState(pair, cfg, kw)
        pairs[pair] = {'st': st, 'df': df, 'av': atr.reindex(df.index).to_numpy(),
                       'cv': ci.reindex(df.index).to_numpy(), 'r24': r24, 'sb': kw.get('short_block_up'),
                       'highs': df['high'].to_numpy(), 'lows': df['low'].to_numpy(),
                       'closes': df['close'].to_numpy(), 'idx': df.index, 'ptr': 0, 'n': len(df)}

    def pair_margin(name):
        st = pairs[name]['st']
        lot_sum = sum(p['lot'] for p in st.long_pos) + sum(p['lot'] for p in st.short_pos)  # engine base_lot=1.0
        return LIVE_LOT[name] * MARGIN_PER_LOT_25X[name] * lot_sum

    def pair_depth(name):
        st = pairs[name]['st']
        return max(len(st.long_pos), len(st.short_pos))

    union = pd.DatetimeIndex(sorted(set().union(*[p['df'].index for p in pairs.values()])))
    margins = []       # per-bar 合算証拠金
    depths = {p: [] for p in LIVE_PAIRS}
    n_all3_deep = 0    # 3本とも深度>=4 のバー
    n_any_active = 0
    for ts in union:
        active = False
        for name, P in pairs.items():
            j = P['ptr']
            if j < P['n'] and P['idx'][j] == ts:
                atr = P['av'][j]
                if not (np.isnan(atr) or atr <= 0):
                    P['st'].close_phase(ts, P['highs'][j], P['lows'][j], P['closes'][j], atr)
                    sbv = P['sb'][j] if P['sb'] is not None else None
                    P['st'].short_block_up_val = (sbv is True) if P['sb'] is not None else False
                    P['st'].entry_phase(ts, P['closes'][j], atr, P['cv'][j], P['r24'][j], False, 'level0')
                    active = True
                P['ptr'] = j + 1
        if not active:
            continue
        n_any_active += 1
        m = sum(pair_margin(p) for p in LIVE_PAIRS)
        margins.append(m)
        d = {p: pair_depth(p) for p in LIVE_PAIRS}
        for p in LIVE_PAIRS:
            depths[p].append(d[p])
        if all(d[p] >= 4 for p in LIVE_PAIRS):
            n_all3_deep += 1

    ma = np.array(margins)
    # 理論満玉参照(全3本 lv5 片側): ladder_mult AUDCAD/EURGBP=taper0.7(2.773), CADCHF=flat(5.0)
    full3 = (LIVE_LOT['AUDCAD'] * MARGIN_PER_LOT_25X['AUDCAD'] * 2.7731 +
             LIVE_LOT['CADCHF'] * MARGIN_PER_LOT_25X['CADCHF'] * 5.0 +
             LIVE_LOT['EURGBP'] * MARGIN_PER_LOT_25X['EURGBP'] * 2.7731)

    def lvl(marg):  # 維持率
        return EQUITY / marg * 100.0 if marg > 0 else float('inf')

    print('=' * 96)
    print(f'live 3本 同時オープン証拠金の分布 (11年 {len(ma):,} アクティブ足, 自己資本{EQUITY:,.0f})')
    print('=' * 96)
    print(f'理論満玉(全3本lv5片側) = {full3:,.0f} JPY  -> 維持率 {lvl(full3):.0f}%  '
          f'(ガード{GUARD_LEVEL:.0f}%発動水準)')
    print('-' * 96)
    pcs = [('max', np.max(ma)), ('p99.99', np.percentile(ma, 99.99)),
           ('p99.9', np.percentile(ma, 99.9)), ('p99', np.percentile(ma, 99)),
           ('p95', np.percentile(ma, 95)), ('p90', np.percentile(ma, 90)),
           ('median', np.percentile(ma, 50)), ('mean', ma.mean())]
    rows = []
    for tag, v in pcs:
        pct_of_full = v / full3 * 100
        print(f'  {tag:8s}: 証拠金={v:>9,.0f} JPY  維持率={lvl(v):>6.0f}%  (満玉の{pct_of_full:5.1f}%)')
        rows.append({'stat': tag, 'margin_jpy': round(v, 0), 'margin_level_pct': round(lvl(v), 1),
                     'pct_of_full_ladder': round(pct_of_full, 1)})
    print('-' * 96)
    print(f'  全3本とも深度>=4 のバー: {n_all3_deep:,} / {n_any_active:,} '
          f'({n_all3_deep/n_any_active*100:.3f}%)')
    for p in LIVE_PAIRS:
        dp = np.array(depths[p])
        print(f'  {p}: 最大同時ラダー深度={dp.max()}  平均={dp.mean():.2f}  '
              f'深度>=4の割合={ (dp>=4).mean()*100:.2f}%')
    # 合算証拠金が自己資本の何%を超えた最悪
    print('-' * 96)
    print(f'  合算証拠金の史上最大 = {ma.max():,.0f} = 自己資本の {ma.max()/EQUITY*100:.1f}%  '
          f'(維持率 {lvl(ma.max()):.0f}%)')
    print(f'  ※ 理論満玉357k(維持率140%)に対し、史実ピークは上記。差が「満玉前提の余剰安全」。')

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
