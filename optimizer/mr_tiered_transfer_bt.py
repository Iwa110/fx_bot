"""
mr_tiered_transfer_bt.py - 確定エッジ AUDCAD(4h) 3段不等分割MR の他相関クロスへの横展開。

背景:
    AUDCAD(4h) の「3段不等分割(Tiered Allocation) 0.2/0.3/0.5 + 高ボラ・ロットスロットル」が
    OOS PF 2.60 / wfoMin 1.81 (no-throttle) の頑健エッジを示した(`dynamic_lot_mr_bt.run_bt_tiered3`)。
    本スクリプトは同ロジックを AUDNZD / CADCHF / EURGBP の4h足へ横展開し、ペア固有の
    「回帰速度(時定数 T_reg)」を定量化したうえで決済ロジック(A 一括 / B 部分利確)を最適化する。

設計の要(本プロジェクトの既存規律を踏襲):
    - エンジンは `dynamic_lot_mr_bt.run_bt_tiered3` を一切改変せず再利用(AUDCAD 4h 数値を機械再現)。
    - Z-score は (close-MA)/SD = 「ボラティリティ正規化済み」の信号。よって閾値(z_tiers/z_stop)は
      ペア間で共通に使える(ATR正規化は信号側では不要)。ATR正規化はロット/レバレッジ側=
      ポートフォリオの等リスク化に適用する(spec「不等分割比率は固定・レバレッジ調整のみ許可」と整合)。
    - IS=2015-2021 / OOS=2022-2026。z_stop と vol_throttle パーセンタイル閾値をスイープし
      OOS頑健性(wfoMin)が最大の地点を選定。
    - 過適合排除: IS↔OOS 符号反転(片方<1.0)構成は即棄却。wfoMin<0.8 は「構造的エッジなし」。

Part1 回帰速度分布   : --reg-speed   -> mr_tiered_transfer_regspeed.csv + .png
Part2 A/B最適化      : --optimize    -> mr_tiered_transfer_optmatrix.csv
Part3 ポートフォリオ : --portfolio   -> mr_tiered_transfer_portfolio.csv
全部                 : (引数なし)

実行: python3 optimizer/mr_tiered_transfer_bt.py
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

import dynamic_lot_mr_bt as M
import liquidity_sweep_bt as LS

HERE = os.path.dirname(os.path.abspath(__file__))
IS_END = pd.Timestamp('2022-01-01', tz='UTC')
TARGETS = ['AUDNZD', 'CADCHF', 'EURGBP']       # 横展開対象
ANCHOR = 'AUDCAD'                              # 基準(閾値較正用)
ALL_PAIRS = [ANCHOR] + TARGETS

# AUDCAD(4h) 確定構成(dynamic_lot_mr_bt の base_cfg + Phase7 既定値)
CANON = dict(
    n=40, z_tiers=[2.0, 2.5, 3.0], lot_tiers=[0.2, 0.3, 0.5],
    z_tp=0.0, z_stop=4.5, max_hold=48, partial_z=1.5,
    vol_throttle_th=0.70, vol_throttle_mult=0.5,
)
# 高ボラ年(マクロ・ボラ局面=確定エッジ群の共有テール)
TAIL_YEARS = [2015, 2017, 2020, 2022]


def make_cfg(**over):
    """run_bt_tiered3 が要求する完全な cfg を CANON ベースで生成。"""
    cfg = {
        'n': CANON['n'], 'z_in': CANON['z_tiers'][0],
        'z_tiers': list(CANON['z_tiers']), 'lot_tiers': list(CANON['lot_tiers']),
        'z_tp': CANON['z_tp'], 'z_stop': CANON['z_stop'], 'max_hold': CANON['max_hold'],
        'partial_z': CANON['partial_z'], 'exit_mode': 'A',
        'vol_throttle_th': CANON['vol_throttle_th'],
        'vol_throttle_mult': CANON['vol_throttle_mult'],
        'htf_filter': 'none', 'adx_max': 25.0, 'slope_max': 1.0,
        'sizing_mode': 'fixed', 'confirm_mode': 'none', 'confirm_window': 6,
        'rsi_os': 30.0, 'rsi_ob': 70.0, 'zk': 1.0, 'max_lot': 3.0,
        'squeeze_lo': 0.20, 'squeeze_mult': 0.5, 'vol_hi': 0.90, 'vol_hi_mult': 0.5,
        'z_in2': 2.5, 'tier_lot': 0.5,
    }
    cfg.update(over)
    return cfg


def load_ind(pair):
    raw = M.load_tf(pair, '4h')
    meta = LS.PAIR_META.get(pair, {'pip': LS.DEFAULT_PIP, 'cost_pips': LS.DEFAULT_COST_PIPS})
    ind = M.add_indicators(raw[['open', 'high', 'low', 'close']].copy(),
                           CANON['n'], 14, 500, 5, 7)
    return ind, meta['pip'], meta['cost_pips']


# ===========================================================================
# Part 1: 回帰速度 T_reg (純・価格統計、戦略の決済とは独立)
# ===========================================================================
def regression_times(ind, z_entry=2.0, cap=400):
    """|Z|>=z_entry の各「新規エクスカーション」について Z=0(MA)へ戻るまでの本数を測る。
       戦略の max_hold/z_stop による打ち切りは入れない=相場固有の回帰時定数。
       cap 本以内に回帰しないものは打ち切り(censored)として cap を記録(中央値は完了分のみ)。"""
    z = ind['z'].to_numpy()
    n = len(z)
    i = M.z_warmup(ind)
    tregs, censored = [], 0
    while i < n - 1:
        zi = z[i]
        if np.isnan(zi) or abs(zi) < z_entry:
            i += 1
            continue
        side = 1 if zi > 0 else -1               # +1=買われすぎ(short設定), -1=売られすぎ
        j = i + 1
        done = False
        while j < n and j - i <= cap:
            zj = z[j]
            if not np.isnan(zj) and ((side > 0 and zj <= 0) or (side < 0 and zj >= 0)):
                tregs.append(j - i)
                done = True
                break
            j += 1
        if not done:
            censored += 1
            tregs.append(min(j - i, cap))
        i = j + 1                                 # 回帰完了点の次から次のエクスカーションを探す
    arr = np.array(tregs, dtype=float)
    completed = arr[:len(arr)]                    # cap到達も含むが中央値は分布で評価
    return arr, censored


def part1_regspeed():
    print('=' * 96)
    print('Part1  回帰速度 T_reg(エントリー|Z|>=2.0 → MA(Z=0)回帰までの本数, 4h足・純価格統計)')
    print('=' * 96)
    print(f"{'pair':8s}|{'n_exc':>7s}{'median':>8s}{'mean':>8s}{'p25':>6s}{'p75':>7s}"
          f"{'p90':>7s}{'cens%':>7s}  | 解釈")
    print('-' * 96)
    dist = {}
    rows = []
    for pair in ALL_PAIRS:
        ind, _, _ = load_ind(pair)
        arr, cens = regression_times(ind)
        dist[pair] = arr
        med = np.median(arr)
        interp = '速い(弾性強)' if med <= 14 else ('中庸' if med <= 22 else '遅い(粘性高)')
        print(f"{pair:8s}|{len(arr):>7d}{med:>8.0f}{arr.mean():>8.1f}"
              f"{np.percentile(arr,25):>6.0f}{np.percentile(arr,75):>7.0f}"
              f"{np.percentile(arr,90):>7.0f}{100*cens/len(arr):>6.1f}%  | {interp}")
        rows.append({'pair': pair, 'n_exc': len(arr), 'treg_median': med,
                     'treg_mean': arr.mean(), 'treg_p25': np.percentile(arr, 25),
                     'treg_p75': np.percentile(arr, 75), 'treg_p90': np.percentile(arr, 90),
                     'censored_pct': 100 * cens / len(arr)})
    anchor_med = np.median(dist[ANCHOR])
    thr = round(anchor_med)
    print('-' * 96)
    print(f"  基準: AUDCAD(4h) T_reg中央値={anchor_med:.0f}本 → 決済ロジック選定の閾値 THR={thr}本 を提案。")
    print(f"  自動選定ルール: T_reg中央値 < {thr}(速い=反転がクリーン) → 構成B(部分利確) 候補,")
    print(f"                  T_reg中央値 >= {thr}(遅い=粘性) → 構成A(一括) 候補。")
    print("  ※ AUDCAD 自身は中央値≈閾値の境界で実証上 A 優位。最終決定は Part2 の A/B 実測 net/DD・wfoMin。")
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'mr_tiered_transfer_regspeed.csv'), index=False)

    # ヒストグラム
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5.5))
        bins = np.arange(0, 81, 4)
        colors = {'AUDCAD': '#888', 'AUDNZD': '#1f77b4', 'CADCHF': '#2ca02c', 'EURGBP': '#d62728'}
        for pair in ALL_PAIRS:
            a = np.clip(dist[pair], 0, 80)
            ax.hist(a, bins=bins, histtype='step', linewidth=2.0,
                    color=colors[pair], density=True,
                    label=f"{pair} (med={np.median(dist[pair]):.0f})")
            ax.axvline(np.median(dist[pair]), color=colors[pair], ls=':', lw=1.2, alpha=0.7)
        ax.axvline(thr, color='k', ls='--', lw=1.4, alpha=0.8, label=f'THR={thr} (A/B split)')
        ax.set_xlabel('T_reg = bars to mean-revert (4h bars, |Z|>=2.0 -> Z=0)')
        ax.set_ylabel('density')
        ax.set_title('Regression-speed (time-constant) distribution - correlation crosses 4h')
        ax.legend()
        fig.tight_layout()
        png = os.path.join(HERE, 'mr_tiered_transfer_regspeed.png')
        fig.savefig(png, dpi=110)
        print(f"  [png] {png}")
    except Exception as e:
        print(f"  [warn] PNG生成skip: {e}")
    return dist, thr


# ===========================================================================
# Part 2: A/B × z_stop × vol_throttle スイープ + 選定
# ===========================================================================
def eval_cfg(ind, pip, cost, cfg):
    is_df = ind[ind.index < IS_END]
    oos_df = ind[ind.index >= IS_END]
    mi = M.run_bt_tiered3(is_df, pip, cost, cfg)[0]
    mo = M.run_bt_tiered3(oos_df, pip, cost, cfg)[0]
    full = M.run_bt_tiered3(ind, pip, cost, cfg)[0]
    folds = []
    for y in M.WFO_YEARS:
        seg = ind[(ind.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                  (ind.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) > 200:
            mf = M.run_bt_tiered3(seg, pip, cost, cfg)[0]
            if mf['n'] >= 8 and not np.isnan(mf['pf']):
                folds.append(mf['pf'])
    wfo_min = min(folds) if folds else float('nan')
    return mi, mo, full, wfo_min


def part2_optimize():
    # スイープ格子。z_stop はめったに発火しないテール stop なので過適合回避のため粗く(0.5刻み)。
    # vol_throttle パーセンタイル閾値は意味のある可変軸として 0.1 刻み + throttle無し(1.01)。
    zstop_grid = [4.0, 4.5, 5.0]
    vt_grid = [1.01, 0.5, 0.6, 0.7, 0.8, 0.9]      # 1.01 = throttle 実質OFF
    print('\n' + '=' * 118)
    print('Part2  横展開 A/B最適化  (3段不等分割 0.2/0.3/0.5 固定, max_hold=48 固定, '
          'z_stop×vol_throttle×{A,B} スイープ)')
    print('=' * 118)
    best_rows, all_rows = [], []
    for pair in TARGETS:
        ind, pip, cost = load_ind(pair)
        print(f"\n[{pair}]  bars={len(ind)}  cost={cost}pips")
        print(f"  {'exit':4s} {'zstop':>5s} {'vt':>4s} |{'IS_PF':>7s}{'OOS_PF':>7s}"
              f"{'full':>6s}{'wfoMin':>7s}{'OOS_net':>8s}{'OOS_DD':>7s}{'avgL':>6s} | flag")
        cand = []
        for em in ['A', 'B']:
            for zs in zstop_grid:
                for vt in vt_grid:
                    cfg = make_cfg(exit_mode=em, z_stop=zs, vol_throttle_th=vt)
                    mi, mo, full, wfo = eval_cfg(ind, pip, cost, cfg)
                    sign_rev = (mi['pf'] < 1.0) or (mo['pf'] < 1.0)
                    selectable = (mi['pf'] >= 1.0 and mo['pf'] > 1.2 and not sign_rev
                                  and not np.isnan(wfo) and wfo >= 0.8)
                    row = {'pair': pair, 'exit_mode': em, 'z_stop': zs, 'vt_th': vt,
                           'is_pf': mi['pf'], 'oos_pf': mo['pf'], 'full_pf': full['pf'],
                           'wfo_min': wfo, 'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
                           'oos_avglot': mo['avg_lot'], 'sign_reversal': sign_rev,
                           'selectable': selectable}
                    all_rows.append(row)
                    cand.append(row)
        # 全構成は冗長なので「各 exit_mode の代表(throttle off / 0.7)」だけ表示
        for r in cand:
            if r['vt_th'] in (1.01, 0.70) and r['z_stop'] == 4.5:
                flag = 'SELECT' if r['selectable'] else (
                    'sign-rev✗' if r['sign_reversal'] else (
                        'wfo<0.8✗' if (not np.isnan(r['wfo_min']) and r['wfo_min'] < 0.8) else 'OOS<1.2'))
                vt_lbl = 'off' if r['vt_th'] > 1 else f"{r['vt_th']:.1f}"
                print(f"  {r['exit_mode']:4s} {r['z_stop']:>5.1f} {vt_lbl:>4s} |"
                      f"{r['is_pf']:>7.2f}{r['oos_pf']:>7.2f}{r['full_pf']:>6.2f}"
                      f"{r['wfo_min']:>7.2f}{r['oos_net']:>8.0f}{r['oos_dd']:>7.0f}"
                      f"{r['oos_avglot']:>6.2f} | {flag}")
        # 選定: selectable の中で wfoMin 最大 → 同点は OOS net/DD
        sel = [r for r in cand if r['selectable']]
        if sel:
            best = max(sel, key=lambda r: (round(r['wfo_min'], 2),
                                           r['oos_net'] / max(r['oos_dd'], 1)))
            verdict = 'ADOPT'
        else:
            # 棄却理由の診断: 最良 wfoMin はどこか
            valid = [r for r in cand if not np.isnan(r['wfo_min'])]
            best = max(valid, key=lambda r: r['wfo_min']) if valid else cand[0]
            verdict = 'REJECT(構造的エッジなし)' if (np.isnan(best['wfo_min']) or best['wfo_min'] < 0.8) \
                else 'REJECT(OOS<1.2 or sign-rev)'
        best = {**best, 'verdict': verdict}
        best_rows.append(best)
        vt_lbl = 'off' if best['vt_th'] > 1 else f"{best['vt_th']:.1f}"
        print(f"  => 最良: exit={best['exit_mode']} z_stop={best['z_stop']} vt={vt_lbl}"
              f"  IS={best['is_pf']:.2f} OOS={best['oos_pf']:.2f} wfoMin={best['wfo_min']:.2f}"
              f"  >>> {verdict}")
    pd.DataFrame(all_rows).to_csv(
        os.path.join(HERE, 'mr_tiered_transfer_optmatrix.csv'), index=False)
    bdf = pd.DataFrame(best_rows)
    bdf.to_csv(os.path.join(HERE, 'mr_tiered_transfer_best.csv'), index=False)
    print('\n  [最適化マトリクス] 各ペアの採用構成(決済A/B と最適パフォーマンス):')
    print('  ' + bdf[['pair', 'exit_mode', 'z_stop', 'vt_th', 'is_pf', 'oos_pf',
                       'full_pf', 'wfo_min', 'verdict']].to_string(index=False).replace('\n', '\n  '))
    return best_rows


# ===========================================================================
# Part 3: ポートフォリオ影響度(MRスリーブ相関 + Grid相関 + 共有テール + Sharpe/DD)
# ===========================================================================
def mr_monthly(pair, cfg):
    """MRスリーブの月次 net(pip*lot) 系列(全期間)を返す。"""
    ind, pip, cost = load_ind(pair)
    _, trades = M.run_bt_tiered3(ind, pip, cost, cfg)
    if not trades:
        return pd.Series(dtype=float)
    df = pd.DataFrame(trades)
    df['m'] = pd.to_datetime(df['exit_t']).dt.strftime('%Y-%m')
    return df.groupby('m')['net_pips'].sum()


def grid_monthly():
    """確定Grid 4本の月次PnL(円)系列を既存エンジンで再現(grid_stepb_recompute / corrcross 構成)。"""
    import grid_dd_reduction_bt as D
    import grid_floatstop_bt as G
    import grid_dirbias_improve_bt as DB
    import grid_corrcross_stepb as CC
    COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}

    def run(pair, cfg, kw):
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kw(df, atr))
        ks = sorted(res['monthly'])
        return pd.Series([res['monthly'][k] for k in ks], index=ks)

    out = {}
    out['AUDCAD'] = run('AUDCAD', D.AUDCAD,
                        lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO})
    out['EURGBP'] = run('EURGBP', D.EURGBP,
                        lambda df, atr: {'short_lot_mult': 0.5, **COMBO})
    audnzd_cfg = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
                  'max_levels': 5, 'float_stop': round(-750_000.0 * 90.0 / 108.0, 0),
                  'quote_jpy': 90.0}
    out['AUDNZD'] = run('AUDNZD', audnzd_cfg,
                        lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO})
    out['CADCHF'] = run('CADCHF', CC.cadchf_cfg(),
                        lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO})
    return out


def annualize(series):
    """月次系列(%表現でない pip/円)から Sharpe(月次→年率) と最大DDを算出。"""
    m = series.to_numpy(dtype=float)
    if len(m) < 6 or m.std() == 0:
        return float('nan'), float('nan')
    sharpe = (m.mean() / m.std()) * np.sqrt(12)
    eq = np.cumsum(m)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
    dd = (peak[1:] - eq).max()
    return sharpe, dd


def part3_portfolio(best_rows):
    print('\n' + '=' * 96)
    print('Part3  ポートフォリオ影響度分析')
    print('=' * 96)
    # --- 採用構成で各MRスリーブ月次を生成(AUDCADは確定A+throttle) ---
    cfgs = {ANCHOR: make_cfg(exit_mode='A', z_stop=4.5, vol_throttle_th=0.70)}
    for r in best_rows:
        cfgs[r['pair']] = make_cfg(exit_mode=r['exit_mode'], z_stop=r['z_stop'],
                                   vol_throttle_th=r['vt_th'])
    mr = {p: mr_monthly(p, c) for p, c in cfgs.items()}
    mr_df = pd.DataFrame(mr).sort_index()

    # --- (a) MRスリーブ間 月次相関 ---
    print('\n  (1) MRスリーブ間 月次PnL相関 (共通月のみ):')
    corr = mr_df.corr()
    print('  ' + corr.round(2).to_string().replace('\n', '\n  '))

    # --- (b) 同一ペア Grid との相関(エッジ重複度) ---
    print('\n  (2) MR(4h) ⟷ 既存Grid(同一ペア) 月次PnL相関 (エッジ重複度):')
    try:
        gm = grid_monthly()
        rows_g = []
        for p in ALL_PAIRS:
            a = mr[p]; b = gm.get(p)
            if b is None:
                continue
            j = pd.concat([a.rename('mr'), b.rename('grid')], axis=1).dropna()
            c = j['mr'].corr(j['grid']) if len(j) > 12 else float('nan')
            rows_g.append({'pair': p, 'corr_mr_grid': c, 'n_months': len(j)})
            print(f"    {p:8s}: corr(MR,Grid)={c:+.2f}  (共通 {len(j)} ヶ月)")
        pd.DataFrame(rows_g).to_csv(
            os.path.join(HERE, 'mr_tiered_transfer_grid_corr.csv'), index=False)
    except Exception as e:
        print(f"    [warn] Grid相関 skip: {e}")

    # --- (c) 共有テール: 高ボラ年の年次net ---
    print('\n  (3) 共有テール(高ボラ年)での年次net挙動  [pip*lot, MRスリーブ]:')
    yr = mr_df.copy()
    yr.index = pd.to_datetime(yr.index + '-01').year
    yearly = yr.groupby(yr.index).sum()
    basket = yearly.sum(axis=1)
    show = yearly.copy()
    show['BASKET'] = basket
    print('  ' + show.round(0).to_string().replace('\n', '\n  '))
    print('\n  高ボラ年(2015/2017/2020/2022)の BASKET net:')
    for y in TAIL_YEARS:
        if y in basket.index:
            comp = ' '.join(f"{p}={yearly.loc[y, p]:+.0f}" for p in yearly.columns if y in yearly.index)
            print(f"    {y}: BASKET={basket.loc[y]:+.0f}   ({comp})")

    # --- (d) Sharpe / MaxDD: 単独 vs バスケット ---
    print('\n  (4) 月次Sharpe(年率) / MaxDD(pip*lot):  単独スリーブ vs 等加重バスケット')
    print(f"    {'sleeve':12s}{'Sharpe':>8s}{'MaxDD':>9s}")
    rows_s = []
    for p in ALL_PAIRS:
        sh, dd = annualize(mr[p].dropna())
        print(f"    {p:12s}{sh:>8.2f}{dd:>9.0f}")
        rows_s.append({'sleeve': p, 'sharpe': sh, 'maxdd': dd})
    bser = mr_df.sum(axis=1).dropna()
    sh_b, dd_b = annualize(bser)
    print(f"    {'BASKET(=4)':12s}{sh_b:>8.2f}{dd_b:>9.0f}")
    rows_s.append({'sleeve': 'BASKET', 'sharpe': sh_b, 'maxdd': dd_b})
    # AUDCAD単独基準の改善
    sh_ac, dd_ac = annualize(mr[ANCHOR].dropna())
    print(f"\n    バスケット vs AUDCAD単独: Sharpe {sh_ac:.2f}→{sh_b:.2f} "
          f"({(sh_b/sh_ac-1)*100:+.0f}%) / MaxDD {dd_ac:.0f}→{dd_b:.0f} "
          f"(注: MaxDDは合算exposureのため絶対値増。リスク調整=Sharpe で評価)")
    pd.DataFrame(rows_s).to_csv(
        os.path.join(HERE, 'mr_tiered_transfer_portfolio.csv'), index=False)
    mr_df.to_csv(os.path.join(HERE, 'mr_tiered_transfer_mr_monthly.csv'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reg-speed', action='store_true')
    ap.add_argument('--optimize', action='store_true')
    ap.add_argument('--portfolio', action='store_true')
    args = ap.parse_args()
    run_all = not (args.reg_speed or args.optimize or args.portfolio)

    best_rows = None
    if args.reg_speed or run_all:
        part1_regspeed()
    if args.optimize or run_all:
        best_rows = part2_optimize()
    if args.portfolio or run_all:
        if best_rows is None:
            best_rows = part2_optimize()
        part3_portfolio(best_rows)


if __name__ == '__main__':
    main()
