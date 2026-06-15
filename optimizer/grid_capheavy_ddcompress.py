"""
grid_capheavy_ddcompress.py - 候補2: 資本重ペア EURGBP / CADCHF の req_cap_99 圧縮。

ポートフォリオ必要資本は EURGBP / CADCHF の req_cap_99(maxDD分布が拘束)が支配する。
この2本のDDを下げればバスケット効率が最も改善する。float_stop / max_levels / taper /
cull_frac を**この2ペア限定で**再点検し、netを多少譲っても req_cap_99(=DD99) を下げる
構成を探す。

検証規律(厳守):
  - IS=2015-21 で selectable (IS_pf >= baseline IS) を必須(OOS-fit禁止)。
  - 全 WFO fold > 1.0 維持・OOS_pf > 1.2 維持(エッジを壊さない)。
  - n_tp が崩壊(薄標本化)していない / full_pf が崖スパイクでないことを確認。
  - req_cap は候補1(grid_joint_stepb)と同じ暦月基盤・同一MC(block3/60mo/20000)。

ベース構成(2026-06-15 確定):
  EURGBP : combo(mom2.0/cull0.5/taper0.7) + short_lot0.5
  CADCHF : R-SMA1200 (テール優先版は +combo)

実行: .venv_dukas/bin/python optimizer/grid_capheavy_ddcompress.py
出力: grid_capheavy_ddcompress_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY
from grid_joint_stepb import bootstrap, cadchf_cfg, COMBO

OUT = Path(__file__).resolve().parent / 'grid_capheavy_ddcompress_result.csv'
SEED = 42


def req_cap_calendar(pair, cfg, kw):
    """変種の暦月基盤 req_cap_99 (lot=1.0)。アイドル月0埋め。"""
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kw)
    s = pd.Series(res['monthly'])
    cal = pd.period_range(pd.PeriodIndex(s.index, freq='M').min(),
                          pd.PeriodIndex(s.index, freq='M').max(), freq='M').strftime('%Y-%m')
    m = s.reindex(cal).fillna(0.0).to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    mdd, fin = bootstrap(m, rng)
    return float(np.percentile(mdd, 99)), res['total_pnl'] / (len(cal) / 12.0), float((fin < 0).mean())


def evaluate(pair, cfg, base_kw, df, atr, ci, r24, reg, label, override_cfg=None, extra_kw=None):
    """metrics(IS/OOS/WFO) + 暦月 req_cap を返す。"""
    c = dict(cfg)
    if override_cfg:
        c.update(override_cfg)
    kw = dict(base_kw)
    if extra_kw:
        kw.update(extra_kw)
    mkw = dict(kw)
    if 'regimes' in mkw:  # metrics は regimes引数で窓スライス
        pass
    m = DB.metrics(c, df, atr, ci, ret24=r24, **mkw)
    # req_cap は run_bt 直叩き(regimesでなく short_block_up配列を渡す)
    rckw = dict(kw)
    if 'regimes' in rckw:
        del rckw['regimes']
    r99, netyr, p5 = req_cap_calendar(pair, c, rckw)
    return {'pair': pair, 'label': label, 'full_pf': m['full_pf'], 'full_net': m['full_net'],
            'full_dd': m['full_dd'], 'full_nfs': m['full_nfs'], 'full_ntp': m['full_ntp'],
            'is_pf': m['is_pf'], 'oos_pf': m['oos_pf'], 'wfo_min': m['wfo_min'],
            'wfo_each': m['wfo_each'], 'req99_cal': round(r99, 0),
            'netyr_cal': round(netyr, 0), 'p5': round(p5, 4)}


def show(r, base_req=None, base_is=None):
    sel = '✓' if (base_is is not None and not np.isnan(r['is_pf']) and r['is_pf'] >= base_is - 1e-9) else ' '
    wf = '✓' if (not np.isnan(r['wfo_min']) and r['wfo_min'] > 1.0) else 'x'
    oo = '✓' if r['oos_pf'] > 1.2 else 'x'
    dpct = f"{(r['req99_cal']/base_req-1)*100:+5.1f}%" if base_req else '  base'
    print(f"{r['label']:26s} fPF={r['full_pf']:5.2f} IS={r['is_pf'] if not np.isnan(r['is_pf']) else 0:4.2f}{sel} "
          f"OOS={r['oos_pf']:4.2f}{oo} wfoMin={r['wfo_min']:4.2f}{wf} nFS={r['full_nfs']:2d} nTP={r['full_ntp']:4d} "
          f"| req99={r['req99_cal']:>10,.0f} {dpct} net/yr={r['netyr_cal']:>9,.0f} P5={r['p5']:.3f}")


def main():
    rows = []
    specs = [
        ('EURGBP', D.EURGBP, {'short_lot_mult': 0.5, **COMBO}),
        ('CADCHF', cadchf_cfg(), {'short_block_up': '__reg1200__'}),  # R-SMA1200
    ]
    for pair, cfg, base_kw in specs:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        reg1200 = DB.sma_regime(df, 1200)
        # base_kw の '__reg1200__' を実配列に解決(req_cap用)。metrics用は regimes 形式も用意。
        def resolve(kw):
            kw = dict(kw)
            if kw.get('short_block_up') == '__reg1200__':
                kw['short_block_up'] = reg1200
            return kw
        base_kw_r = resolve(base_kw)

        print('=' * 130); print(f'{pair}  base={base_kw}'); print('=' * 130)
        base = evaluate(pair, cfg, base_kw_r, df, atr, ci, r24, reg1200, 'baseline')
        show(base)
        base_req, base_is = base['req99_cal'], base['is_pf']
        rows.append(base)

        variants = []
        # float_stop を締める/緩める(price距離一致のため現fsにスケール)
        for sc in (0.6, 0.75, 0.85, 1.3):
            variants.append((f'fs x{sc}', {'float_stop': round(cfg['float_stop'] * sc, 0)}, {}))
        # max_levels を浅く(ラダー短縮=DD源を断つ)
        for ml in (3, 4):
            variants.append((f'max_lv{ml}', {'max_levels': ml}, {}))
        # taper を強める(深レッグ露出減)
        for tp in (0.6, 0.8):
            variants.append((f'taper{tp}', {}, {'taper': tp}))
        # cull を締める
        for cf in (0.4, 0.6):
            variants.append((f'cull{cf}', {}, {'cull_frac': cf}))
        # CADCHF は base に combo無し → combo追加(テール制御版)も評価
        if pair == 'CADCHF':
            variants.append(('+combo', {}, dict(COMBO)))
            variants.append(('+combo+lv4', {'max_levels': 4}, dict(COMBO)))
            variants.append(('+combo+fs0.75', {'float_stop': round(cfg['float_stop'] * 0.75, 0)}, dict(COMBO)))
        # EURGBP 有望組合せ
        if pair == 'EURGBP':
            variants.append(('lv4+fs0.85', {'max_levels': 4, 'float_stop': round(cfg['float_stop'] * 0.85, 0)}, {}))
            variants.append(('lv3+taper0.6', {'max_levels': 3}, {'taper': 0.6}))

        for label, ovr, extra in variants:
            r = evaluate(pair, cfg, base_kw_r, df, atr, ci, r24, reg1200, label,
                         override_cfg=ovr, extra_kw=extra)
            show(r, base_req, base_is)
            rows.append(r)
        print()

    rdf = pd.DataFrame([{k: v for k, v in r.items() if k != 'wfo_each'} for r in rows])
    rdf.to_csv(OUT, index=False)
    print(f'saved {OUT}')
    print('\n判定: selectable(IS✓) ∧ OOS>1.2(✓) ∧ wfoMin>1.0(✓) ∧ req99↓ ∧ nTP非崩壊 を満たす変種が採用候補。')


if __name__ == '__main__':
    main()
