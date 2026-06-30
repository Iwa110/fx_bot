"""
audcad_stress_test.py - AUDCAD(4h) 3段不等分割(構成A) の実稼働前ストレステスト。

検証規律(IS/OOS, フルコスト, Lookahead排除, 次足始値約定)は dynamic_lot_mr_bt のエンジンを
そのまま使用(4h は 1h から resample, lookahead 無し)。本スクリプトは「トレード結果の事後評価」。

評価3項目:
  1. 極端値・イベント解析: 高ボラ期間(ATRパーセンタイル上位)/イベント窓(USD指標±24h 実日付 +
     RBA第1火曜±24h 決定論プロキシ)に分離して PF/net/worst を出力し、破滅的逆行が無いか確認。
  2. 期待値分布: 全トレード net(pips) のヒストグラム(テールの向き)・勝ち/負け平均・歪度、
     さらに「不等分割が負けトレードの MAE(lot加重) を物理的に縮小できているか」を 1h baseline と比較。
  3. モンテカルロ: トレード順をシャッフルした 10,000 シナリオの maxDD 分布 → 95%/99% 信頼区間。

出力:
  - audcad_stress_test.png: (左上)net分布 (右上)maxDD分布(MC) (左下)MAE比較 (右下)年次net。
  - audcad_stress_test_result.csv: セグメント別集計。
  - 標準出力: 各評価の数値サマリ。

使用法:
  python3 optimizer/audcad_stress_test.py
  python3 optimizer/audcad_stress_test.py --pair AUDCAD --tf 4h --mc 10000
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


class _Args:
    def __init__(self, **kw):
        d = dict(n=40, z_in=2.0, z_tp=0.0, z_stop=4.5, max_hold=48, zk=1.0, max_lot=3.0,
                 squeeze_lo=0.2, squeeze_mult=0.5, vol_hi=0.9, vol_hi_mult=0.5,
                 atr_n=14, atr_lookback=500, ema_span=5, rsi_n=7, rsi_os=30, rsi_ob=70,
                 confirm_window=6, adx_max=25.0, slope_max=1.0, z_in2=2.5, tier_lot=0.5,
                 htf_tf='4h', htf_adx_n=14, htf_slope_ma=50, htf_slope_lb=10,
                 tier3_zs=[2.0, 2.5, 3.0], tier3_lots=[0.2, 0.3, 0.5], partial_z=1.5)
        d.update(kw)
        self.__dict__.update(d)


# ----------------------------------------------------------------------------
# トレード生成 (構成A: 不等分割 一括決済)
# ----------------------------------------------------------------------------
def gen_trades(pair, tf, exit_mode='A'):
    raw = M.load_tf(pair, tf)
    if raw is None:
        raise SystemExit(f'[error] {pair} {tf} データ無し')
    meta = LS.PAIR_META[pair]
    ind = M.add_indicators(raw[['open', 'high', 'low', 'close']].copy(), 40, 14, 500, 5, 7)
    cfg = M.base_cfg(_Args())
    cfg['z_stop'] = 4.5
    cfg['exit_mode'] = exit_mode
    _, trades = M.run_bt_tiered3(ind, meta['pip'], meta['cost_pips'], cfg)
    tdf = pd.DataFrame(trades)
    tdf['entry_t'] = pd.to_datetime(tdf['entry_t'], utc=True)
    # エントリー時の ATR パーセンタイル(高ボラ判定)を付与
    atr_pct = ind['atr_pct'].reindex(tdf['entry_t']).to_numpy()
    tdf['atr_pct'] = atr_pct
    tdf['win'] = tdf['net_pips'] > 0
    return tdf, ind, meta['pip']


def baseline_trades(pair, tf):
    """baseline(Z=2.0で 1.0ロット一括)を MAE 比較の対照として生成。"""
    raw = M.load_tf(pair, tf)
    meta = LS.PAIR_META[pair]
    ind = M.add_indicators(raw[['open', 'high', 'low', 'close']].copy(), 40, 14, 500, 5, 7)
    cfg = M.base_cfg(_Args())
    cfg['sizing_mode'] = 'fixed'
    cfg['confirm_mode'] = 'none'
    cfg['z_stop'] = 4.5
    _, trades = M.run_bt(ind, meta['pip'], meta['cost_pips'], cfg)
    return pd.DataFrame(trades)


# ----------------------------------------------------------------------------
# イベント窓
# ----------------------------------------------------------------------------
def usd_event_times():
    """news_events.csv の USD 指標(NFP/CPI)実日付(2022-26)。AUDCAD は USD/CAD 脚 +
    リスクセンチメント経由で USD 指標に強く反応するため代表イベントとして使用。"""
    p = os.path.join(M.HERE if hasattr(M, 'HERE') else HERE, '..', 'data', 'news_events.csv')
    p = os.path.normpath(os.path.join(HERE, '..', 'data', 'news_events.csv'))
    if not os.path.exists(p):
        return pd.DatetimeIndex([])
    e = pd.read_csv(p)
    e = e[e['currency'] == 'USD']
    ts = pd.to_datetime(e['date'] + ' ' + e['time'], utc=True, errors='coerce').dropna()
    return pd.DatetimeIndex(ts.unique())


def rba_proxy_times(start, end):
    """RBA 政策金利は概ね毎月第1火曜(1月除く)。決定論プロキシ(04:30 UTC)。
    厳密な公表時刻ではないが ±24h 窓では十分。全期間(2015-2026)をカバー。"""
    times = []
    for y in range(start.year, end.year + 1):
        for mo in range(1, 13):
            if mo == 1:
                continue
            d = pd.Timestamp(year=y, month=mo, day=1, tz='UTC')
            # 第1火曜
            offset = (1 - d.weekday()) % 7
            first_tue = d + pd.Timedelta(days=offset)
            times.append(first_tue + pd.Timedelta(hours=4, minutes=30))
    ts = pd.DatetimeIndex(times)
    return ts[(ts >= start) & (ts <= end)]


def in_event_window(entry_times, event_times, hours=24):
    """entry_times の各時刻が、いずれかの event_times の ±hours 以内なら True。
    epoch 秒(float) で比較して tz/int64 の罠を回避。"""
    if len(event_times) == 0:
        return np.zeros(len(entry_times), dtype=bool)
    et = np.array([t.timestamp() for t in entry_times])
    ev = np.sort(np.array([t.timestamp() for t in event_times]))
    win = hours * 3600.0
    idx = np.searchsorted(ev, et)
    out = np.zeros(len(et), dtype=bool)
    for k, t in enumerate(et):
        for jj in (idx[k] - 1, idx[k]):
            if 0 <= jj < len(ev) and abs(ev[jj] - t) <= win:
                out[k] = True
                break
    return out


# ----------------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------------
def seg_metrics(sub):
    if len(sub) == 0:
        return dict(n=0, pf=np.nan, net=0.0, wr=np.nan, worst=0.0, avg=0.0)
    nets = sub['net_pips'].to_numpy()
    gw = nets[nets > 0].sum()
    gl = -nets[nets <= 0].sum()
    pf = gw / gl if gl > 0 else (np.inf if gw > 0 else np.nan)
    return dict(n=len(sub), pf=pf, net=float(nets.sum()), wr=float((nets > 0).mean()),
                worst=float(nets.min()), avg=float(nets.mean()))


def pl(label, m):
    pf = f"{m['pf']:.2f}" if np.isfinite(m['pf']) else ('inf' if m['n'] else '-')
    print(f"  {label:26s} n={m['n']:>4d} PF={pf:>5s} net={m['net']:>8.0f} "
          f"WR={m['wr']*100 if not np.isnan(m['wr']) else 0:4.0f}% "
          f"avg={m['avg']:6.1f} worst={m['worst']:8.1f}")


# ----------------------------------------------------------------------------
# モンテカルロ maxDD
# ----------------------------------------------------------------------------
def mc_maxdd(nets, n_iter=10000, seed=42):
    rng = np.random.default_rng(seed)
    nets = np.asarray(nets, float)
    dds = np.empty(n_iter)
    for k in range(n_iter):
        perm = rng.permutation(nets)
        eq = np.cumsum(perm)
        peak = np.maximum.accumulate(eq)
        dds[k] = (peak - eq).max()
    return dds


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pair', default='AUDCAD')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--mc', type=int, default=10000)
    ap.add_argument('--vol-th', dest='vol_th', type=float, default=0.80,
                    help='高ボラ判定の ATR パーセンタイル閾値')
    args = ap.parse_args()

    tdf, ind, pip = gen_trades(args.pair, args.tf, 'A')
    print('=' * 92)
    print(f'AUDCAD ストレステスト  pair={args.pair} tf={args.tf}  trades={len(tdf)}  '
          f"{tdf['entry_t'].min().date()}~{tdf['entry_t'].max().date()}")
    print(f"  全体: {seg_metrics(tdf)['pf']:.2f} PF / net {tdf['net_pips'].sum():.0f}pip / "
          f"WR {tdf['win'].mean()*100:.1f}%")
    print('=' * 92)

    # IS/OOS と年次 (実稼働の最重要 caveat: ヘッドラインPFがどの期間のものか)
    isd = tdf[tdf['entry_t'] < M.IS_END]
    ood = tdf[tdf['entry_t'] >= M.IS_END]
    print(f"  IS(2015-2021) PF={seg_metrics(isd)['pf']:.2f} net={isd['net_pips'].sum():.0f} n={len(isd)}  |  "
          f"OOS(2022-2026) PF={seg_metrics(ood)['pf']:.2f} net={ood['net_pips'].sum():.0f} n={len(ood)}")
    yr_net = tdf.assign(year=tdf['entry_t'].dt.year).groupby('year')['net_pips'].sum()
    neg_years = [int(y) for y, v in yr_net.items() if v < 0]
    print(f"  ⚠️ 年次で純損失の年: {neg_years}  (= マクロ高ボラ年。下記 高ボラセグメントと一致)")

    rows = []
    # ===== 1. 極端値・イベント解析 =====
    print('\n[1] 極端値・イベント解析')
    hi = tdf[tdf['atr_pct'] >= args.vol_th]
    lo = tdf[tdf['atr_pct'] < args.vol_th]
    print(f'  -- 高ボラ(ATR pct>={args.vol_th}) vs 通常 --')
    pl(f'高ボラ', seg_metrics(hi))
    pl('通常', seg_metrics(lo))
    rows += [{'seg': 'highvol', **seg_metrics(hi)}, {'seg': 'normal', **seg_metrics(lo)}]

    usd = usd_event_times()
    rba = rba_proxy_times(tdf['entry_t'].min(), tdf['entry_t'].max())
    et = tdf['entry_t']
    in_usd = in_event_window(et, usd, 24)
    in_rba = in_event_window(et, rba, 24)
    # USD指標は 2022-26 のみ収録 -> その期間内で event vs 非event を比較
    usd_period = (et >= usd.min()).to_numpy() if len(usd) else np.zeros(len(et), bool)
    print('  -- イベント窓 ±24h --')
    pl('USD指標±24h(2022-26)', seg_metrics(tdf[in_usd]))
    pl('  (同期間 非イベント)', seg_metrics(tdf[usd_period & (~in_usd)]))
    pl('RBA第1火曜±24h(全期間)', seg_metrics(tdf[in_rba]))
    pl('  (非RBA窓)', seg_metrics(tdf[~in_rba]))
    rows += [{'seg': 'usd_event', **seg_metrics(tdf[in_usd])},
             {'seg': 'rba_proxy', **seg_metrics(tdf[in_rba])}]
    worst_overall = tdf['net_pips'].min()
    print(f'  -- 破滅的逆行チェック: 全期間 worst単発トレード = {worst_overall:.1f} pip(lot加重) '
          f"(高ボラ worst={seg_metrics(hi)['worst']:.1f} / 通常 worst={seg_metrics(lo)['worst']:.1f})")

    # ===== 2. 期待値分布 =====
    print('\n[2] 期待値分布')
    nets = tdf['net_pips'].to_numpy()
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    skew = float(((nets - nets.mean()) ** 3).mean() / (nets.std() ** 3)) if nets.std() else 0.0
    print(f'  net分布: mean={nets.mean():.2f} std={nets.std():.2f} skew={skew:+.2f} '
          f'(skew>0=利益側へテール / <0=損失側へテール)')
    print(f'  勝ち平均={wins.mean():.1f}pip (n={len(wins)}) / 負け平均={losses.mean():.1f}pip '
          f'(n={len(losses)}) / payoff={abs(wins.mean()/losses.mean()):.2f}' if len(losses) else '')
    # MAE 比較: tier3(4h) vs 4h baseline(分割効果を分離) vs 1h baseline(spec要求)
    base4 = baseline_trades(args.pair, '4h')
    base1 = baseline_trades(args.pair, '1h')

    def loss_mae(d):
        return d[d['net_pips'] <= 0]['mae_lotpips']
    print('  -- MAE(lot加重, pips) 比較: 不等分割が含み損を物理的に縮小しているか --')
    print(f'    {"構成":24s}{"全med":>8s}{"全p95":>8s}{"負けmed":>9s}{"負けp95":>9s}')
    for label, d in [('4h tier3(0.2/0.3/0.5)', tdf['mae_lotpips'].to_frame('mae_lotpips').assign(net_pips=tdf['net_pips'])),
                     ('4h baseline(z2 x1.0)', base4), ('1h baseline(z2 x1.0)', base1)]:
        all_m = d['mae_lotpips']
        lm = loss_mae(d)
        print(f'    {label:24s}{all_m.median():>8.1f}{all_m.quantile(.95):>8.1f}'
              f'{lm.median():>9.1f}{lm.quantile(.95):>9.1f}')
    t_loss_mae = loss_mae(tdf.assign())
    b4 = loss_mae(base4).median()
    b1 = loss_mae(base1).median()
    red4 = (1 - t_loss_mae.median() / b4) * 100 if b4 else float('nan')
    red1 = (1 - t_loss_mae.median() / b1) * 100 if b1 else float('nan')
    print(f'    → 負けMAE中央値の縮小: 同TF比較(vs 4h baseline)={red4:+.0f}% / '
          f'1h baseline比={red1:+.0f}%。')
    print(f'      同TF比較が「不等分割そのもの」の効果(浅Zで0.2lotのみ=含み損が物理的に小)。')
    b_loss_mae = loss_mae(base1)

    # ===== 3. モンテカルロ maxDD =====
    print('\n[3] モンテカルロ maxDD (トレード順 シャッフル %d 回)' % args.mc)
    dds = mc_maxdd(nets, args.mc)
    realized_eq = np.cumsum(nets)
    realized_dd = (np.maximum.accumulate(realized_eq) - realized_eq).max()
    p = np.percentile(dds, [50, 95, 99, 99.9])
    print(f'  実現 maxDD(時系列どおり) = {realized_dd:.0f} pip(lot加重)')
    print(f'  MC maxDD 分布: 中央値={p[0]:.0f} / 95%ile={p[1]:.0f} / 99%ile={p[2]:.0f} / '
          f'99.9%ile={p[3]:.0f} pip(lot加重)')
    # AUDCAD pip価値: 0.0001 x 100,000(1.0lot) = 10 CAD ≈ 1,080円(CADJPY~108)
    jpy_per_lotpip = 10 * 108
    print(f'  → 95%信頼区間の最悪DD = {p[1]:.0f} lot-pip。')
    print(f'    AUDCAD 1.0lot基準(1 lot-pip≒10CAD≒1,080円)で 95%ile DD ≈ {p[1]*jpy_per_lotpip:,.0f}円 / '
          f'99%ile ≈ {p[2]*jpy_per_lotpip:,.0f}円。')
    print(f'  (注: net_pips は lot加重済み=実際の段別lotを反映。実額は採用スケールlotとCADJPYで比例。)')
    rows.append({'seg': 'mc_dd', 'n': args.mc, 'pf': np.nan, 'net': realized_dd,
                 'wr': np.nan, 'worst': p[1], 'avg': p[0]})

    # ===== 図表 =====
    fig, ax = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(f'AUDCAD {args.tf} tier3+A  Stress Test  (trades={len(tdf)})', fontsize=12)
    # net 分布
    a = ax[0, 0]
    a.hist(nets, bins=60, color='tab:blue', alpha=0.7)
    a.axvline(0, color='k', lw=1)
    a.axvline(nets.mean(), color='tab:green', ls='--', label=f'mean={nets.mean():.1f}')
    a.set_title(f'Net per-trade distribution (skew={skew:+.2f})')
    a.set_xlabel('net pips (lot-weighted)'); a.set_ylabel('count'); a.legend(fontsize=8)
    a.grid(alpha=0.2)
    # MC maxDD
    a = ax[0, 1]
    a.hist(dds, bins=60, color='tab:red', alpha=0.6)
    a.axvline(p[1], color='k', ls='--', label=f'95%ile={p[1]:.0f}')
    a.axvline(p[2], color='purple', ls=':', label=f'99%ile={p[2]:.0f}')
    a.axvline(realized_dd, color='tab:green', ls='-', label=f'realized={realized_dd:.0f}')
    a.set_title('Monte-Carlo maxDD distribution'); a.set_xlabel('maxDD pips (lot-weighted)')
    a.set_ylabel('count'); a.legend(fontsize=8); a.grid(alpha=0.2)
    # MAE 比較 (負けトレード)
    a = ax[1, 0]
    bins = np.linspace(0, max(b_loss_mae.quantile(.97), t_loss_mae.quantile(.97), 1), 40)
    a.hist(b_loss_mae, bins=bins, alpha=0.5, color='tab:orange', density=True,
           label=f'1h baseline (med {b_loss_mae.median():.1f})')
    a.hist(t_loss_mae, bins=bins, alpha=0.5, color='tab:blue', density=True,
           label=f'4h tier3 (med {t_loss_mae.median():.1f})')
    a.set_title('MAE of losing trades (lot-weighted)'); a.set_xlabel('MAE pips')
    a.set_ylabel('density'); a.legend(fontsize=8); a.grid(alpha=0.2)
    # 年次 net + 高ボラ寄与
    a = ax[1, 1]
    tdf['year'] = tdf['entry_t'].dt.year
    yr = tdf.groupby('year')['net_pips'].sum()
    yr_hi = tdf[tdf['atr_pct'] >= args.vol_th].groupby('year')['net_pips'].sum().reindex(yr.index).fillna(0)
    a.bar(yr.index, yr.values, color='tab:blue', alpha=0.6, label='all')
    a.bar(yr.index, yr_hi.values, color='tab:red', alpha=0.7, label='high-vol subset')
    a.axhline(0, color='k', lw=1)
    a.set_title('Net by year (red = high-vol subset)'); a.set_xlabel('year')
    a.set_ylabel('net pips'); a.legend(fontsize=8); a.grid(alpha=0.2)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    png = os.path.join(HERE, 'audcad_stress_test.png')
    fig.savefig(png, dpi=110); plt.close(fig)
    print(f'\n[png] {png}')

    out = os.path.join(HERE, 'audcad_stress_test_result.csv')
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f'[csv] {out}')


if __name__ == '__main__':
    main()
