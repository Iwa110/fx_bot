"""
grid_toolkit_allpairs_bt.py - 開発済み改善ツール一式を残りペアへ総当たり適用。

これまでの採用候補(エントリーmom24ゲート / worst-leg cull / lot taper / 方向バイアス
[hard long-only, レジーム条件付きshort SMA1200, soft short_lot] / combo)を、まだ方向次元で
未検証のペアに当てて「救済できるNo-Goペアがないか」を確認する。

対象(未検証): CHFJPY / AUDNZD / EURCHF / USDJPY / EURUSD
  ※AUDCAD/EURGBP/NZDJPY/GBPJPY は grid_novel_bt.py / grid_dirbias_improve_bt.py で検証済。

設定は確立済みテンプレ(grid_dd_reduction_transfer.py と同一・再チューニング無し):
  CHFJPY = V7_CONFIG / AUDNZD,EURCHF = AUDCADテンプレ(fs=quote_jpy比) /
  USDJPY,EURUSD = テンプレ(fs=ATR中央値×qj 比でスケール)。

採用バー: IS-selectable(IS PF≥base) ∧ OOS>1.2 ∧ wfoMin>1.0(できれば>1.2) ∧ DD許容。
エンジンは grid_dirbias_improve_bt.run_bt(全機能搭載・静的一致assert済)を流用。

実行: .venv_dukas/bin/python optimizer/grid_toolkit_allpairs_bt.py
出力: grid_toolkit_allpairs_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB

OUT = Path(__file__).resolve().parent / 'grid_toolkit_allpairs_result.csv'
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def side_pf(pair, cfg, df, atr, ci):
    """long/short 単独PF(baseline設定, full期間)を診断表示。"""
    lo = DB.run_bt(cfg, df, atr, ci, short_ml=0)
    so = DB.run_bt(cfg, df, atr, ci, long_ml=0)
    print(f'  [診断] long-only net={lo["total_pnl"]:>12,.0f} PF={lo["pf"]:.2f}  | '
          f'short-only net={so["total_pnl"]:>12,.0f} PF={so["pf"]:.2f}')


def main():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0

    plan = [
        ('CHFJPY', {**GI.V7_CONFIG['CHFJPY']}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0))),
        ('EURCHF', template_cfg(170.0, round(-750_000.0 * 170.0 / 108.0, 0))),
        ('USDJPY', None),
        ('EURUSD', None),
    ]
    explore_qj = {'USDJPY': 1.0, 'EURUSD': 155.0}

    rows = []
    for pair, cfg in plan:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        if cfg is None:
            qj = explore_qj[pair]
            fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
            cfg = template_cfg(qj, fs)
        r24 = D.ret24_series(df, atr)
        reg1200 = DB.sma_regime(df, 1200)

        print('=' * 140); print(f'{pair}  fs={cfg["float_stop"]:,.0f} ci={cfg["ci_threshold"]} '
                                 f'atr={cfg["atr_mult"]} lv={cfg["max_levels"]}'); print('=' * 140)
        base = DB.metrics(cfg, df, atr, ci); DB.show('baseline', base)
        rows.append((pair, 'baseline', base))
        side_pf(pair, cfg, df, atr, ci)
        ref = G.run_backtest(pair, cfg, df, atr, ci)
        assert abs(ref['total_pnl'] - base['full_net']) < 1.0, f'{pair} mismatch'

        variants = [
            ('long-only', {'short_ml': 0}),
            ('R-SMA1200', {'short_block_up': reg1200, 'regimes': {'short_block_up': reg1200}}),
            ('soft short_lot0.5', {'short_lot_mult': 0.5}),
            ('combo (both)', {'ret24': r24, **COMBO}),
            ('long-only+combo', {'ret24': r24, 'short_ml': 0, **COMBO}),
            ('R-SMA1200+combo', {'ret24': r24, 'short_block_up': reg1200,
                                 'regimes': {'short_block_up': reg1200}, **COMBO}),
        ]
        for tag, kw in variants:
            m = DB.metrics(cfg, df, atr, ci, **kw); DB.show(tag, m, base)
            rows.append((pair, tag, m))
        print()

    out = [{'pair': p, 'tag': t, **{k: v for k, v in m.items() if k != 'wfo_each'}} for p, t, m in rows]
    df_out = pd.DataFrame(out)
    df_out.to_csv(OUT, index=False)
    print(f'saved {OUT}')

    # 採用バー判定サマリー
    print('\n=== 採用バー判定 (IS≥base ∧ OOS>1.2 ∧ wfoMin>1.0) ===')
    for pair in df_out['pair'].unique():
        sp = df_out[df_out['pair'] == pair]
        base_is = sp[sp.tag == 'baseline']['is_pf'].iloc[0]
        hit = []
        for _, r in sp.iterrows():
            if r['tag'] == 'baseline': continue
            if r['is_pf'] >= base_is and r['oos_pf'] > 1.2 and r['wfo_min'] > 1.0:
                hit.append(f"{r['tag']}(PF{r['full_pf']:.2f}/IS{r['is_pf']:.2f}/OOS{r['oos_pf']:.2f}/wfoMin{r['wfo_min']:.2f})")
        print(f'  {pair}: ' + ('  '.join(hit) if hit else '該当なし(救済不可)'))


if __name__ == '__main__':
    main()
