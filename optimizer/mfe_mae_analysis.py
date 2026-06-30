"""
mfe_mae_analysis.py - MFE/MAE 確率分布・可視化ツール (死因分析用)。

目的 (2026-06-30):
    逆張り戦略の負けトレードが
      (A)「エントリー後に順行したが TP に届かずノイズで SL に狩られた」のか、
      (B)「エントリー直後から完全な逆行トレンドに飲み込まれた」のか、
    を MFE/MAE の確率分布で統計的に切り分ける。

用語:
    MFE (Maximum Favorable Excursion): 保有中に最大で何 pips 含み益になったか (>=0)。
    MAE (Maximum Adverse Excursion) : 保有中に最大で何 pips 含み損になったか (>=0, 絶対値)。

入力 (いずれか):
    1) --trades <csv>: 既存のトレードログ。
       必須列: side(long/short), entry, net_pips。
       MFE/MAE は (a) mfe_pips/mae_pips 列があればそれを使用、
                  (b) 無ければ entry_t/exit_t + data/<pair>_<tf> から再計算。
    2) 引数なし: liquidity_sweep_bt を AUDCAD/EURGBP で実走し、その trades を
       バー系列から MFE/MAE まで enrich して解析 (前回の逆張り検証の死因分析)。

出力:
    - mfe_mae_analysis_<tag>.png: 勝ち/負けの MFE・MAE 確率密度(KDE)+ヒストグラム +
      MAE-MFE 散布図 (アウトカム色分け)。
    - mfe_mae_analysis_<tag>.csv: トレード単位の MFE/MAE/net_pips。
    - 標準出力: 分布統計(平均/標準偏差/歪度/尖度/分位点) + 死因診断サマリ。

検証規律: MFE/MAE は保有期間中の確定バー high/low のみを使用 (lookahead 無し)。

使用法:
    python3 optimizer/mfe_mae_analysis.py
    python3 optimizer/mfe_mae_analysis.py --tf 15m --pairs AUDCAD EURGBP
    python3 optimizer/mfe_mae_analysis.py --trades path/to/trades.csv --pair AUDCAD --tf 1h
"""

import argparse
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import liquidity_sweep_bt as LS   # データロード・pip 定義・逆張りエンジンを再利用

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------------------
# MFE/MAE の算出
# ----------------------------------------------------------------------------
def enrich_trades_with_excursion(trades, df, pip):
    """trades(side/entry/entry_t/exit_t を含む)に保有期間中の MFE/MAE(pips)を付与。

    保有区間 = [entry_idx, exit_idx]。entry_idx は約定足(= entry_t の位置)。
    その区間の high の最大 / low の最小だけで含み益・含み損のピークを測る。
    """
    idx = df.index
    h = df['high'].to_numpy()
    l = df['low'].to_numpy()
    pos = pd.Series(np.arange(len(idx)), index=idx)
    out = []
    for t in trades:
        ei = int(pos.get(t['entry_t']))
        xi = int(pos.get(t['exit_t'])) if t['exit_t'] in pos.index else len(idx) - 1
        if xi < ei:
            xi = ei
        seg_hi = h[ei:xi + 1].max()
        seg_lo = l[ei:xi + 1].min()
        entry = t['entry']
        if t['side'] == 'long':
            mfe = (seg_hi - entry) / pip
            mae = (entry - seg_lo) / pip
        else:  # short
            mfe = (entry - seg_lo) / pip
            mae = (seg_hi - entry) / pip
        rec = dict(t)
        rec['mfe_pips'] = max(mfe, 0.0)
        rec['mae_pips'] = max(mae, 0.0)
        out.append(rec)
    return out


def trades_dataframe(trades):
    df = pd.DataFrame(trades)
    df['win'] = df['net_pips'] > 0
    return df


# ----------------------------------------------------------------------------
# 統計 (scipy 非依存: 自前実装)
# ----------------------------------------------------------------------------
def moments(x):
    """平均/標準偏差/歪度(skewness)/超過尖度(excess kurtosis)。"""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return dict(n=0, mean=np.nan, std=np.nan, skew=np.nan, kurt=np.nan,
                    p10=np.nan, p25=np.nan, p50=np.nan, p75=np.nan, p90=np.nan)
    m = x.mean()
    s = x.std(ddof=0)
    if s == 0 or n < 2:
        skew = kurt = 0.0
    else:
        skew = float(((x - m) ** 3).mean() / s ** 3)
        kurt = float(((x - m) ** 4).mean() / s ** 4 - 3.0)
    p = np.percentile(x, [10, 25, 50, 75, 90])
    return dict(n=n, mean=float(m), std=float(s), skew=skew, kurt=kurt,
                p10=p[0], p25=p[1], p50=p[2], p75=p[3], p90=p[4])


def gaussian_kde(samples, grid, bw=None):
    """Scott 帯域のガウシアン KDE (scipy 非依存)。"""
    samples = np.asarray(samples, float)
    n = len(samples)
    if n < 2:
        return np.zeros_like(grid)
    std = samples.std(ddof=1)
    if std <= 0:
        std = 1e-9
    if bw is None:
        bw = std * n ** (-1.0 / 5.0)   # Scott's rule
    if bw <= 0:
        bw = 1e-9
    u = (grid[:, None] - samples[None, :]) / bw
    k = np.exp(-0.5 * u ** 2) / np.sqrt(2 * np.pi)
    return k.sum(axis=1) / (n * bw)


def kde_peak(samples, lo, hi):
    """KDE の最頻値(統計的に有意なピーク位置)を返す。"""
    samples = np.asarray(samples, float)
    if len(samples) < 2 or hi <= lo:
        return np.nan
    grid = np.linspace(lo, hi, 400)
    dens = gaussian_kde(samples, grid)
    return float(grid[int(np.argmax(dens))])


# ----------------------------------------------------------------------------
# 可視化
# ----------------------------------------------------------------------------
def plot_distributions(tdf, tag, tp_proxy=None):
    wins = tdf[tdf['win']]
    losses = tdf[~tdf['win']]
    mfe_hi = np.percentile(tdf['mfe_pips'], 99) if len(tdf) else 1.0
    mae_hi = np.percentile(tdf['mae_pips'], 99) if len(tdf) else 1.0
    span_hi = max(mfe_hi, mae_hi, 1.0)

    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle(f'MFE/MAE Distribution & Death-Cause Analysis  [{tag}]  '
                 f'n={len(tdf)} (win={len(wins)} loss={len(losses)})', fontsize=12)

    def kde_hist(a, win_v, loss_v, lo, hi, title, xlabel):
        bins = np.linspace(lo, hi, 40)
        if len(win_v):
            a.hist(win_v, bins=bins, density=True, alpha=0.30, color='tab:green',
                   label=f'win (n={len(win_v)})')
        if len(loss_v):
            a.hist(loss_v, bins=bins, density=True, alpha=0.30, color='tab:red',
                   label=f'loss (n={len(loss_v)})')
        grid = np.linspace(lo, hi, 400)
        if len(win_v) >= 2:
            a.plot(grid, gaussian_kde(win_v, grid), color='tab:green', lw=2)
        if len(loss_v) >= 2:
            a.plot(grid, gaussian_kde(loss_v, grid), color='tab:red', lw=2)
        a.set_title(title)
        a.set_xlabel(xlabel)
        a.set_ylabel('density')
        a.legend(fontsize=8)
        a.grid(alpha=0.2)

    # (0,0) MFE 分布
    kde_hist(ax[0, 0], wins['mfe_pips'].values, losses['mfe_pips'].values,
             0, span_hi, 'MFE (favorable excursion)', 'MFE (pips)')
    if tp_proxy:
        ax[0, 0].axvline(tp_proxy, color='black', ls='--', lw=1,
                         label=f'TP proxy={tp_proxy:.1f}')
        ax[0, 0].legend(fontsize=8)

    # (0,1) MAE 分布
    kde_hist(ax[0, 1], wins['mae_pips'].values, losses['mae_pips'].values,
             0, span_hi, 'MAE (adverse excursion)', 'MAE (pips)')

    # (1,0) MAE vs MFE 散布 (アウトカム色分け)
    a = ax[1, 0]
    if len(losses):
        a.scatter(losses['mae_pips'], losses['mfe_pips'], s=14, alpha=0.5,
                  color='tab:red', label='loss')
    if len(wins):
        a.scatter(wins['mae_pips'], wins['mfe_pips'], s=14, alpha=0.5,
                  color='tab:green', label='win')
    lim = span_hi
    a.plot([0, lim], [0, lim], color='gray', ls=':', lw=1)
    if tp_proxy:
        a.axhline(tp_proxy, color='black', ls='--', lw=1)
    a.set_xlim(0, lim)
    a.set_ylim(0, lim)
    a.set_title('MAE vs MFE (per trade)')
    a.set_xlabel('MAE (pips)')
    a.set_ylabel('MFE (pips)')
    a.legend(fontsize=8)
    a.grid(alpha=0.2)

    # (1,1) 負けトレードの MFE 累積分布 (死因の核心)
    a = ax[1, 1]
    if len(losses):
        lv = np.sort(losses['mfe_pips'].values)
        cdf = np.arange(1, len(lv) + 1) / len(lv)
        a.plot(lv, cdf, color='tab:red', lw=2, label='loss MFE CDF')
    if len(wins):
        wv = np.sort(wins['mfe_pips'].values)
        cdf = np.arange(1, len(wv) + 1) / len(wv)
        a.plot(wv, cdf, color='tab:green', lw=2, label='win MFE CDF')
    if tp_proxy:
        a.axvline(tp_proxy, color='black', ls='--', lw=1, label=f'TP proxy={tp_proxy:.1f}')
    a.set_title('MFE cumulative distribution')
    a.set_xlabel('MFE (pips)')
    a.set_ylabel('P(MFE <= x)')
    a.set_xlim(0, span_hi)
    a.legend(fontsize=8)
    a.grid(alpha=0.2)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(HERE, f'mfe_mae_analysis_{tag}.png')
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


# ----------------------------------------------------------------------------
# レポート
# ----------------------------------------------------------------------------
def report(tdf, tag, tp_proxy=None):
    wins = tdf[tdf['win']]
    losses = tdf[~tdf['win']]
    print('=' * 96)
    print(f'[{tag}]  trades={len(tdf)}  win={len(wins)}  loss={len(losses)}  '
          f"WR={len(wins)/max(len(tdf),1)*100:.1f}%")
    print('=' * 96)

    def line(label, m):
        print(f"  {label:18s} n={m['n']:>4d} mean={m['mean']:7.2f} std={m['std']:7.2f} "
              f"skew={m['skew']:+6.2f} kurt={m['kurt']:+6.2f} | "
              f"p10={m['p10']:6.1f} p50={m['p50']:6.1f} p90={m['p90']:6.1f}")

    print('-- MFE (favorable, pips) --')
    line('all', moments(tdf['mfe_pips']))
    line('win', moments(wins['mfe_pips']))
    line('loss', moments(losses['mfe_pips']))
    print('   KDE peak: win=%.2f loss=%.2f' % (
        kde_peak(wins['mfe_pips'], 0, np.percentile(tdf['mfe_pips'], 99) if len(tdf) else 1),
        kde_peak(losses['mfe_pips'], 0, np.percentile(tdf['mfe_pips'], 99) if len(tdf) else 1)))
    print('-- MAE (adverse, pips) --')
    line('all', moments(tdf['mae_pips']))
    line('win', moments(wins['mae_pips']))
    line('loss', moments(losses['mae_pips']))

    # --- 死因診断 ---
    print('-- 死因診断 (negative trades) --')
    if len(losses) == 0:
        print('   負けトレードなし。')
        return
    lo_mfe = losses['mfe_pips'].values
    # TP proxy: 指定が無ければ「勝ちトレードの MFE 中央値」(典型的な利確到達距離) を使う。
    if tp_proxy is None:
        tp_proxy = float(np.median(wins['mfe_pips'])) if len(wins) else float(np.median(lo_mfe))
    frac_ran_far = float((lo_mfe >= tp_proxy).mean())      # 順行が TP 近くまで届いた割合
    frac_immediate = float((lo_mfe <= max(tp_proxy * 0.25, 1.0)).mean())  # ほぼ順行せず
    lo_mae_med = float(np.median(losses['mae_pips']))
    lo_mfe_med = float(np.median(lo_mfe))
    print(f'   TP proxy(=win MFE 中央値) = {tp_proxy:.1f} pips')
    print(f'   負けの MFE 中央値 = {lo_mfe_med:.1f}  / MAE 中央値 = {lo_mae_med:.1f}')
    print(f'   (A) 順行して TP 近くまで届いた負け [MFE>=TP_proxy]   = {frac_ran_far*100:.1f}%')
    print(f'   (B) ほぼ順行せず即逆行した負け    [MFE<=TP_proxy*0.25]= {frac_immediate*100:.1f}%')
    if frac_ran_far >= 0.45:
        verdict = ('(A)優勢: 多くの負けが TP 近くまで順行 → ノイズで SL に狩られている。'
                   'TP を手前に置く/部分利確/トレールで救済余地あり。')
    elif frac_immediate >= 0.45:
        verdict = ('(B)優勢: 多くの負けがほぼ順行せず即逆行 → 完全な逆行トレンドに飲まれている。'
                   'エントリー方向そのものが間違い(エッジ不在)で TP/SL 調整では救えない。')
    else:
        verdict = ('混合: 順行死と即逆行死が拮抗。SL/TP の置き直しでは構造的改善は限定的。')
    print(f'   判定: {verdict}')


def save_csv(tdf, tag):
    cols = [c for c in ['pair', 'side', 'entry_t', 'exit_t', 'entry', 'exit',
                        'net_pips', 'mfe_pips', 'mae_pips', 'win'] if c in tdf.columns]
    out = os.path.join(HERE, f'mfe_mae_analysis_{tag}.csv')
    tdf[cols].to_csv(out, index=False)
    print(f'[csv] {out} ({len(tdf)} 行)')


# ----------------------------------------------------------------------------
# 入力ソース
# ----------------------------------------------------------------------------
def from_backtest(pairs, tf, tp_mode, rr, sweep_pips, sl_pips, sess_start, sess_end):
    """liquidity_sweep_bt を実走し、trades を MFE/MAE まで enrich して結合。"""
    all_trades = []
    for pair in pairs:
        raw = LS.load_data(pair, tf)
        if raw is None or len(raw) < 200:
            print(f'[warn] {pair}: データ不足のためスキップ')
            continue
        df = LS.attach_pdhl(raw)
        meta = LS.PAIR_META.get(pair, {'pip': LS.DEFAULT_PIP, 'cost_pips': LS.DEFAULT_COST_PIPS})
        cfg = {
            'use_sweep_filter': True, 'use_session': True,
            'sess_start': sess_start, 'sess_end': sess_end,
            'sweep_pips': sweep_pips, 'sl_pips': sl_pips,
            'tp_mode': tp_mode, 'rr': rr, 'cost_pips': meta['cost_pips'],
        }
        _, trades = LS.run_bt(df, meta['pip'], cfg)
        trades = enrich_trades_with_excursion(trades, df, meta['pip'])
        for t in trades:
            t['pair'] = pair
        all_trades.extend(trades)
        print(f'  {pair}: {len(trades)} trades')
    return trades_dataframe(all_trades) if all_trades else None


def from_csv(path, pair, tf):
    """既存トレードログを読み込み、必要なら MFE/MAE を再計算する。"""
    raw = pd.read_csv(path)
    if 'side' not in raw or 'net_pips' not in raw:
        raise SystemExit('[error] trades csv には少なくとも side, net_pips 列が必要です。')
    if 'mfe_pips' in raw and 'mae_pips' in raw:
        tdf = raw.copy()
    else:
        if not {'entry_t', 'exit_t', 'entry'} <= set(raw.columns):
            raise SystemExit('[error] mfe/mae 列が無い場合は entry_t/exit_t/entry 列 + --pair/--tf が必要です。')
        data = LS.load_data(pair, tf)
        if data is None:
            raise SystemExit(f'[error] data/{pair}_{tf} が見つかりません。')
        df = LS.attach_pdhl(data)
        meta = LS.PAIR_META.get(pair, {'pip': LS.DEFAULT_PIP})
        raw['entry_t'] = pd.to_datetime(raw['entry_t'], utc=True)
        raw['exit_t'] = pd.to_datetime(raw['exit_t'], utc=True)
        trades = raw.to_dict('records')
        trades = enrich_trades_with_excursion(trades, df, meta['pip'])
        tdf = pd.DataFrame(trades)
    return trades_dataframe(tdf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trades', help='既存トレードログ CSV (side, net_pips 必須)')
    ap.add_argument('--pair', help='--trades で MFE/MAE 再計算する場合の対象ペア')
    ap.add_argument('--pairs', nargs='+', default=['AUDCAD', 'EURGBP'],
                    help='--trades 無し時に liquidity_sweep を走らせるペア')
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--tag', default=None, help='出力ファイルの識別子')
    # liquidity_sweep 走行パラメータ (--trades 無し時)
    ap.add_argument('--tp-mode', dest='tp_mode', default='rr', choices=['rr', 'mid', 'opposite'])
    ap.add_argument('--rr', type=float, default=1.5)
    ap.add_argument('--sweep-pips', dest='sweep_pips', type=float, default=1.0)
    ap.add_argument('--sl-pips', dest='sl_pips', type=float, default=2.0)
    ap.add_argument('--sess-start', dest='sess_start', type=int, default=6)
    ap.add_argument('--sess-end', dest='sess_end', type=int, default=16)
    ap.add_argument('--tp-proxy', dest='tp_proxy', type=float, default=None,
                    help='死因診断の TP 距離(pips)。未指定なら勝ちMFE中央値')
    args = ap.parse_args()

    if args.trades:
        tag = args.tag or os.path.splitext(os.path.basename(args.trades))[0]
        tdf = from_csv(args.trades, args.pair, args.tf)
    else:
        tag = args.tag or f"liqsweep_{'-'.join(args.pairs)}_{args.tf}_{args.tp_mode}"
        print(f'liquidity_sweep_bt を実走 (pairs={args.pairs} tf={args.tf} '
              f'tp={args.tp_mode} rr={args.rr})')
        tdf = from_backtest(args.pairs, args.tf, args.tp_mode, args.rr,
                            args.sweep_pips, args.sl_pips, args.sess_start, args.sess_end)

    if tdf is None or len(tdf) == 0:
        print('[error] 解析対象のトレードがありません。')
        return

    report(tdf, tag, args.tp_proxy)
    png = plot_distributions(tdf, tag, args.tp_proxy if args.tp_proxy
                             else (float(np.median(tdf[tdf['win']]['mfe_pips']))
                                   if (tdf['win']).any() else None))
    print(f'[png] {png}')
    save_csv(tdf, tag)


if __name__ == '__main__':
    main()
