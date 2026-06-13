"""
grid_stepb_recompute.py - Step B 再算定: forward-test候補(改善構成)の必要資本・破産確率・投入計画。

旧Step B(grid_sizing_ruin.py)は v7 静的構成(cull/taper無し)で算定したが、今週の改善で
DD/worst単発が半減した。各候補の確定構成で月次PnL+テールイベントを取り直し、ブロック・
ブートストラップMCで必要資本を再算定する。手法は grid_sizing_ruin.py を踏襲(同一MC設定)。

候補(2026-06-13 確定構成):
  AUDCAD : R-SMA1200 + combo(mom2.0/cull0.5/taper0.7)         [両側, Go筆頭]
  EURGBP : combo + short_lot0.5 (+tp0.8は決済側で別途)          [相関クロス]
  AUDNZD : R-SMA1200 + combo                                    [相関クロス, 限界的]
  USDJPY : long-only + combo                                    [carry, スケール禁止]
  NZDJPY : long-only + combo                                    [carry, スケール禁止]

円換算 quote_jpy: AUDCAD108/EURGBP190/AUDNZD90/USDJPY1/NZDJPY1。USDJPY/NZDJPYはJPY建てそのまま。
注: net円はFXレート想定でスケール(PF/比率は不感)。lot線形につき必要資本もlotでスケール。

実行: .venv_dukas/bin/python optimizer/grid_stepb_recompute.py
出力: grid_stepb_recompute_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB

OUT = Path(__file__).resolve().parent / 'grid_stepb_recompute_result.csv'
RNG = np.random.default_rng(42)
N_MC = 20000
BLOCK = 3
HORIZON_MONTHS = 60
# A4 ギャップ貫通バッファ(grid_sizing_ruin.py の実測 max_ratio)。未測定ペアは保守的に1.20。
GAP_BUFFER = {'AUDCAD': 1.10, 'NZDJPY': 1.20, 'EURGBP': 1.20, 'AUDNZD': 1.20, 'USDJPY': 1.20}

COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def build_candidates():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0
    usdjpy_fs = lambda atr: round(-750_000.0 * float(atr.median()) / ref_atr_jpy, 0)
    nzdjpy_cfg = {**GI.V7_CONFIG['NZDJPY']}

    return [
        ('AUDCAD', D.AUDCAD, 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('EURGBP', D.EURGBP, 'combo+short_lot0.5',
         lambda df, atr: {'short_lot_mult': 0.5, **COMBO}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)), 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('USDJPY', None, 'long-only+combo',
         lambda df, atr: {'short_ml': 0, **COMBO}),
        ('NZDJPY', nzdjpy_cfg, 'long-only+combo',
         lambda df, atr: {'short_ml': 0, **COMBO}),
    ], usdjpy_fs


def block_bootstrap_maxdd(monthly, horizon, n_mc, block):
    n = len(monthly)
    n_blocks = int(np.ceil(horizon / block))
    maxdds = np.empty(n_mc); finals = np.empty(n_mc)
    starts = RNG.integers(0, n - block + 1, size=(n_mc, n_blocks))
    for i in range(n_mc):
        seq = np.concatenate([monthly[s:s + block] for s in starts[i]])[:horizon]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        dd = peak[1:] - eq
        maxdds[i] = dd.max(); finals[i] = eq[-1]
    return maxdds, finals


def main():
    cands, usdjpy_fs = build_candidates()
    rows = []
    for pair, cfg, label, kwfn in cands:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        if cfg is None:  # USDJPY
            cfg = template_cfg(1.0, usdjpy_fs(atr))
        r24 = D.ret24_series(df, atr)
        kw = kwfn(df, atr)
        res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kw)

        mser = res['monthly']
        ks = sorted(mser)
        m = np.array([mser[k] for k in ks], dtype=float)
        n_years = len(m) / 12.0
        net = res['total_pnl']; net_yr = net / n_years
        mu = m.mean()

        # 単発テール: FS + cull + 負のB48。ギャップ貫通バッファ。
        fs = np.array(res['fs_events'], dtype=float)
        cull = np.array(res['cull_events'], dtype=float)
        b48 = np.array([x for x in res['b48_events'] if x < 0], dtype=float)
        singles = np.concatenate([a for a in [fs, cull, b48] if len(a)]) if (len(fs)+len(cull)+len(b48)) else np.array([0.0])
        worst_single = -singles.min() if len(singles) else 0.0
        worst_gap = worst_single * GAP_BUFFER.get(pair, 1.2)

        maxdds, finals = block_bootstrap_maxdd(m, HORIZON_MONTHS, N_MC, BLOCK)
        dd_med = np.percentile(maxdds, 50)
        dd99 = np.percentile(maxdds, 99)
        dd999 = np.percentile(maxdds, 99.9)

        # 必要資本(lot=1.0) = max(MC DD99, 単発+gap) を吸収。破産<1%基準。
        req99 = max(dd99, worst_gap)
        req999 = max(dd999, worst_gap)
        ruin_at_req99 = float((maxdds > req99).mean())
        p_loss_5yr = float((finals < 0).mean())

        rows.append({
            'pair': pair, 'config': label, 'pf': res['pf'], 'net11yr': round(net, 0),
            'net_per_yr': round(net_yr, 0), 'mean_month': round(mu, 0),
            'maxDD_hist': res['max_dd'], 'worst_single': round(worst_single, 0),
            'worst_gap': round(worst_gap, 0), 'mc_dd_med': round(dd_med, 0),
            'mc_dd99': round(dd99, 0), 'mc_dd999': round(dd999, 0),
            'req_cap_99': round(req99, 0), 'req_cap_999': round(req999, 0),
            'ruin@req99': round(ruin_at_req99, 4), 'p_loss_5yr': round(p_loss_5yr, 4),
            'n_fs': len(fs), 'n_cull': len(cull),
        })

    rdf = pd.DataFrame(rows); rdf.to_csv(OUT, index=False)

    print('=== Step B 再算定: forward-test候補(改善構成) / Dukascopy 11年 / lot=1.0基準 ===')
    print(f'  MC {N_MC}回 / horizon {HORIZON_MONTHS}ヶ月 / 月ブロック {BLOCK}\n')
    print('--- B1. 収益性 & テール (lot=1.0, 円) ---')
    print(f'{"pair":7s} {"config":18s} {"PF":>5s} {"net/yr":>11s} {"mean/mo":>9s} '
          f'{"histDD":>10s} {"worst+gap":>10s} {"MC_DD99":>10s} {"MC_DD99.9":>10s}')
    for r in rows:
        print(f'{r["pair"]:7s} {r["config"]:18s} {r["pf"]:5.2f} {r["net_per_yr"]:11,.0f} '
              f'{r["mean_month"]:9,.0f} {r["maxDD_hist"]:10,.0f} {r["worst_gap"]:10,.0f} '
              f'{r["mc_dd99"]:10,.0f} {r["mc_dd999"]:10,.0f}')

    print('\n--- B2. 必要資本 & 破産確率 (lot=1.0, 破産<1%基準=req_cap_99) ---')
    print(f'{"pair":7s} {"req_cap_99":>12s} {"req_cap_999":>12s} {"ruin@req99":>11s} {"P(5yr損)":>9s}')
    for r in rows:
        print(f'{r["pair"]:7s} {r["req_cap_99"]:12,.0f} {r["req_cap_999"]:12,.0f} '
              f'{r["ruin@req99"]:11.3f} {r["p_loss_5yr"]:9.3f}')

    # 旧Step B(v7) との比較(AUDCAD/NZDJPY)
    try:
        old = pd.read_csv(Path(__file__).resolve().parent / 'grid_sizing_ruin_result.csv').set_index('pair')
        print('\n--- B3. 旧v7 vs 新構成 の必要資本(req_cap_99, lot=1.0) ---')
        for r in rows:
            if r['pair'] in old.index:
                o = old.loc[r['pair'], 'req_cap_99(lot1)']
                print(f'  {r["pair"]:7s} 旧v7={o:>12,.0f} → 新={r["req_cap_99"]:>12,.0f} '
                      f'({r["req_cap_99"]/o*100:.0f}%)')
    except Exception as e:
        print(f'(旧比較スキップ: {e})')

    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
