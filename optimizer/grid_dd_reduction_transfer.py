"""
grid_dd_reduction_transfer.py - 併用案(mom2.0+cull0.5+taper0.7)の他ペア転移検証。

AUDCAD/EURGBP で確認済みの併用案(grid_dd_reduction_bt.py)を、パラメータ再チューニング
無しで他ペアへ適用し、(1)リスク改善(maxDD/worst/nFS)の構造性 (2)No-Goペアの格上げ有無
を確認する。比較は baseline / mom単独 / 併用 の3段。

設定の出所(全て既存検証の確立済みテンプレ・新規チューニング無し):
  GBPJPY/CHFJPY/NZDJPY : V7_CONFIG (grid_insensitivity.py)
  AUDNZD/EURCHF        : AUDCADテンプレ atr1.5/ci65/lv5, fs=-750k×qj/108 (grid_newpairs_bt.py 準拠)
  USDJPY/EURUSD(探索的): 同テンプレだが fs は ATR中央値×qj 比でスケール
                         (JPY建て高価格ペアは price-distance 換算が破綻するため)

実行: .venv_dukas/bin/python optimizer/grid_dd_reduction_transfer.py
出力: grid_dd_reduction_transfer_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_dd_reduction_transfer_result.csv'

COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}

# 確立済み設定
V7 = GI.V7_CONFIG
TEMPLATE_QJ = {'AUDNZD': 90.0, 'EURCHF': 170.0}        # grid_newpairs_bt.py 準拠
EXPLORE_QJ = {'USDJPY': 1.0, 'EURUSD': 155.0}          # 探索的(Grid未評価ペア)


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def main():
    rows = []
    # AUDCAD の ATR中央値×qj (探索ペアのfsスケール基準)
    df_ac = D.load_duk('AUDCAD')
    atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0

    plan = []
    for p in ['GBPJPY', 'CHFJPY', 'NZDJPY']:
        plan.append((p, {**V7[p]}, 'v7'))
    for p, qj in TEMPLATE_QJ.items():
        plan.append((p, template_cfg(qj, round(-750_000.0 * qj / 108.0, 0)), 'tpl(price)'))
    for p, qj in EXPLORE_QJ.items():
        plan.append((p, None, f'tpl(ATR), qj={qj}'))   # fsはデータ読込後に決定

    for pair, cfg, src in plan:
        df = D.load_duk(pair)
        atr = G.compute_atr_series(df)
        ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        if cfg is None:                                # 探索的ペア: ATR比でfsスケール
            qj = EXPLORE_QJ[pair]
            fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
            cfg = template_cfg(qj, fs)
        print('=' * 132)
        print(f'{pair}  [{src}]  atr={cfg["atr_mult"]} ci={cfg["ci_threshold"]} '
              f'lv={cfg["max_levels"]} fs={cfg["float_stop"]:,.0f} qj={cfg["quote_jpy"]}')
        print('=' * 132)
        base_m = D.metrics(cfg, df, atr, ci)
        D.show('baseline', base_m)
        rows.append({'pair': pair, 'tag': 'baseline', 'src': src,
                     **{k: v for k, v in base_m.items() if k != 'wfo_each'}})
        for tag, kw in [('mom2.0 only', {'mom_thr': 2.0}),
                        ('combo m+c+t', COMBO)]:
            m = D.metrics(cfg, df, atr, ci, r24, **kw)
            D.show(tag, m, base_m)
            rows.append({'pair': pair, 'tag': tag.replace(' ', '_'), 'src': src,
                         **{k: v for k, v in m.items() if k != 'wfo_each'}})
        print()

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(f'saved {OUT}')

    # サマリー: 併用のbaseline比
    print('\n=== サマリー (combo vs baseline) ===')
    for pair in out['pair'].unique():
        b = out[(out['pair'] == pair) & (out['tag'] == 'baseline')].iloc[0]
        c = out[(out['pair'] == pair) & (out['tag'] == 'combo_m+c+t')].iloc[0]
        print(f'{pair}: PF {b["full_pf"]:.2f}→{c["full_pf"]:.2f}  '
              f'DD {b["full_dd"]/1e6:.2f}M→{c["full_dd"]/1e6:.2f}M  '
              f'worst {b["full_worst"]/1e3:,.0f}k→{c["full_worst"]/1e3:,.0f}k  '
              f'IS {b["is_pf"]:.2f}→{c["is_pf"]:.2f}  OOS {b["oos_pf"]:.2f}→{c["oos_pf"]:.2f}  '
              f'wfoMin {b["wfo_min"]:.2f}→{c["wfo_min"]:.2f}')


if __name__ == '__main__':
    main()
