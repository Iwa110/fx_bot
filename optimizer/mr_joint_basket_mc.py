"""
mr_joint_basket_mc.py - MR_AC(AUDCAD) + MR_CC(CADCHF) バスケット合算モンテカルロDD。

背景:
    vps/mr_monitor.py は AUDCAD(magic20260050/MR_AC)とCADCHF(magic20260051/MR_CC)を
    同時に demo フォワード稼働中。各ペアの昇格/キルスイッチ判定は `mr_forward_review.py` が
    「単独」で行うが、両方が同時にlotを張る実運用ではバスケット合算の資本要件
    (= 2本同時保有時の最悪ドローダウン)を別途確認する必要がある
    (grid確定4本の `grid_joint_stepb.py` と同じ問題設定)。

手法: BTトレード列を時系列マージし、2方式でMC maxDDを算出。
  (a) 既存 mc_maxdd 流用(audcad_stress_test.py / mr_tiered_transfer_bt.py と同一手法):
      2本のトレード净(JPY換算)を1本の列にまとめ、トレード順をシャッフル(10,000回)。
      時間の前後関係を捨てるぶん「2本が同時に同方向へ食い込む」月内相関は反映されない。
  (b) ブロックブートストラップ(grid_joint_stepb.py と同一手法, block=3/60ヶ月/20000回):
      暦月に0埋め・整列したバスケット月次(AUDCAD月次+CADCHF月次を先に合算)を
      月ブロックでリサンプル → 同一ブロックを両ペアで共有=月内の同時発生(共有テール)を保持。
  両者を併記することで「トレード順シャッフル」が同時発生リスクをどれだけ過小評価しているかを見る。

採用構成(vps/mr_monitor.py PAIR_CONFIG, lot_scale=1.0=demoフォワード同一スケール):
  AUDCAD: exit A / z_stop 4.5 / vol_th 0.70   (mr_tiered_transfer_bt.CANON)
  CADCHF: exit B / z_stop 4.0 / vol_th 0.90   (mr_tiered_transfer_best.csv)

出力: mr_joint_basket_mc_result.csv + console
実行: python3 optimizer/mr_joint_basket_mc.py
"""
import os

import numpy as np
import pandas as pd

import dynamic_lot_mr_bt as M
import mr_tiered_transfer_bt as T

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 42
N_ITER_SHUFFLE = 10_000
N_MC_BLOCK = 20_000
BLOCK = 3
HORIZON_MONTHS = 60

# mr_forward_review.PAIRS / mr_tiered_transfer_bt.PIP_VALUE_JPY と同一値(整合必須)
PIP_VALUE_JPY = {'AUDCAD': 1080.0, 'CADCHF': 1850.0}

PAIR_CFG = {
    'AUDCAD': T.make_cfg(exit_mode='A', z_stop=4.5, vol_throttle_th=0.70),
    'CADCHF': T.make_cfg(exit_mode='B', z_stop=4.0, vol_throttle_th=0.90),
}


def load_trades(pair):
    ind, pip, cost = T.load_ind(pair)
    _, trades = M.run_bt_tiered3(ind, pip, cost, PAIR_CFG[pair])
    df = pd.DataFrame(trades)
    df['pair'] = pair
    df['net_jpy'] = df['net_pips'] * PIP_VALUE_JPY[pair]
    return df[['pair', 'entry_t', 'exit_t', 'net_pips', 'net_jpy']]


def mc_maxdd_shuffle(nets, n_iter=N_ITER_SHUFFLE, seed=SEED):
    """既存手法(audcad_stress_test.mc_maxdd / mr_tiered_transfer_bt.mc_maxdd と同一)。"""
    rng = np.random.default_rng(seed)
    nets = np.asarray(nets, float)
    dds = np.empty(n_iter)
    for k in range(n_iter):
        perm = rng.permutation(nets)
        eq = np.cumsum(perm)
        peak = np.maximum.accumulate(eq)
        dds[k] = (peak - eq).max()
    return dds


def monthly_jpy(df):
    s = df.set_index(pd.to_datetime(df['exit_t']))['net_jpy']
    return s.resample('ME').sum()


def block_bootstrap(monthly, n_mc=N_MC_BLOCK, block=BLOCK, horizon=HORIZON_MONTHS, seed=SEED):
    """grid_joint_stepb.bootstrap と同一手法(月ブロックで複数系列を同時リサンプル=同時点相関保持)。
    monthly: dict[pair] -> 暦月0埋め済み np.array (同じ長さ・同じインデックス順)。
    戻り値: {'BASKET': maxdds, pair: maxdds, ...} (pairは単独系列を同一ブロック列で評価=参考比較用)。
    """
    rng = np.random.default_rng(seed)
    pairs = list(monthly.keys())
    n = len(monthly[pairs[0]])
    n_blocks = int(np.ceil(horizon / block))
    starts = rng.integers(0, n - block + 1, size=(n_mc, n_blocks))
    maxdds = {p: np.empty(n_mc) for p in pairs}
    maxdds['BASKET'] = np.empty(n_mc)
    for i in range(n_mc):
        s = starts[i]
        basket_seq = np.zeros(0)
        per_pair_seq = {}
        for p in pairs:
            seq = np.concatenate([monthly[p][st:st + block] for st in s])[:horizon]
            per_pair_seq[p] = seq
        basket_seq = sum(per_pair_seq.values())
        for p in pairs:
            eq = np.cumsum(per_pair_seq[p])
            peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
            maxdds[p][i] = (peak[1:] - eq).max()
        eq = np.cumsum(basket_seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        maxdds['BASKET'][i] = (peak[1:] - eq).max()
    return maxdds


def pctiles(arr):
    p = np.percentile(arr, [50, 95, 99, 99.9])
    return {'mc50': float(p[0]), 'mc95': float(p[1]), 'mc99': float(p[2]), 'mc999': float(p[3])}


def main():
    print('=' * 100)
    print('MR_AC(AUDCAD) + MR_CC(CADCHF) バスケット合算モンテカルロDD')
    print('=' * 100)

    trades = {p: load_trades(p) for p in PAIR_CFG}
    for p, df in trades.items():
        print(f"  {p:8s}: n={len(df):4d}  net={df['net_jpy'].sum():>14,.0f}円  "
              f"期間 {df['exit_t'].min().date()}~{df['exit_t'].max().date()}")

    # --- (1) 時系列マージ(exit_t でソート) + 実現(時系列どおり)バスケットmaxDD ---
    merged = pd.concat(trades.values(), ignore_index=True).sort_values('exit_t').reset_index(drop=True)
    merged.to_csv(os.path.join(HERE, 'mr_joint_basket_mc_merged_trades.csv'), index=False)
    basket_nets = merged['net_jpy'].to_numpy()
    eq = np.cumsum(basket_nets)
    realized_dd = float((np.maximum.accumulate(eq) - eq).max())
    print(f"\n  マージ後トレード数(時系列順) n={len(merged)}  "
          f"実現バスケットmaxDD(時系列どおり) = {realized_dd:,.0f}円")

    # --- (2a) トレード順シャッフルMC(既存 mc_maxdd 流用。時間相関を無視した素のシャッフル) ---
    print(f"\n[a] トレード順シャッフルMC(既存 mc_maxdd 流用, n_iter={N_ITER_SHUFFLE:,})")
    dds_shuffle = mc_maxdd_shuffle(basket_nets)
    pc_shuffle = pctiles(dds_shuffle)
    # 参考: 各ペア単独でも同手法を適用し、単純合算(線形和)と比較
    standalone_shuffle = {}
    for p, df in trades.items():
        dds_p = mc_maxdd_shuffle(df['net_jpy'].to_numpy())
        standalone_shuffle[p] = pctiles(dds_p)
        print(f"    {p:8s} 単独 MC95={standalone_shuffle[p]['mc95']:>12,.0f}円  "
              f"MC99={standalone_shuffle[p]['mc99']:>12,.0f}円")
    lin_sum_95 = sum(v['mc95'] for v in standalone_shuffle.values())
    print(f"    単純合算(単独MC95の線形和)      = {lin_sum_95:>12,.0f}円")
    print(f"    BASKET(シャッフル, 時間相関無視) MC50={pc_shuffle['mc50']:>12,.0f}円  "
          f"MC95={pc_shuffle['mc95']:>12,.0f}円  MC99={pc_shuffle['mc99']:>12,.0f}円  "
          f"MC99.9={pc_shuffle['mc999']:>12,.0f}円")
    print(f"    分散効果(shuffle, 95%ile) = 1 - BASKET/線形和 = "
          f"{(1 - pc_shuffle['mc95'] / lin_sum_95) * 100:+.1f}%")

    # --- (2b) ブロックブートストラップ(暦月0埋め・同時点相関保持) ---
    print(f"\n[b] ブロックブートストラップ(grid_joint_stepb.py と同一手法, "
          f"block={BLOCK}mo/horizon={HORIZON_MONTHS}mo/n_mc={N_MC_BLOCK:,})")
    monthly_raw = {p: monthly_jpy(df) for p, df in trades.items()}
    all_idx = sorted(set().union(*[s.index for s in monthly_raw.values()]))
    cal = pd.period_range(pd.Timestamp(all_idx[0]).to_period('M'),
                          pd.Timestamp(all_idx[-1]).to_period('M'), freq='M')
    monthly_aligned = {}
    for p, s in monthly_raw.items():
        s2 = s.copy()
        s2.index = s2.index.to_period('M')
        monthly_aligned[p] = s2.reindex(cal).fillna(0.0).to_numpy()
    n_months = len(cal)
    active_pct = {p: float((monthly_aligned[p] != 0).sum()) / n_months * 100 for p in monthly_aligned}
    print(f"    暦月数(0埋め): {n_months} ({cal[0]}~{cal[-1]})  活動月率: "
          + ' / '.join(f'{p}={active_pct[p]:.0f}%' for p in monthly_aligned))

    mc_block = block_bootstrap(monthly_aligned)
    block_pc = {k: pctiles(v) for k, v in mc_block.items()}
    for p in monthly_aligned:
        print(f"    {p:8s} 単独(同ブロック)  MC95={block_pc[p]['mc95']:>12,.0f}円  "
              f"MC99={block_pc[p]['mc99']:>12,.0f}円")
    lin_sum_95_block = sum(block_pc[p]['mc95'] for p in monthly_aligned)
    print(f"    単純合算(単独MC95の線形和)      = {lin_sum_95_block:>12,.0f}円")
    print(f"    BASKET(ブロック, 月内同時点相関保持) MC50={block_pc['BASKET']['mc50']:>12,.0f}円  "
          f"MC95={block_pc['BASKET']['mc95']:>12,.0f}円  MC99={block_pc['BASKET']['mc99']:>12,.0f}円  "
          f"MC99.9={block_pc['BASKET']['mc999']:>12,.0f}円")
    print(f"    分散効果(block, 95%ile) = 1 - BASKET/線形和 = "
          f"{(1 - block_pc['BASKET']['mc95'] / lin_sum_95_block) * 100:+.1f}%")

    # --- 月次相関(参考: 既存 mr_tiered_transfer_bt part3 の AUDCAD/CADCHF corr=0.13 と整合確認) ---
    mdf = pd.DataFrame({p: monthly_aligned[p] for p in monthly_aligned}, index=cal)
    corr = mdf['AUDCAD'].corr(mdf['CADCHF'])
    print(f"\n  月次PnL相関(AUDCAD⟷CADCHF, 暦月0埋め後) = {corr:+.3f}  "
          f"(mr_tiered_transfer_bt part3 既報値 ~0.13 と比較)")

    # --- 2通りのMC手法の差(=時間相関を無視することの過小評価度) ---
    print(f"\n  [比較] shuffle(時間無視) vs block(月内同時点相関保持)  MC95:"
          f"  {pc_shuffle['mc95']:,.0f}円  vs  {block_pc['BASKET']['mc95']:,.0f}円  "
          f"(差 {(block_pc['BASKET']['mc95'] / pc_shuffle['mc95'] - 1) * 100:+.1f}%)")

    rows = [
        {'method': 'shuffle', 'series': 'AUDCAD_standalone', **standalone_shuffle['AUDCAD']},
        {'method': 'shuffle', 'series': 'CADCHF_standalone', **standalone_shuffle['CADCHF']},
        {'method': 'shuffle', 'series': 'linear_sum', 'mc50': np.nan, 'mc95': lin_sum_95,
         'mc99': np.nan, 'mc999': np.nan},
        {'method': 'shuffle', 'series': 'BASKET', **pc_shuffle},
        {'method': 'block_bootstrap', 'series': 'AUDCAD_standalone', **block_pc['AUDCAD']},
        {'method': 'block_bootstrap', 'series': 'CADCHF_standalone', **block_pc['CADCHF']},
        {'method': 'block_bootstrap', 'series': 'linear_sum', 'mc50': np.nan,
         'mc95': lin_sum_95_block, 'mc99': np.nan, 'mc999': np.nan},
        {'method': 'block_bootstrap', 'series': 'BASKET', **block_pc['BASKET']},
    ]
    out = pd.DataFrame(rows)
    out['realized_dd_basket'] = realized_dd
    out.to_csv(os.path.join(HERE, 'mr_joint_basket_mc_result.csv'), index=False)
    print(f"\n  [csv] {os.path.join(HERE, 'mr_joint_basket_mc_result.csv')}")
    print(f"  [csv] {os.path.join(HERE, 'mr_joint_basket_mc_merged_trades.csv')}")


if __name__ == '__main__':
    main()
