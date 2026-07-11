"""
crypto_ratio_mr_bt.py - 候補A: ETH/BTC 比率の平均回帰(確定エッジ AUDCAD(4h) 3段不等分割MR の銘柄差し替え)。

背景 (memory: project_crypto_extension_plan_20260711):
    FX bot の土日無稼働を埋める crypto 拡張の執行本線。構造的理由=ETH/BTC は両者とも
    「crypto β」を共有 → 比率がレジーム水準へ平均回帰する、という仮説。AUDCAD(4h) で
    OOS PF 2.60 を出した `dynamic_lot_mr_bt.run_bt_tiered3`(3段不等分割 0.2/0.3/0.5 +
    高ボラ・ロットスロットル + MA一括/部分利確 + タイムストップ)を一切改変せず ETH/BTC へ適用。

    弱点(既知): crypto 比率は FX 相関クロスより回帰が弱くトレンドが長い(ETH/BTC は数年
    トレンド実績)。→ IS/OOS/WFO で厳格に殺す。通らなければ即 Close。

検証規律(既存 FX プロジェクト踏襲):
    - エンジンは run_bt_tiered3 を再利用(Z-score 正規化信号ゆえ z 閾値はそのまま流用可)。
    - IS = 2017-2021(凍結) / OOS = 2022-2026。約定=次足始値、指標=確定足(lookahead 排除)。
    - フルコスト: 国内現物スポットの往復手数料+スプレッドを「価格に対する割合」で控除。
      比率は 0.016~0.117 と広く変動するため、固定絶対コストは regime を歪める。
      → 各評価区間(IS/OOS/年次fold)の中央価格 × cost_frac を cost_pips に換算し、
         区間内で一貫した %コストを課す(cross-regime の歪みを排除)。
    - ★税ハードル: 国内 crypto は雑所得・累進最大55%・損失繰越なし(FX の 20.315%一律 +
      3年繰越の優位が消える)。→ 構造テスト(PF)は税引前で純粋にエッジを見る一方、
      最終ゲートで「年次netに55%課税・負の年は救済なし・繰越なし」を適用した税引後経済性を
      別途評価し、"土日が動くだけ" では採用しない。

採用バー(税引前・構造): OOS PF > 1.2 ∧ 全 OOS fold PF > 1.0 ∧ IS-selectable(IS PF>=1.0,
    符号反転なし, wfoMin>=0.8)。これを通った上で税引後ゲート(after-tax 累積 net > 0 ∧
    after-tax 年次 Sharpe > 0)をクリアして初めて「国内現物で土日稼働に値する」と判定。

実行 (専用venv):
    .venv_crypto/bin/python optimizer/crypto_ratio_mr_bt.py
    .venv_crypto/bin/python optimizer/crypto_ratio_mr_bt.py --cost-frac 0.008
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

import dynamic_lot_mr_bt as M

HERE = os.path.dirname(os.path.abspath(__file__))
IS_END = pd.Timestamp('2022-01-01', tz='UTC')       # IS=2017-2021 / OOS=2022-
PAIR = 'ETHBTC'
TF = '4h'
PIP = 1e-5                                            # 表示単位(PFは pip 不変。純粋に可読性用)
JP_CRYPTO_TAX = 0.55                                  # 国内 crypto 雑所得 最高税率(住民税込)

# AUDCAD(4h) 確定構成(dynamic_lot_mr_bt の base_cfg + Phase7 既定値)。z 閾値は Z 正規化ゆえ流用可。
CANON = dict(
    n=40, z_tiers=[2.0, 2.5, 3.0], lot_tiers=[0.2, 0.3, 0.5],
    z_tp=0.0, z_stop=4.5, max_hold=48, partial_z=1.5,
    vol_throttle_th=0.70, vol_throttle_mult=0.5,
)
# 全暦年(IS+OOS)。年次PF診断で「回帰が壊れるトレンド年」を可視化する。
ALL_YEARS = list(range(2018, 2027))
OOS_FOLDS = M.WFO_YEARS                               # [2022..2026] = 採用バーの fold


def make_cfg(**over):
    """run_bt_tiered3 が要求する完全な cfg を CANON ベースで生成(transfer 版と同一枠)。"""
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


def load_ind():
    raw = M.load_tf(PAIR, TF)
    if raw is None:
        raise SystemExit(f'[fatal] data/{PAIR}_{TF}.csv が見つかりません。'
                         f'先に fetch_crypto_ohlc.py を実行してください。')
    ind = M.add_indicators(raw[['open', 'high', 'low', 'close']].copy(),
                           CANON['n'], 14, 500, 5, 7)
    return ind


def seg_cost_pips(seg, cost_frac):
    """区間 seg の中央価格 × cost_frac(往復) を pip 換算した cost_pips。
       区間内で一貫した %コストになり cross-regime の歪みを避ける。"""
    med = float(seg['close'].median())
    return (cost_frac * med) / PIP


def run_seg(seg, cost_frac, cfg):
    return M.run_bt_tiered3(seg, PIP, seg_cost_pips(seg, cost_frac), cfg)[0]


# ---------------------------------------------------------------------------
# 回帰速度 T_reg (純・価格統計。ETH/BTC の「回帰が遅い/トレンドが長い」弱点を定量化)
# ---------------------------------------------------------------------------
def regression_times(ind, z_entry=2.0, cap=400):
    """|Z|>=z_entry のエクスカーションが Z=0(MA) へ戻るまでの本数。戦略の打ち切りは入れない。
       cap 本以内に回帰しないものは censored として cap を記録。"""
    z = ind['z'].to_numpy()
    n = len(z)
    i = M.z_warmup(ind)
    tregs, censored = [], 0
    while i < n - 1:
        zi = z[i]
        if np.isnan(zi) or abs(zi) < z_entry:
            i += 1
            continue
        side = 1 if zi > 0 else -1
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
        i = j + 1
    return np.array(tregs, dtype=float), censored


def part_regspeed(ind):
    print('=' * 92)
    print('回帰速度 T_reg  (|Z|>=2.0 → MA(Z=0)回帰までの 4h本数, 純価格統計)')
    print('=' * 92)
    arr, cens = regression_times(ind)
    med = np.median(arr)
    print(f"  ETHBTC(4h): n_exc={len(arr)}  median={med:.0f}本  mean={arr.mean():.1f}  "
          f"p75={np.percentile(arr,75):.0f}  p90={np.percentile(arr,90):.0f}  "
          f"censored(>400本)={100*cens/len(arr):.1f}%")
    print(f"  参考: AUDCAD(4h) T_reg中央値≈21本 / 確定Grid相関クロス 19-23本。")
    interp = '速い' if med <= 22 else ('中庸' if med <= 40 else '遅い(粘性高=トレンド長・MR弱の兆候)')
    print(f"  → ETH/BTC は中央値 {med:.0f}本 = {interp}。censored率が高いほど「戻らないトレンド」が多い。")
    return {'treg_median': med, 'treg_mean': float(arr.mean()),
            'treg_p90': float(np.percentile(arr, 90)), 'censored_pct': 100 * cens / len(arr)}


# ---------------------------------------------------------------------------
# 評価: IS / OOS / full / OOS年次fold + 全暦年PF
# ---------------------------------------------------------------------------
def eval_cfg(ind, cost_frac, cfg):
    is_df = ind[ind.index < IS_END]
    oos_df = ind[ind.index >= IS_END]
    mi = run_seg(is_df, cost_frac, cfg)
    mo = run_seg(oos_df, cost_frac, cfg)
    full = run_seg(ind, cost_frac, cfg)
    folds = {}
    for y in OOS_FOLDS:
        seg = ind[(ind.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                  (ind.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) > 200:
            mf = run_seg(seg, cost_frac, cfg)
            if mf['n'] >= 8 and not np.isnan(mf['pf']):
                folds[y] = mf['pf']
    wfo_min = min(folds.values()) if folds else float('nan')
    return mi, mo, full, wfo_min, folds


def yearly_pf(ind, cost_frac, cfg):
    """全暦年の PF/net(税引前)。回帰が壊れるトレンド年の可視化。"""
    rows = []
    for y in ALL_YEARS:
        seg = ind[(ind.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                  (ind.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) < 200:
            continue
        m = run_seg(seg, cost_frac, cfg)
        rows.append({'year': y, 'n': m['n'], 'pf': m['pf'],
                     'net_pips': m['net_pips'], 'wr': m['wr']})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 税引後ゲート: 年次netに55%課税(負の年は救済なし・繰越なし)
# ---------------------------------------------------------------------------
def after_tax(yearly_df, oos_only=True):
    """年次 net(pip*lot) 系列に国内 crypto 税制を適用。
       損失繰越なし・損益通算なし → 各年 独立に max(net,0)*0.55 を控除。
       (この変換は正のスケールに不変。符号/相対規模のみに依存 = pip/lot に非依存。)"""
    df = yearly_df.copy()
    if oos_only:
        df = df[df['year'] >= 2022]
    if df.empty:
        return None
    pre = df['net_pips'].to_numpy(dtype=float)
    tax = np.where(pre > 0, pre * JP_CRYPTO_TAX, 0.0)
    post = pre - tax
    pre_cum, post_cum = pre.sum(), post.sum()
    drag = 1.0 - post_cum / pre_cum if pre_cum > 0 else float('nan')
    # 年次 Sharpe(税引後, 年率そのもの=年次系列の平均/標準偏差)
    sh_pre = pre.mean() / pre.std() if pre.std() > 0 else float('nan')
    sh_post = post.mean() / post.std() if post.std() > 0 else float('nan')
    return {'years': list(df['year']), 'pre_annual': pre, 'post_annual': post,
            'pre_cum': pre_cum, 'post_cum': post_cum, 'tax_drag': drag,
            'sharpe_pre': sh_pre, 'sharpe_post': sh_post,
            'neg_years': int((pre <= 0).sum()), 'n_years': len(pre)}


# ---------------------------------------------------------------------------
# 月次ブロック・ブートストラップ MC (maxDD 分布)
# ---------------------------------------------------------------------------
def monthly_mc(ind, cost_frac, cfg, n_iter=5000, horizon=24, block=3, seed=42):
    """OOS の月次 net 系列をブロック・ブートストラップし maxDD 分布を出す(暦月0埋め)。"""
    _, trades = M.run_bt_tiered3(ind[ind.index >= IS_END], PIP,
                                 seg_cost_pips(ind[ind.index >= IS_END], cost_frac), cfg)
    if not trades:
        return None
    td = pd.DataFrame(trades)
    td['m'] = pd.to_datetime(td['exit_t']).dt.to_period('M')
    monthly = td.groupby('m')['net_pips'].sum()
    full_idx = pd.period_range(monthly.index.min(), monthly.index.max(), freq='M')
    monthly = monthly.reindex(full_idx, fill_value=0.0).to_numpy()
    if len(monthly) < block + 1:
        return None
    rng = np.random.default_rng(seed)
    dds = []
    n_blocks = int(np.ceil(horizon / block))
    starts_max = len(monthly) - block
    for _ in range(n_iter):
        starts = rng.integers(0, starts_max + 1, size=n_blocks)
        path = np.concatenate([monthly[s:s + block] for s in starts])[:horizon]
        eq = np.cumsum(path)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        dds.append((peak[1:] - eq).max())
    dds = np.array(dds)
    return {'dd_median': float(np.median(dds)), 'dd_p95': float(np.percentile(dds, 95)),
            'dd_p99': float(np.percentile(dds, 99)), 'monthly_mean': float(monthly.mean())}


# ---------------------------------------------------------------------------
# メイン: スイープ + 選定 + 税引後ゲート
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cost-frac', type=float, default=0.008,
                    help='往復コスト(比率価格に対する割合)。国内現物: 2脚×(taker+spread)≈0.8%%')
    ap.add_argument('--cost-sweep', action='store_true', help='コスト感応度も出力')
    args = ap.parse_args()

    ind = load_ind()
    print('#' * 92)
    print(f'# 候補A  ETH/BTC(4h) 3段不等分割MR  bars={len(ind)}  '
          f'{ind.index[0].date()}~{ind.index[-1].date()}')
    print(f'# IS=2017-2021(凍結) / OOS=2022-2026 / cost_frac={args.cost_frac*100:.2f}%(往復) '
          f'/ 税率={JP_CRYPTO_TAX*100:.0f}%(繰越なし)')
    print('#' * 92)

    reg = part_regspeed(ind)

    # --- スイープ格子(transfer 版と同一軸: exit_mode × z_stop × vol_throttle) ---
    zstop_grid = [4.0, 4.5, 5.0]
    vt_grid = [1.01, 0.5, 0.6, 0.7, 0.8, 0.9]        # 1.01 = throttle 実質OFF
    print('\n' + '=' * 108)
    print('スイープ  (3段不等分割 0.2/0.3/0.5 固定, max_hold=48 固定, '
          'exit_mode×z_stop×vol_throttle)')
    print('=' * 108)
    print(f"  {'exit':4s} {'zstop':>5s} {'vt':>4s} |{'IS_PF':>7s}{'OOS_PF':>7s}"
          f"{'full':>6s}{'wfoMin':>7s}{'OOS_net':>9s}{'OOS_DD':>8s}{'OOS_n':>6s} | flag")
    all_rows, cand = [], []
    for em in ['A', 'B']:
        for zs in zstop_grid:
            for vt in vt_grid:
                cfg = make_cfg(exit_mode=em, z_stop=zs, vol_throttle_th=vt)
                mi, mo, full, wfo, folds = eval_cfg(ind, args.cost_frac, cfg)
                sign_rev = (mi['pf'] < 1.0) or (mo['pf'] < 1.0)
                all_fold_ok = bool(folds) and all(v > 1.0 for v in folds.values())
                selectable = (mi['pf'] >= 1.0 and mo['pf'] > 1.2 and not sign_rev
                              and not np.isnan(wfo) and wfo >= 0.8 and all_fold_ok)
                row = {'exit_mode': em, 'z_stop': zs, 'vt_th': vt,
                       'is_pf': mi['pf'], 'oos_pf': mo['pf'], 'full_pf': full['pf'],
                       'wfo_min': wfo, 'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
                       'oos_n': mo['n'], 'all_fold_gt1': all_fold_ok,
                       'sign_reversal': sign_rev, 'selectable': selectable}
                all_rows.append(row)
                cand.append(row)
                if vt in (1.01, 0.70) and zs == 4.5:      # 代表構成のみ表示
                    flag = 'SELECT' if selectable else (
                        'sign-rev' if sign_rev else (
                            'fold<1' if not all_fold_ok else (
                                'wfo<0.8' if (not np.isnan(wfo) and wfo < 0.8) else 'OOS<1.2')))
                    vt_lbl = 'off' if vt > 1 else f'{vt:.1f}'
                    print(f"  {em:4s} {zs:>5.1f} {vt_lbl:>4s} |{mi['pf']:>7.2f}{mo['pf']:>7.2f}"
                          f"{full['pf']:>6.2f}{wfo:>7.2f}{mo['net_pips']:>9.0f}"
                          f"{mo['max_dd_pips']:>8.0f}{mo['n']:>6d} | {flag}")
    pd.DataFrame(all_rows).to_csv(os.path.join(HERE, 'crypto_ratio_mr_bt_result.csv'), index=False)

    # --- 選定 ---
    sel = [r for r in cand if r['selectable']]
    if sel:
        best = max(sel, key=lambda r: (round(r['wfo_min'], 2),
                                       r['oos_net'] / max(r['oos_dd'], 1)))
        verdict = 'ADOPT(構造)'
    else:
        valid = [r for r in cand if not np.isnan(r['wfo_min'])]
        best = max(valid, key=lambda r: r['wfo_min']) if valid else cand[0]
        verdict = 'REJECT(構造的エッジなし)'
    vt_lbl = 'off' if best['vt_th'] > 1 else f"{best['vt_th']:.1f}"
    print(f"\n  最良構成: exit={best['exit_mode']} z_stop={best['z_stop']} vt={vt_lbl}  "
          f"IS={best['is_pf']:.2f} OOS={best['oos_pf']:.2f} full={best['full_pf']:.2f} "
          f"wfoMin={best['wfo_min']:.2f}  >>> {verdict}")

    best_cfg = make_cfg(exit_mode=best['exit_mode'], z_stop=best['z_stop'],
                        vol_throttle_th=best['vt_th'])

    # --- 全暦年PF診断(回帰破断年の可視化) ---
    print('\n' + '=' * 92)
    print('全暦年 PF/net 診断(税引前)  ※ crypto はトレンド年に MR が壊れる想定')
    print('=' * 92)
    ydf = yearly_pf(ind, args.cost_frac, best_cfg)
    ydf.to_csv(os.path.join(HERE, 'crypto_ratio_mr_bt_yearly.csv'), index=False)
    print(f"  {'year':>6s}{'n':>5s}{'PF':>7s}{'net':>10s}{'WR':>7s}   (IS<2022 / OOS>=2022)")
    for _, r in ydf.iterrows():
        tag = 'IS ' if r['year'] < 2022 else 'OOS'
        print(f"  {int(r['year']):>6d}{int(r['n']):>5d}{r['pf']:>7.2f}"
              f"{r['net_pips']:>10.0f}{r['wr']*100:>6.0f}%   {tag}")

    # --- 税引後ゲート(OOS 年次に55%課税・繰越なし) ---
    print('\n' + '=' * 92)
    print('★税引後ゲート  国内crypto 雑所得55%・損失繰越なし・損益通算なし (OOS 2022-2026)')
    print('=' * 92)
    at = after_tax(ydf, oos_only=True)
    passed_struct = (verdict.startswith('ADOPT'))
    if at is None:
        print('  [warn] OOS 年次データ不足で税引後評価不能')
        tax_pass = False
    else:
        print(f"  年次net(税引前): " + ' '.join(f"{y}:{v:+.0f}" for y, v in
                                              zip(at['years'], at['pre_annual'])))
        print(f"  年次net(税引後): " + ' '.join(f"{y}:{v:+.0f}" for y, v in
                                              zip(at['years'], at['post_annual'])))
        print(f"  累積 net: 税引前={at['pre_cum']:+.0f}  税引後={at['post_cum']:+.0f}  "
              f"(税ドラッグ={at['tax_drag']*100:.0f}%  損失年={at['neg_years']}/{at['n_years']})")
        print(f"  年次Sharpe: 税引前={at['sharpe_pre']:.2f}  税引後={at['sharpe_post']:.2f}")
        tax_pass = (at['post_cum'] > 0 and not np.isnan(at['sharpe_post'])
                    and at['sharpe_post'] > 0)
        print(f"  税引後ゲート: {'PASS' if tax_pass else 'FAIL'} "
              f"(after-tax 累積>0 ∧ after-tax Sharpe>0)")

    # --- MC (maxDD 分布) ---
    mc = monthly_mc(ind, args.cost_frac, best_cfg)
    if mc:
        print('\n  月次ブロックMC(OOS, 24ヶ月地平): '
              f"maxDD median={mc['dd_median']:.0f} p95={mc['dd_p95']:.0f} "
              f"p99={mc['dd_p99']:.0f} (pip*lot)")

    # --- コスト感応度(任意) ---
    if args.cost_sweep:
        print('\n' + '=' * 60)
        print('コスト感応度 (best構成の OOS PF)')
        print('=' * 60)
        for cf in [0.004, 0.006, 0.008, 0.012, 0.016]:
            mi, mo, full, wfo, folds = eval_cfg(ind, cf, best_cfg)
            print(f"  cost={cf*100:>4.1f}%  IS={mi['pf']:.2f} OOS={mo['pf']:.2f} "
                  f"full={full['pf']:.2f} wfoMin={wfo:.2f}")

    # --- 総括 ---
    print('\n' + '#' * 92)
    final = 'GO(構造+税引後 両クリア)' if (passed_struct and tax_pass) else (
        'NO-GO(構造は通るが税引後で消滅)' if (passed_struct and not tax_pass) else
        'NO-GO(構造的エッジなし)')
    print(f'# 候補A 判定: {final}')
    print(f'#   回帰速度中央値={reg["treg_median"]:.0f}本 / 構造={verdict} / '
          f'税引後={"PASS" if tax_pass else "FAIL"}')
    print('#' * 92)


if __name__ == '__main__':
    main()
