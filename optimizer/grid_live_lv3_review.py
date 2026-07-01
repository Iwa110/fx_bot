"""
grid_live_lv3_review.py - 方向C(浅ラダー lv3)の IS/OOS/WFO + req_cap 再検証。

目的: 国内25倍で証拠金を空けるため max_levels を 5->3 に浅くしても、平均回帰の
エッジ(IS/OOS/WFO)が保たれるかを確認する。浅化は「深レッグの回復益を削る」既知の
トレードオフがあるため、採用バー(IS-selectable ∧ OOS>1.2 ∧ wfoMin>1.0 ∧ req_cap↓)
で判定する。

対象 = 実口座 live 3本のデプロイ構成(エンジンでモデル可能な範囲):
  AUDCAD : R-SMA1200 + combo(mom2.0/cull0.5/taper0.7)
  CADCHF : R-SMA1200 + cull0.6
  EURGBP : combo + short_lot0.5  ※デプロイ実機は mom120=4 + tp0.8 を追加しているが
           このエンジンは mom120/tp_mult 非対応 -> 近似(proxy)。EURGBP は参考値。

規律: IS=2015-21 凍結 -> OOS=2022-26/年次WFO。req_cap は暦月基盤・同一MC
(grid_capheavy_ddcompress.req_cap_calendar / block3・60mo・20000・seed42)。

実行: .venv_dukas/bin/python optimizer/grid_live_lv3_review.py
出力: grid_live_lv3_review_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_joint_stepb import cadchf_cfg, COMBO
import grid_capheavy_ddcompress as CH

OUT = Path(__file__).resolve().parent / 'grid_live_lv3_review_result.csv'

# pair -> (cfg, regime_n or None, metrics_extra_kw, reqcap_kw(reg)->dict)
SPECS = {
    'AUDCAD': (D.AUDCAD, 1200, dict(COMBO),
               lambda reg: {'short_block_up': reg, **COMBO}),
    'CADCHF': (cadchf_cfg(), 1200, {'cull_frac': 0.6},
               lambda reg: {'short_block_up': reg, 'cull_frac': 0.6}),
    'EURGBP': (D.EURGBP, None, {'short_lot_mult': 0.5, **COMBO},
               lambda reg: {'short_lot_mult': 0.5, **COMBO}),
}


def fmt(pair, lv, m, r99, netyr, p5, base=None):
    sel = wf = oo = dd = ' '
    if base is not None:
        sel = 'OK ' if (not np.isnan(m['is_pf']) and m['is_pf'] >= base['is_pf'] - 1e-9) else 'IS<base'
    wf = 'OK' if (not np.isnan(m['wfo_min']) and m['wfo_min'] > 1.0) else 'x'
    oo = 'OK' if m['oos_pf'] > 1.2 else 'x'
    dpct = '' if base is None else f" req_cap {(r99/base['req99']-1)*100:+5.1f}%"
    print(f"  lv{lv}: fPF={m['full_pf']:5.2f} IS={m['is_pf']:4.2f}[{sel}] OOS={m['oos_pf']:4.2f}[{oo}] "
          f"wfoMin={m['wfo_min']:4.2f}[{wf}] DD={m['full_dd']:>10,.0f} nFS={m['full_nfs']:2d} "
          f"nTP={m['full_ntp']:4d} | req99={r99:>10,.0f}{dpct} net/yr={netyr:>9,.0f} P5={p5:.3f} "
          f"wfo={m['wfo_each']}")


def main():
    rows = []
    for pair, (cfg, reg_n, mkw_extra, rc_builder) in SPECS.items():
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        reg_arr = DB.sma_regime(df, reg_n) if reg_n else None
        print('=' * 128); print(f'{pair}  (regime_short SMA{reg_n})' if reg_n else pair); print('=' * 128)

        base_metrics = None
        for lv in (5, 3):
            c = dict(cfg); c['max_levels'] = lv
            mkw = dict(mkw_extra)
            if reg_arr is not None:
                mkw['regimes'] = {'short_block_up': reg_arr}
                mkw['short_block_up'] = reg_arr
            m = DB.metrics(c, df, atr, ci, ret24=r24, **mkw)
            r99, netyr, p5 = CH.req_cap_calendar(pair, c, rc_builder(reg_arr))
            r = {'pair': pair, 'max_levels': lv, 'full_pf': m['full_pf'], 'full_net': m['full_net'],
                 'full_dd': m['full_dd'], 'full_nfs': m['full_nfs'], 'full_ntp': m['full_ntp'],
                 'is_pf': m['is_pf'], 'oos_pf': m['oos_pf'], 'wfo_min': m['wfo_min'],
                 'wfo_each': str(m['wfo_each']), 'req99': round(r99, 0),
                 'netyr': round(netyr, 0), 'p5': round(p5, 4)}
            if lv == 5:
                base_metrics = {'is_pf': m['is_pf'], 'wfo_min': m['wfo_min'], 'req99': r99}
                fmt(pair, lv, m, r99, netyr, p5)
            else:
                fmt(pair, lv, m, r99, netyr, p5, base_metrics)
            rows.append(r)
        print()

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'saved {OUT}')
    print('\n採用バー(lv3): IS>=lv5 ∧ OOS>1.2 ∧ wfoMin>1.0 ∧ req_cap<lv5 ∧ nTP非崩壊。')
    print('EURGBP は mom120/tp0.8 非モデル化の proxy=参考値。')


if __name__ == '__main__':
    main()
