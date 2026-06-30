"""
mr_distribution_analysis.py - 平均回帰トレードの「保有時間」「乖離幅(Z)」分布解析 (Phase 4)。

目的 (2026-06-30):
    相関クロス平均回帰の死因を、MFE/MAE に続いて2軸で深掘りする。
      1) Time in Trade (保有本数) vs 勝率/PF
         -> 時間経過で MA が価格に寄り期待値が下がる(時間的劣化)か。最適タイムストップを特定。
      2) Entry Z-score (乖離幅) vs 勝率/PF
         -> 乖離が大きすぎる領域(例 Z>3.5)は平均回帰でなくレジームシフト(トレンド)で
            期待値がマイナスへ転じるか。ハードストップを置くべき Z 水準を特定。

入力 (いずれか):
    1) --trades <csv>: dynamic_lot_mr_bt.py --dump-trades の出力 (side, net_pips,
       hold_bars, z_in_actual 列を含む)。
    2) 引数なし: dynamic_lot_mr_bt を baseline(confirm=none, 固定ロット)で実走し解析。

出力:
    - mr_distribution_<tag>.png: (上)保有本数ビン別 勝率/PF (下)|Z|ビン別 勝率/PF。
    - mr_distribution_<tag>.csv: ビン別集計。
    - 標準出力: 「N本以上で勝率急減」「Z>X でエッジ消失」などのファクト。

検証規律: hold_bars/z_in_actual はバックテストが lookahead 無しで記録した実測値を使用。

使用法:
    python3 optimizer/mr_distribution_analysis.py
    python3 optimizer/mr_distribution_analysis.py --trades dynamic_lot_mr_trades_AUDCAD_1h_none.csv
    python3 optimizer/mr_distribution_analysis.py --pairs AUDCAD --tf 1h --split oos
"""

import argparse
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import liquidity_sweep_bt as LS
import dynamic_lot_mr_bt as M

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------------------
# ビン集計
# ----------------------------------------------------------------------------
def bin_stats(df, value_col, edges, labels):
    """value_col を edges でビン分けし、ビン毎の n/勝率/PF/平均net/累積netを返す。"""
    cats = pd.cut(df[value_col], bins=edges, labels=labels, right=False,
                  include_lowest=True)
    rows = []
    for lab in labels:
        g = df[cats == lab]
        if len(g) == 0:
            rows.append({'bin': lab, 'n': 0, 'wr': np.nan, 'pf': np.nan,
                         'avg_net': np.nan, 'sum_net': 0.0})
            continue
        nets = g['net_pips'].to_numpy()
        gw = nets[nets > 0].sum()
        gl = -nets[nets <= 0].sum()
        pf = gw / gl if gl > 0 else (np.inf if gw > 0 else np.nan)
        rows.append({'bin': lab, 'n': len(g), 'wr': float((nets > 0).mean()),
                     'pf': pf, 'avg_net': float(nets.mean()), 'sum_net': float(nets.sum())})
    return pd.DataFrame(rows)


def hold_edges():
    edges = [0, 4, 7, 13, 25, 49, 97, np.inf]
    labels = ['1-3', '4-6', '7-12', '13-24', '25-48', '49-96', '97+']
    return edges, labels


def z_edges():
    edges = [2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, np.inf]
    labels = ['2.00-2.25', '2.25-2.50', '2.50-2.75', '2.75-3.00',
              '3.00-3.50', '3.50-4.00', '4.00+']
    return edges, labels


# ----------------------------------------------------------------------------
# 可視化
# ----------------------------------------------------------------------------
def plot_bins(hold_df, z_df, tag):
    fig, axes = plt.subplots(2, 1, figsize=(12, 10))
    fig.suptitle(f'Mean-Reversion: Hold-time & Entry-Z distribution  [{tag}]', fontsize=12)

    def draw(ax, bdf, xlabel, title):
        x = np.arange(len(bdf))
        wr = bdf['wr'].to_numpy() * 100
        ax.bar(x, wr, color='tab:blue', alpha=0.45, label='win rate (%)')
        for xi, (w, nn) in enumerate(zip(wr, bdf['n'])):
            if not np.isnan(w):
                ax.text(xi, w + 1, f'n={int(nn)}', ha='center', va='bottom', fontsize=7)
        ax.axhline(50, color='tab:blue', ls=':', lw=1)
        ax.set_ylabel('win rate (%)', color='tab:blue')
        ax.set_ylim(0, 100)
        ax.set_xticks(x)
        ax.set_xticklabels(bdf['bin'], rotation=0, fontsize=8)
        ax.set_xlabel(xlabel)
        ax.set_title(title)
        ax2 = ax.twinx()
        pf = bdf['pf'].replace([np.inf], np.nan).to_numpy()
        ax2.plot(x, pf, color='tab:red', marker='o', lw=2, label='PF')
        ax2.axhline(1.0, color='tab:red', ls='--', lw=1)
        ax2.set_ylabel('PF', color='tab:red')
        pf_fin = pf[np.isfinite(pf)]
        ax2.set_ylim(0, max(2.0, (pf_fin.max() if len(pf_fin) else 2.0) * 1.1))

    he, _ = hold_edges()
    draw(axes[0], hold_df, 'hold bars', 'Time in Trade vs WR / PF')
    draw(axes[1], z_df, '|entry Z-score|', 'Entry deviation (|Z|) vs WR / PF')
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(HERE, f'mr_distribution_{tag}.png')
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


# ----------------------------------------------------------------------------
# ファクト抽出
# ----------------------------------------------------------------------------
def report_facts(tdf, hold_df, z_df, tag):
    print('=' * 92)
    print(f'[{tag}]  trades={len(tdf)}  '
          f"WR={(tdf['net_pips']>0).mean()*100:.1f}%  net={tdf['net_pips'].sum():.0f}pip")
    print('=' * 92)
    print('-- 保有本数(Time in Trade)別 --')
    print(hold_df.to_string(index=False, float_format=lambda x: f'{x:.2f}'))
    print('-- 乖離幅 |entry Z| 別 --')
    print(z_df.to_string(index=False, float_format=lambda x: f'{x:.2f}'))

    print('\n-- ファクト抽出 --')
    # 保有時間の劣化点: PF が初めて 1.0 を割るビン / 勝率が初めて 50% を割るビン
    deg_pf = hold_df[(hold_df['n'] >= 20) & (hold_df['pf'] < 1.0)]
    deg_wr = hold_df[(hold_df['n'] >= 20) & (hold_df['wr'] < 0.5)]
    if len(deg_pf):
        b = deg_pf.iloc[0]
        print(f'  時間劣化: 保有 [{b["bin"]}] 本 で PF<1.0 (PF={b["pf"]:.2f}, '
              f'WR={b["wr"]*100:.0f}%, n={int(b["n"])})。')
        print(f'    ⚠️ 注意: このビン別PFは「そのビンで決済された」生存条件付きの値であり、'
              f'「{b["bin"]}本でタイムストップを切れば改善する」を意味しない。')
        print(f'    深い含み損トレードは時間を与えると平均回帰で部分回復するため、'
              f'早期に切ると回復益を殺す(Grid float_stop と同型)。'
              f'実際のタイムストップ最適値は max_hold スイープで検証すること。')
    else:
        print('  時間劣化: 全ビンで PF>=1.0 (十分標本) → 明確な時間的劣化なし。'
              'タイムストップ短縮の根拠は弱い。')
    if len(deg_wr):
        b = deg_wr.iloc[0]
        print(f'  勝率急減: 保有 [{b["bin"]}] 本 で WR<50% (WR={b["wr"]*100:.0f}%)。')
    # 乖離幅のエッジ消失点: PF が初めて 1.0 を割る Z ビン
    z_lost = z_df[(z_df['n'] >= 15) & (z_df['pf'] < 1.0)]
    if len(z_lost):
        b = z_lost.iloc[0]
        print(f'  レジームシフト: |Z| [{b["bin"]}] で PF<1.0 (PF={b["pf"]:.2f}, '
              f'WR={b["wr"]*100:.0f}%, n={int(b["n"])}) → この乖離以上は平均回帰でなく'
              f'トレンド化の疑い。ハードストップ/エントリー上限の候補。')
    else:
        print('  レジームシフト: 全 |Z| ビンで PF>=1.0 (十分標本) → 乖離拡大でのエッジ消失は'
              '明確に観測されず (z_stop=4 が妥当か、より高Zは標本薄)。')
    # 乖離が大きいほど良いか(平均回帰圧力仮説の検証)
    big = z_df[(z_df['n'] >= 15)]
    if len(big) >= 2:
        corr = np.corrcoef(np.arange(len(big)), big['pf'].replace([np.inf], np.nan).ffill())[0, 1]
        trend = '正(乖離大ほど良い)' if corr > 0.2 else ('負(乖離大ほど悪い)' if corr < -0.2 else 'ほぼ無相関')
        print(f'  乖離幅↔PF の傾向: {trend} (rank corr={corr:+.2f}) '
              f'→ 「乖離が大きいほど期待値が高い」仮説の妥当性。')


# ----------------------------------------------------------------------------
# トレード取得
# ----------------------------------------------------------------------------
def trades_from_bt(pairs, tf, args, split):
    rows = []
    for pair in pairs:
        raw = M.load_tf(pair, tf)        # 4h 等は 1h から resample (lookahead 無し)
        if raw is None or len(raw) < 1000:
            print(f'[warn] {pair}: データ不足スキップ')
            continue
        meta = LS.PAIR_META.get(pair, {'pip': LS.DEFAULT_PIP, 'cost_pips': LS.DEFAULT_COST_PIPS})
        df = raw[['open', 'high', 'low', 'close']].copy()
        ind = M.add_indicators(df, args.n, args.atr_n, args.atr_lookback,
                               args.ema_span, args.rsi_n)
        if split == 'is':
            ind = ind[ind.index < M.IS_END]
        elif split == 'oos':
            ind = ind[ind.index >= M.IS_END]
        cfg = M.base_cfg(args)
        cfg['confirm_mode'] = args.confirm
        cfg['sizing_mode'] = 'fixed'
        _, trades = M.run_bt(ind, meta['pip'], meta['cost_pips'], cfg)
        for t in trades:
            t['pair'] = pair
        rows.extend(trades)
        print(f'  {pair}: {len(trades)} trades (split={split}, confirm={args.confirm})')
    return pd.DataFrame(rows) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trades', help='既存トレードログ CSV (hold_bars, z_in_actual, net_pips 必須)')
    ap.add_argument('--pairs', nargs='+', default=['AUDCAD'])
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--split', default='all', choices=['all', 'is', 'oos'],
                    help='--trades 無し時の解析期間')
    ap.add_argument('--tag', default=None)
    # BT パラメータ (--trades 無し時)
    ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--z-in', dest='z_in', type=float, default=2.0)
    ap.add_argument('--z-tp', dest='z_tp', type=float, default=0.0)
    ap.add_argument('--z-stop', dest='z_stop', type=float, default=4.0)
    ap.add_argument('--max-hold', dest='max_hold', type=int, default=48)
    ap.add_argument('--zk', type=float, default=1.0)
    ap.add_argument('--max-lot', dest='max_lot', type=float, default=3.0)
    ap.add_argument('--squeeze-lo', dest='squeeze_lo', type=float, default=0.20)
    ap.add_argument('--squeeze-mult', dest='squeeze_mult', type=float, default=0.5)
    ap.add_argument('--vol-hi', dest='vol_hi', type=float, default=0.90)
    ap.add_argument('--vol-hi-mult', dest='vol_hi_mult', type=float, default=0.5)
    ap.add_argument('--atr-n', dest='atr_n', type=int, default=14)
    ap.add_argument('--atr-lookback', dest='atr_lookback', type=int, default=500)
    ap.add_argument('--ema-span', dest='ema_span', type=int, default=5)
    ap.add_argument('--rsi-n', dest='rsi_n', type=int, default=7)
    ap.add_argument('--rsi-os', dest='rsi_os', type=float, default=30.0)
    ap.add_argument('--rsi-ob', dest='rsi_ob', type=float, default=70.0)
    ap.add_argument('--confirm-window', dest='confirm_window', type=int, default=6)
    ap.add_argument('--confirm', default='none', choices=['none', 'ema', 'candle', 'rsi'])
    # base_cfg が要求する HTF/層化パラメータ (本ツールでは未使用だが cfg 構築に必要)
    ap.add_argument('--adx-max', dest='adx_max', type=float, default=25.0)
    ap.add_argument('--slope-max', dest='slope_max', type=float, default=1.0)
    ap.add_argument('--htf-tf', dest='htf_tf', default='4h')
    ap.add_argument('--htf-adx-n', dest='htf_adx_n', type=int, default=14)
    ap.add_argument('--htf-slope-ma', dest='htf_slope_ma', type=int, default=50)
    ap.add_argument('--htf-slope-lb', dest='htf_slope_lb', type=int, default=10)
    ap.add_argument('--z-in2', dest='z_in2', type=float, default=2.5)
    ap.add_argument('--tier-lot', dest='tier_lot', type=float, default=0.5)
    ap.add_argument('--tier3-zs', dest='tier3_zs', type=float, nargs=3, default=[2.0, 2.5, 3.0])
    ap.add_argument('--tier3-lots', dest='tier3_lots', type=float, nargs=3, default=[0.2, 0.3, 0.5])
    ap.add_argument('--partial-z', dest='partial_z', type=float, default=1.5)
    args = ap.parse_args()

    if args.trades:
        tdf = pd.read_csv(args.trades)
        tag = args.tag or os.path.splitext(os.path.basename(args.trades))[0]
    else:
        tag = args.tag or f"mr_{'-'.join(args.pairs)}_{args.tf}_{args.confirm}_{args.split}"
        print(f'dynamic_lot_mr_bt を実走 (pairs={args.pairs} tf={args.tf} '
              f'confirm={args.confirm} split={args.split})')
        tdf = trades_from_bt(args.pairs, args.tf, args, args.split)

    if tdf is None or len(tdf) == 0:
        print('[error] 解析対象のトレードがありません。')
        return
    for col in ('hold_bars', 'z_in_actual', 'net_pips', 'side'):
        if col not in tdf.columns:
            raise SystemExit(f'[error] トレードログに列 {col} がありません。')
    tdf = tdf.copy()
    tdf['abs_z'] = tdf['z_in_actual'].abs()

    he, hl = hold_edges()
    ze, zl = z_edges()
    hold_df = bin_stats(tdf, 'hold_bars', he, hl)
    z_df = bin_stats(tdf, 'abs_z', ze, zl)

    report_facts(tdf, hold_df, z_df, tag)
    png = plot_bins(hold_df, z_df, tag)
    print(f'[png] {png}')
    out = os.path.join(HERE, f'mr_distribution_{tag}.csv')
    hold_df.assign(axis='hold').to_csv(out, index=False)
    z_df.assign(axis='z').to_csv(out, mode='a', index=False, header=False)
    print(f'[csv] {out}')


if __name__ == '__main__':
    main()
