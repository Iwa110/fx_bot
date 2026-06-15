"""
grid_corrcross_stepb.py - Stage B合格ペア(CADCHF)の Step B(必要資本/破産確率)+ 既存Go群との相関。

Stage B(grid_corrcross_screen_bt.py)で唯一クリーンに採用バーを通過した CADCHF について:
  (1) Step B: grid_stepb_recompute.py と同一MC(月次ブロックブートストラップ20000回/60ヶ月)で
      req_cap_99・資本効率(net/yr÷req_cap)・P(5yr損)を算定。R-SMA1200 と R-SMA1200+combo を比較。
  (2) 既存Go群(AUDCAD/EURGBP/AUDNZD)との月次PnL相関 + ブレンドの Sharpe/maxDD 改善評価。
      ※低相関は採用理由にしない(教訓5例)が、分散効果が実際に出るかを定量化。

実行: .venv_dukas/bin/python optimizer/grid_corrcross_stepb.py
出力: grid_corrcross_stepb_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY

OUT = Path(__file__).resolve().parent / 'grid_corrcross_stepb_result.csv'
RNG = np.random.default_rng(42)
N_MC = 20000; BLOCK = 3; HORIZON_MONTHS = 60
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def cadchf_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df)
    qj = QUOTE_JPY['CADCHF']
    fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
    return template_cfg(qj, fs)


def monthly_series(pair, cfg, kwfn):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kwfn(df, atr))
    ks = sorted(res['monthly'])
    return res, pd.Series([res['monthly'][k] for k in ks], index=ks)


def block_bootstrap(monthly, horizon, n_mc, block):
    n = len(monthly); n_blocks = int(np.ceil(horizon / block))
    maxdds = np.empty(n_mc); finals = np.empty(n_mc)
    starts = RNG.integers(0, n - block + 1, size=(n_mc, n_blocks))
    for i in range(n_mc):
        seq = np.concatenate([monthly[s:s + block] for s in starts[i]])[:horizon]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        maxdds[i] = (peak[1:] - eq).max(); finals[i] = eq[-1]
    return maxdds, finals


def stepb_row(pair, label, res, mser):
    m = mser.to_numpy(dtype=float)
    n_years = len(m) / 12.0
    net = res['total_pnl']; net_yr = net / n_years
    fs = np.array(res['fs_events'], dtype=float)
    cull = np.array(res['cull_events'], dtype=float)
    b48 = np.array([x for x in res['b48_events'] if x < 0], dtype=float)
    singles = np.concatenate([a for a in [fs, cull, b48] if len(a)]) if (len(fs)+len(cull)+len(b48)) else np.array([0.0])
    worst_single = -singles.min() if len(singles) else 0.0
    worst_gap = worst_single * 1.20   # 未測定ペア=保守的1.20(grid_stepb_recompute慣例)
    maxdds, finals = block_bootstrap(m, HORIZON_MONTHS, N_MC, BLOCK)
    dd99 = np.percentile(maxdds, 99); dd999 = np.percentile(maxdds, 99.9)
    req99 = max(dd99, worst_gap); req999 = max(dd999, worst_gap)
    return {
        'pair': pair, 'config': label, 'pf': res['pf'], 'net_per_yr': round(net_yr, 0),
        'maxDD_hist': res['max_dd'], 'worst_gap': round(worst_gap, 0),
        'mc_dd99': round(dd99, 0), 'req_cap_99': round(req99, 0), 'req_cap_999': round(req999, 0),
        'cap_eff': round(net_yr / req99, 3), 'ruin@req99': round(float((maxdds > req99).mean()), 4),
        'p_loss_5yr': round(float((finals < 0).mean()), 4), 'n_fs': len(fs), 'n_cull': len(cull),
    }, mser


def main():
    cc = cadchf_cfg()
    print(f'CADCHF cfg: fs={cc["float_stop"]:,.0f} qj={cc["quote_jpy"]}\n')

    # CADCHF の2構成
    cad_variants = {
        'R-SMA1200': lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200)},
        'R-SMA1200+combo': lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO},
    }
    # 既存Go群(grid_stepb_recompute と同一構成)
    go_defs = [
        ('AUDCAD', D.AUDCAD, 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('EURGBP', D.EURGBP, 'combo+short_lot0.5',
         lambda df, atr: {'short_lot_mult': 0.5, **COMBO}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)), 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
    ]

    rows = []; mser_map = {}
    for label, kwfn in cad_variants.items():
        res, mser = monthly_series('CADCHF', cc, kwfn)
        row, ms = stepb_row('CADCHF', label, res, mser)
        rows.append(row); mser_map[f'CADCHF/{label}'] = ms

    for pair, cfg, label, kwfn in go_defs:
        res, mser = monthly_series(pair, cfg, kwfn)
        row, ms = stepb_row(pair, label, res, mser)
        rows.append(row); mser_map[pair] = ms

    rdf = pd.DataFrame(rows); rdf.to_csv(OUT, index=False)

    print('=== Step B (lot=1.0, MC20000/60ヶ月/月ブロック3) ===')
    print(f'{"pair/config":24s} {"PF":>5s} {"net/yr":>11s} {"histDD":>10s} {"MC_DD99":>10s} '
          f'{"req99":>11s} {"cap_eff":>8s} {"P(5yr損)":>9s}')
    for r in rows:
        nm = f'{r["pair"]}/{r["config"]}' if r['pair'] == 'CADCHF' else r['pair']
        print(f'{nm:24s} {r["pf"]:5.2f} {r["net_per_yr"]:11,.0f} {r["maxDD_hist"]:10,.0f} '
              f'{r["mc_dd99"]:10,.0f} {r["req_cap_99"]:11,.0f} {r["cap_eff"]:8.2f} {r["p_loss_5yr"]:9.3f}')

    # ── 月次相関(共通月のみ) ──
    print('\n=== 月次PnL相関: CADCHF/R-SMA1200+combo vs 既存Go群 ===')
    base = mser_map['CADCHF/R-SMA1200+combo']
    aligned = pd.DataFrame({k: mser_map[k] for k in ['CADCHF/R-SMA1200+combo', 'AUDCAD', 'EURGBP', 'AUDNZD']}).dropna()
    corr = aligned.corr()
    for g in ['AUDCAD', 'EURGBP', 'AUDNZD']:
        print(f'  CADCHF vs {g}: {corr.loc["CADCHF/R-SMA1200+combo", g]:+.3f}')

    # ── ブレンド効果(等ロット合算 vs 既存3ペアのみ) ──
    def sharpe(s):
        mu, sd = s.mean(), s.std(ddof=1)
        return float(mu / sd * np.sqrt(12)) if sd > 0 else float('nan')
    def maxdd(s):
        eq = s.cumsum().to_numpy(); peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        return float((peak[1:] - eq).max())
    go3 = aligned[['AUDCAD', 'EURGBP', 'AUDNZD']].sum(axis=1)
    go4 = aligned[['AUDCAD', 'EURGBP', 'AUDNZD', 'CADCHF/R-SMA1200+combo']].sum(axis=1)
    print('\n=== ブレンド(等ロット合算, 共通月) ===')
    print(f'  既存3ペア : Sharpe={sharpe(go3):.2f}  maxDD={maxdd(go3):,.0f}  net/yr={go3.mean()*12:,.0f}')
    print(f'  +CADCHF   : Sharpe={sharpe(go4):.2f}  maxDD={maxdd(go4):,.0f}  net/yr={go4.mean()*12:,.0f}')
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
