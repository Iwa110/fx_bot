"""
grid_corrcross_screen_bt.py - Stage B: v8ツールキットを Stage A合格クロスへ適用。

Stage A(grid_corrcross_screen.py)で Go アンカーの構造圏内と判定された候補のみに、
確立済みテンプレ(atr1.5/ci65/lv5, float_stop=AUDCAD基準でprice距離正規化)を当て、
baseline + ツールキット変種を回す(再チューニング無し):
  baseline / long-only / R-SMA1200(regime short) / soft short_lot0.5 /
  combo(mom2.0+cull0.5+taper0.7) / long-only+combo / R-SMA1200+combo

エンジンは grid_dirbias_improve_bt.run_bt(全機能搭載・静的一致assert済)を流用。
各ペアで G.run_backtest との full net 一致を assert。

採用バー(事前登録, grid_toolkit_allpairs と同一):
  IS-selectable(IS PF≥baseline) ∧ OOS>1.2 ∧ wfoMin>1.0(できれば>1.2) ∧ DD許容。

実行(Stage A後): .venv_dukas/bin/python optimizer/grid_corrcross_screen_bt.py
  --pairs を省略すると grid_corrcross_screen_rank.csv の stageB=True を自動採用。
出力: grid_corrcross_screen_bt_result.csv + console
"""
import argparse
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY, GROUP

HERE = Path(__file__).resolve().parent
OUT = HERE / 'grid_corrcross_screen_bt_result.csv'
RANK = HERE / 'grid_corrcross_screen_rank.csv'
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def side_pf(cfg, df, atr, ci):
    lo = DB.run_bt(cfg, df, atr, ci, short_ml=0)
    so = DB.run_bt(cfg, df, atr, ci, long_ml=0)
    print(f'  [診断] long-only net={lo["total_pnl"]:>13,.0f} PF={lo["pf"]:.2f}  | '
          f'short-only net={so["total_pnl"]:>13,.0f} PF={so["pf"]:.2f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', nargs='+', default=None)
    args = ap.parse_args()

    if args.pairs:
        pairs = args.pairs
    elif RANK.exists():
        rk = pd.read_csv(RANK)
        pairs = rk[rk['stageB']]['pair'].tolist()
        print(f'Stage A から自動採用: {pairs}')
    else:
        raise SystemExit('rank CSV無し。先に grid_corrcross_screen.py を実行するか --pairs 指定。')
    if not pairs:
        print('Stage B 対象ペア無し(Go圏内候補ゼロ)。Close。'); return

    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * QUOTE_JPY['AUDCAD']

    rows = []
    for pair in pairs:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        qj = QUOTE_JPY[pair]
        fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
        cfg = template_cfg(qj, fs)
        r24 = D.ret24_series(df, atr); reg1200 = DB.sma_regime(df, 1200)

        print('=' * 140); print(f'{pair} [{GROUP.get(pair, "?")}]  fs={fs:,.0f} qj={qj} '
                                 f'atr={cfg["atr_mult"]} ci={cfg["ci_threshold"]} lv={cfg["max_levels"]}'); print('=' * 140)
        base = DB.metrics(cfg, df, atr, ci); DB.show('baseline', base)
        rows.append((pair, 'baseline', base))
        side_pf(cfg, df, atr, ci)
        ref = G.run_backtest(pair, cfg, df, atr, ci)
        assert abs(ref['total_pnl'] - base['full_net']) < 1.0, f'{pair} engine mismatch'

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
    pd.DataFrame(out).to_csv(OUT, index=False)
    print(f'saved {OUT}')

    print('\n=== 採用バー判定 (IS≥base ∧ OOS>1.2 ∧ wfoMin>1.0) ===')
    df_out = pd.DataFrame(out)
    for pair in df_out['pair'].unique():
        sp = df_out[df_out['pair'] == pair]
        base_is = sp[sp.tag == 'baseline']['is_pf'].iloc[0]
        hit = []
        for _, r in sp.iterrows():
            if r['tag'] == 'baseline': continue
            if r['is_pf'] >= base_is and r['oos_pf'] > 1.2 and r['wfo_min'] > 1.0:
                hit.append(f"{r['tag']}(PF{r['full_pf']:.2f}/IS{r['is_pf']:.2f}/"
                           f"OOS{r['oos_pf']:.2f}/wfoMin{r['wfo_min']:.2f}/DD{r['full_dd']:,.0f})")
        print(f'  {pair}: ' + ('  '.join(hit) if hit else '該当なし(救済不可)'))


if __name__ == '__main__':
    main()
