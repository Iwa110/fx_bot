"""
grid_joint_stepb.py - ジョイント(ポートフォリオ)Step B: 確定Grid 4本を同時保有したときの
バスケット必要資本を、月次PnLの相関を保持したジョイント・ブロックブートストラップで算定。

動機: 既存 Step B(grid_stepb_recompute.py / grid_corrcross_stepb.py)は各ペア「単独」の
req_cap_99。実運用は4本同時保有=月次PnLが低〜中相関のため、バスケットのmaxDD分布は
単純合算より小さい(分散効果)。これを定量化すれば新エッジ無しで資本効率↑。

確定Grid 4本(2026-06-15 時点のベスト構成):
  AUDCAD : R-SMA1200 + combo         (Tier1, Go筆頭)
  CADCHF : R-SMA1200                  (Tier2, 新Go。テール優先は+combo)
  AUDNZD : R-SMA1200 + combo          (Tier2)
  EURGBP : combo + short_lot0.5       (Tier2)
※ carry系(USDJPY/NZDJPY)はスケール禁止のため本バスケットに含めない。

手法(grid_stepb_recompute.py と同一・一貫):
  - 月次PnL系列を DB.run_bt(collect=True) で各ペア取得(lot=1.0, 円換算 quote_jpy)。
  - 共通月(intersection)で整列 → 行列 M (n_months × 4)。
  - per-pair req_cap_99: M[:,p] を独立にブロックブートストラップ(block=3, 60ヶ月, 20000回)。
  - basket req_cap_99: バスケット月次 = M @ w を「先に合算してから」同手法でブートストラップ。
    → 同一行ブロックを全ペアで共有=月内の同時点相関を保持(=ジョイント)。
  - 分散効果 = 1 - basket_req99 / Σ(w_p · standalone_req99_p)。

配分案を比較し、月利30万円(=360万/yr)の必要資本をバスケット分散込みで再算定する。

実行: .venv_dukas/bin/python optimizer/grid_joint_stepb.py
出力: grid_joint_stepb_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY

OUT = Path(__file__).resolve().parent / 'grid_joint_stepb_result.csv'
SEED = 42
N_MC = 20000
BLOCK = 3
HORIZON_MONTHS = 60
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
TARGET_NET_YR = 3_600_000.0   # 月利30万円


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def cadchf_cfg():
    """grid_corrcross_stepb.cadchf_cfg と同一(fs は AUDCAD基準でprice距離一致)。"""
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df)
    qj = QUOTE_JPY['CADCHF']
    fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
    return template_cfg(qj, fs)


def _eurgbp_improved():
    """候補2: fs x1.3 (回復可能ラダーを切らない=DD/単発tail両減) の EURGBP cfg。"""
    c = dict(D.EURGBP); c['float_stop'] = round(D.EURGBP['float_stop'] * 1.3, 0)
    return c


def build_defs(improved=False):
    """(pair, cfg, label, kwfn) の4本。grid_corrcross_stepb / grid_stepb_recompute と整合。
    improved=True で候補2(grid_capheavy_ddcompress.py)のDD圧縮構成を EURGBP/CADCHF に適用。
      EURGBP: combo + short_lot0.5 + fs x1.3 + taper0.6
      CADCHF: R-SMA1200 + cull0.6
    """
    if not improved:
        return [
            ('AUDCAD', D.AUDCAD, 'R-SMA1200+combo',
             lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
            ('CADCHF', cadchf_cfg(), 'R-SMA1200',
             lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200)}),
            ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)), 'R-SMA1200+combo',
             lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
            ('EURGBP', D.EURGBP, 'combo+short_lot0.5',
             lambda df, atr: {'short_lot_mult': 0.5, **COMBO}),
        ]
    return [
        ('AUDCAD', D.AUDCAD, 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('CADCHF', cadchf_cfg(), 'R-SMA1200+cull0.6',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), 'cull_frac': 0.6}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)), 'R-SMA1200+combo',
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('EURGBP', _eurgbp_improved(), 'combo+slot0.5+fsx1.3+taper0.6',
         lambda df, atr: {'short_lot_mult': 0.5, 'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.6}),
    ]


def monthly_series(pair, cfg, kwfn):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kwfn(df, atr))
    ks = sorted(res['monthly'])
    return res, pd.Series([res['monthly'][k] for k in ks], index=ks)


def bootstrap(monthly, rng):
    """単一系列のブロックブートストラップ。maxdds, finals を返す。"""
    n = len(monthly); n_blocks = int(np.ceil(HORIZON_MONTHS / BLOCK))
    maxdds = np.empty(N_MC); finals = np.empty(N_MC)
    starts = rng.integers(0, n - BLOCK + 1, size=(N_MC, n_blocks))
    for i in range(N_MC):
        seq = np.concatenate([monthly[s:s + BLOCK] for s in starts[i]])[:HORIZON_MONTHS]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        maxdds[i] = (peak[1:] - eq).max(); finals[i] = eq[-1]
    return maxdds, finals


def req_cap(maxdds):
    return float(np.percentile(maxdds, 99))


def run_basket(defs, tag, out_suffix):
    series = {}; resmap = {}
    print('\n' + '#' * 90)
    print(f'### {tag}')
    print('#' * 90)
    for pair, cfg, label, kwfn in defs:
        res, s = monthly_series(pair, cfg, kwfn)
        series[pair] = s; resmap[pair] = (label, res)

    pairs = [d[0] for d in defs]
    # 暦月基盤(carry_crash_hedge_bt と同じ): grid の monthly は close発生月しか記録しない
    # (CIゲートで休眠する月が多い)。アイドル月の実現PnL=0 を補完しないとMCが活動月だけに
    # 圧縮されmaxDDを過大評価する。全ペア共通の暦月レンジに reindex し 0 埋め(=ジョイント整列)。
    all_idx = pd.PeriodIndex(sorted(set().union(*[s.index for s in series.values()])), freq='M')
    cal = pd.period_range(all_idx.min(), all_idx.max(), freq='M').strftime('%Y-%m')
    M = pd.DataFrame({p: series[p].reindex(cal).fillna(0.0) for p in pairs})
    months = M.index.tolist()
    print(f'暦月数(0埋め): {len(months)} ({months[0]}〜{months[-1]}) ※活動月 '
          + '/'.join(f'{p}:{(M[p]!=0).sum()}' for p in pairs) + '\n')

    # --- per-pair(整列窓・lot1.0) ---
    standalone = {}
    print('--- P1. per-pair(共通窓, lot=1.0) ---')
    print(f'{"pair":7s} {"config":18s} {"net/yr":>11s} {"std/mo":>9s} {"req99":>11s} {"capEff":>7s} {"P(5yr損)":>9s}')
    n_years = len(months) / 12.0
    netyr = {}
    for p in pairs:
        col = M[p].to_numpy(dtype=float)
        rng = np.random.default_rng(SEED)
        mdd, fin = bootstrap(col, rng)
        r99 = req_cap(mdd); ny = col.sum() / n_years; netyr[p] = ny
        standalone[p] = {'req99': r99, 'netyr': ny, 'std': col.std(),
                         'p5': float((fin < 0).mean()), 'maxdds': mdd}
        eff = ny / r99 if r99 else float('nan')
        print(f'{p:7s} {resmap[p][0]:18s} {ny:11,.0f} {col.std():9,.0f} {r99:11,.0f} '
              f'{eff:7.2f} {standalone[p]["p5"]:9.3f}')

    # --- 月次相関 ---
    print('\n--- P2. 月次PnL相関(共通窓) ---')
    corr = M.corr()
    print('        ' + ''.join(f'{p:>9s}' for p in pairs))
    for p in pairs:
        print(f'{p:7s} ' + ''.join(f'{corr.loc[p, q]:+9.2f}' for q in pairs))

    # --- 配分案: バスケットreq_cap(ジョイントMC) ---
    Mnp = M.to_numpy(dtype=float)  # (n_months, 4)

    def basket_eval(w):
        """w: per-pair lot 重みベクトル。バスケットを先に合算→ジョイントMC。"""
        w = np.asarray(w, dtype=float)
        basket = Mnp @ w
        rng = np.random.default_rng(SEED)  # 各配分で同一乱数(公平比較)
        mdd, fin = bootstrap(basket, rng)
        r99 = req_cap(mdd)
        ny = basket.sum() / n_years
        naive = sum(w[i] * standalone[pairs[i]]['req99'] for i in range(len(pairs)))
        return {'w': w, 'netyr': ny, 'req99': r99, 'naive_req99': naive,
                'div': 1 - r99 / naive if naive else 0.0,
                'eff': ny / r99 if r99 else float('nan'),
                'p5': float((fin < 0).mean()), 'maxdds_med': float(np.percentile(mdd, 50))}

    # 重み案(相対lot比)
    inv_req = np.array([1.0 / standalone[p]['req99'] for p in pairs])
    inv_std = np.array([1.0 / standalone[p]['std'] for p in pairs])
    allocs = {
        'AUDCAD単独':       [1, 0, 0, 0],
        '等ロット':          [1, 1, 1, 1],
        '等req_cap配分':     (inv_req / inv_req[0]).round(3).tolist(),   # AUDCAD=1基準
        'リスクバジェット(inv-std)': (inv_std / inv_std[0]).round(3).tolist(),
    }

    print('\n--- P3. 配分案の比較(バスケット, ジョイントMC) ---')
    print(f'  weights順: {pairs}')
    print(f'{"alloc":22s} {"weights":28s} {"net/yr":>11s} {"req99(joint)":>13s} {"naive_sum":>12s} {"分散効果":>8s} {"capEff":>7s} {"P5":>6s}')
    alloc_rows = []
    for name, w in allocs.items():
        e = basket_eval(w)
        wstr = '[' + ','.join(f'{x:g}' for x in e['w']) + ']'
        print(f'{name:22s} {wstr:28s} {e["netyr"]:11,.0f} {e["req99"]:13,.0f} '
              f'{e["naive_req99"]:12,.0f} {e["div"]*100:7.1f}% {e["eff"]:7.2f} {e["p5"]:6.3f}')
        alloc_rows.append({'alloc': name, 'weights': wstr, 'net_yr': round(e['netyr'], 0),
                           'req99_joint': round(e['req99'], 0), 'naive_sum': round(e['naive_req99'], 0),
                           'diversification': round(e['div'], 4), 'cap_eff': round(e['eff'], 3),
                           'p_loss_5yr': round(e['p5'], 4)})

    # --- P4. 月利30万円シナリオ(全配分でlotスケール) ---
    print('\n--- P4. 月利30万円(360万/yr)シナリオ: バスケット分散込み必要資本 ---')
    print('  (各配分の相対比を維持して net/yr=360万 にスケール → req_cap_99)')
    print(f'{"alloc":22s} {"scale":>7s} {"必要資本(joint)":>15s} {"naive_sum":>12s} {"節約額":>11s} {"AUDCADのみ比":>11s}')
    audcad_only = basket_eval([1, 0, 0, 0])
    audcad_only_cap_30 = audcad_only['req99'] * (TARGET_NET_YR / audcad_only['netyr'])
    p4_rows = []
    for name, w in allocs.items():
        e = basket_eval(w)
        if e['netyr'] <= 0:
            continue
        scale = TARGET_NET_YR / e['netyr']
        cap_joint = e['req99'] * scale
        cap_naive = e['naive_req99'] * scale
        print(f'{name:22s} {scale:7.2f} {cap_joint:15,.0f} {cap_naive:12,.0f} '
              f'{cap_naive - cap_joint:11,.0f} {cap_joint / audcad_only_cap_30:11.2f}x')
        p4_rows.append({'alloc_30man': name, 'scale': round(scale, 3),
                        'req_cap_30man_joint': round(cap_joint, 0),
                        'req_cap_30man_naive': round(cap_naive, 0)})

    # --- P5. 相関崩れストレス: 同時最悪月 / concurrent worst-gap ---
    print('\n--- P5. 相関崩れストレス(同時DD) ---')
    eq_basket = (Mnp @ np.array([1, 1, 1, 1])).cumsum()
    worst_idx = int(np.argmin(M.sum(axis=1).to_numpy()))
    print(f'  等ロット最悪単月: {months[worst_idx]} = {M.sum(axis=1).iloc[worst_idx]:,.0f}円')
    # 各ペアの最悪3ヶ月が重なるか
    for p in pairs:
        worst3 = M[p].nsmallest(3)
        print(f'  {p:7s} 最悪3月: ' + ', '.join(f'{m}({v:,.0f})' for m, v in worst3.items()))

    out = OUT.with_name(f'grid_joint_stepb{out_suffix}.csv')
    pd.DataFrame(alloc_rows).to_csv(out, index=False)
    pd.DataFrame(p4_rows).to_csv(OUT.with_name(f'grid_joint_stepb_30man{out_suffix}.csv'), index=False)
    print(f'\nsaved {out}')


def main():
    print('=== ジョイント Step B: 確定Grid 4本 / Dukascopy 11年 / lot=1.0基準 / 暦月基盤 ===')
    print(f'  MC {N_MC}回 / horizon {HORIZON_MONTHS}ヶ月 / 月ブロック {BLOCK} / seed {SEED}')
    run_basket(build_defs(improved=False),
               'ベースライン構成 (2026-06-15 確定 4本)', '_result')
    run_basket(build_defs(improved=True),
               '候補2 DD圧縮後 (EURGBP fsx1.3+taper0.6 / CADCHF +cull0.6)', '_improved')


if __name__ == '__main__':
    main()
