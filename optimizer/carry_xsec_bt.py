"""
carry_xsec_bt.py - 横断的(cross-sectional)キャリー・ファクター 検証BT。

経済仮説:
    高金利通貨をlong・低金利通貨をshortするドル中立ポートフォリオを月次(or週次)
    リバランスで保有すると、キャリー・プレミアム(数十年の学術的証拠を持つリスク
    プレミアム)により OOS でも Sharpe>0 の頑健なリターンが出るか。
    価格パターンに依存しない初の戦略(per-pair時系列でなく通貨横断の相対値・ファンダ駆動)。

ユニバース: USD/EUR/GBP/CHF/JPY/AUD/NZD/CAD (8通貨)。
    7本のUSDメジャー D1 (Dukascopy, dukas=真値) で8通貨を完全スパン:
      EUR=EURUSD, GBP=GBPUSD, AUD=AUDUSD, NZD=NZDUSD,
      JPY=1/USDJPY, CHF=1/USDCHF, CAD=1/USDCAD, USD=1.0(基準)。

signal(金利):
    data/policy_rates.csv (build_policy_rates.py が出力, 政策金利の決定日付き履歴)。
    各通貨の対USD金利差 = rate_ccy - rate_USD。**ルックアヘッド厳禁** =
    日次にffill後 shift(1) で「t時点で既知=t-1まで」のレートのみ使用。
    リターンにもキャリー利回り (r_ccy - r_USD)/252 を日次加算(これがプレミアム本体)。

執行: 月末(or週次)にランク → 上位N long / 下位N short, ドル中立・等金額(or金利差加重)。
    リバランス翌営業日から発効(next-bar fill)。往復スプレッド+ロールをturnoverに比例で差引。
    vol-target sizing でex-anteボラを一定化(リスク調整リターンの公平比較)。

規律: IS=2015-2021凍結 / OOS=2022-2026 / 年次WFO。signal/weightは全てt-1既知。
    評価軸 = Sharpe / maxDD / Calmar / 年次勝率 (薄標本のためPFは従)。
    過適合signature点検(IS↔OOS逆相関 / 単一局面依存 / N・頻度curve-fit)。
    carry-crash診断(リスクオフ局面の単月最大DD・テール)。

出力: optimizer/carry_xsec_bt_result.csv (全バリアントのメトリクス)
      optimizer/carry_xsec_daily.csv (採用候補の日次リターン, ブレンド検証用)
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
OUT_DIR = Path(__file__).resolve().parent

# 通貨 -> (USDメジャーのシンボル, invert)。invert=True なら 1/price で ccy/USD 価格化。
CCY_DEF = {
    'EUR': ('EURUSD', False),
    'GBP': ('GBPUSD', False),
    'AUD': ('AUDUSD', False),
    'NZD': ('NZDUSD', False),
    'JPY': ('USDJPY', True),
    'CHF': ('USDCHF', True),
    'CAD': ('USDCAD', True),
}
CCYS = ['USD', 'EUR', 'GBP', 'CHF', 'JPY', 'AUD', 'NZD', 'CAD']

ANN = 252
IS_END = pd.Timestamp('2021-12-31')
OOS_START = pd.Timestamp('2022-01-01')


def load_prices():
    """各通貨の対USD価格(1単位ccyのUSD建て価値)D1 を返す DataFrame(cols=CCYS without weekends)."""
    px = {}
    idx = None
    for ccy, (sym, inv) in CCY_DEF.items():
        df = pd.read_csv(DATA / f'{sym}_D1_dukas.csv', parse_dates=['datetime'])
        df = df.set_index('datetime').sort_index()
        s = df['close'].astype(float)
        if inv:
            s = 1.0 / s
        px[ccy] = s
        idx = s.index if idx is None else idx.union(s.index)
    pxdf = pd.DataFrame(px).reindex(idx).ffill()
    pxdf['USD'] = 1.0
    # 週末(土日)の薄い/重複バーを除外: 平日のみ
    pxdf = pxdf[pxdf.index.dayofweek < 5]
    return pxdf[CCYS]


def load_rates(idx):
    """日次の政策金利(%)を返す。announce+1で発効(shift(1))= t時点で既知のレートのみ。"""
    rt = pd.read_csv(DATA / 'policy_rates.csv', parse_dates=['date'])
    wide = rt.pivot(index='date', columns='currency', values='rate')
    daily = wide.reindex(idx.union(wide.index)).sort_index().ffill().reindex(idx)
    daily = daily.shift(1)  # 決定日翌営業日から既知 -> lookahead無し
    daily = daily.ffill().bfill()  # 期首の欠損を埋める(2015初頭)
    return daily[CCYS]


def build_returns(px, rates):
    """通貨ごと 対USD トータル日次リターン = spotリターン + キャリー利回り(金利差/252)。"""
    spot = px.pct_change()
    spot['USD'] = 0.0
    diff = rates.sub(rates['USD'], axis=0)  # rate_ccy - rate_USD (%)
    carry = diff / 100.0 / ANN  # 日次キャリー利回り
    carry['USD'] = 0.0
    rets = spot + carry
    return rets.fillna(0.0), diff


def metrics(daily, label=''):
    """日次リターン系列のメトリクス。"""
    daily = daily.dropna()
    if len(daily) < 30 or daily.std() == 0:
        return {}
    ann_ret = daily.mean() * ANN
    ann_vol = daily.std() * np.sqrt(ANN)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    calmar = ann_ret / abs(dd) if dd < 0 else np.nan
    # 月次PF・年次勝率
    m = (1 + daily).resample('ME').prod() - 1
    pf = m[m > 0].sum() / abs(m[m < 0].sum()) if (m < 0).any() else np.inf
    y = (1 + daily).resample('YE').prod() - 1
    ywin = (y > 0).mean()
    return dict(ann_ret=ann_ret, ann_vol=ann_vol, sharpe=sharpe, maxDD=dd,
                calmar=calmar, monthly_pf=pf, yr_winrate=ywin, n_days=len(daily))


def run_strategy(rets, diff, N=3, freq='M', weighting='equal', long_only=False,
                 target_vol=0.10, cost_bp=2.0, vol_lookback=60, vol_clip=(0.25, 3.0)):
    """
    横断キャリー戦略の日次リターン系列を返す。
    freq: 'M'=月末リバランス / 'W'=週次(金曜)。
    weighting: 'equal' or 'diff'(金利差加重)。
    long_only: True なら上位N longバスケットのみ(ドル建てショート無し)。
    vol-target: 過去vol_lookback日のポート実現volで target_vol に正規化(t-1, clip)。
    cost: リバランス時 turnover * cost_bp(片道, bp)。
    """
    idx = rets.index
    rcols = [c for c in CCYS if c != 'USD'] if False else CCYS
    # リバランス日(各期間の最終営業日)
    if freq == 'M':
        grouper = idx.to_period('M')
    else:
        grouper = idx.to_period('W')
    reb_dates = pd.Series(idx, index=idx).groupby(grouper).last().values
    reb_dates = pd.DatetimeIndex(reb_dates)

    raw_w = pd.DataFrame(0.0, index=idx, columns=CCYS)
    for reb in reb_dates:
        d = diff.loc[reb].drop('USD')  # 対USD金利差(USDは基準0なので除外してランク)
        d = d.dropna()
        if len(d) < 2 * N:
            continue
        order = d.sort_values(ascending=False)
        longs = order.index[:N]
        shorts = order.index[-N:]
        w = pd.Series(0.0, index=CCYS)
        if weighting == 'equal':
            for c in longs:
                w[c] = 1.0 / N
            if not long_only:
                for c in shorts:
                    w[c] = -1.0 / N
        else:  # diff加重 (金利差の大きさで重み付け, 正規化)
            lw = d[longs].clip(lower=0)
            lw = lw / lw.sum() if lw.sum() > 0 else pd.Series(1.0 / N, index=longs)
            for c in longs:
                w[c] = lw[c]
            if not long_only:
                sw = (-d[shorts]).clip(lower=0)
                sw = sw / sw.sum() if sw.sum() > 0 else pd.Series(1.0 / N, index=shorts)
                for c in shorts:
                    w[c] = -sw[c]
        # long_only の場合 USDショートで資金調達(=対USD long バスケット)。ドル中立にはしない。
        # 発効は翌営業日から
        fut = idx[idx > reb]
        if len(fut) == 0:
            continue
        raw_w.loc[fut[0]:, :] = 0.0  # 次のリバランスまで保持されるので一旦上書き
        raw_w.loc[fut[0]:, CCYS] = w.values

    # vol-target: 各日の生ウェイトに対し、過去vol_lookback日の実現ポートvolで翌日スケール
    base_port = (raw_w.shift(1) * rets).sum(axis=1)  # 生ウェイトでの日次(発効はshiftで翌日)
    realized = base_port.rolling(vol_lookback).std() * np.sqrt(ANN)
    scale = (target_vol / realized).clip(*vol_clip)
    scale = scale.shift(1).fillna(1.0)  # t-1のvol情報のみ
    w_scaled = raw_w.mul(scale, axis=0)

    # ポート日次リターン(発効=前日ウェイト)
    port = (w_scaled.shift(1) * rets).sum(axis=1)
    # コスト: ウェイト変化(turnover)に比例。w_scaled は日次でほぼ一定→変化日のみ課金
    turn = w_scaled.diff().abs().sum(axis=1).fillna(0.0)
    cost = turn * (cost_bp / 1e4)
    port = port - cost
    port.name = f'N{N}_{freq}_{weighting}{"_LO" if long_only else ""}'
    return port, w_scaled


def split_metrics(port, label):
    rows = []
    for seg, s in [('FULL', port), ('IS', port[port.index <= IS_END]),
                   ('OOS', port[port.index >= OOS_START])]:
        m = metrics(s)
        if m:
            m = {'variant': label, 'segment': seg, **m}
            rows.append(m)
    return rows


def yearly_sharpe(port):
    out = {}
    for yr, s in port.groupby(port.index.year):
        m = metrics(s)
        out[yr] = m.get('sharpe', np.nan) if m else np.nan
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target-vol', type=float, default=0.10)
    ap.add_argument('--cost-bp', type=float, default=2.0)
    args = ap.parse_args()

    px = load_prices()
    rates = load_rates(px.index)
    rets, diff = build_returns(px, rates)
    print(f'price index: {px.index[0].date()} .. {px.index[-1].date()} ({len(px)} bars)')
    print('mean rate diff vs USD (full, %):')
    print(diff.mean().round(2).to_string())

    variants = []
    # メインのスイープ: N=2,3 / monthly,weekly / equal,diff / long-only
    # (N=4 は 2N>7 で long/short が非重複に組めず infeasible -> 除外)
    configs = []
    for N in (2, 3):
        for freq in ('M', 'W'):
            for wt in ('equal', 'diff'):
                configs.append(dict(N=N, freq=freq, weighting=wt, long_only=False))
        configs.append(dict(N=N, freq='M', weighting='equal', long_only=True))

    all_rows = []
    daily_store = {}
    yearly_rows = []
    for cfg in configs:
        port, _ = run_strategy(rets, diff, target_vol=args.target_vol,
                               cost_bp=args.cost_bp, **cfg)
        label = port.name
        all_rows += split_metrics(port, label)
        daily_store[label] = port
        ys = yearly_sharpe(port)
        yearly_rows.append({'variant': label, **{f'sh{y}': round(v, 2) if pd.notna(v) else None
                                                  for y, v in ys.items()}})

    res = pd.DataFrame(all_rows)
    res.to_csv(OUT_DIR / 'carry_xsec_bt_result.csv', index=False)
    yr = pd.DataFrame(yearly_rows)
    yr.to_csv(OUT_DIR / 'carry_xsec_yearly_sharpe.csv', index=False)

    # 採用候補の日次をブレンド検証用に保存(代表 = N3 M equal)
    rep = daily_store['N3_M_equal']
    pd.DataFrame({'date': rep.index, 'ret': rep.values}).to_csv(
        OUT_DIR / 'carry_xsec_daily.csv', index=False)

    # ベンチマーク: 等金額 long-all-non-USD バスケット(=短USD), vol-targetなし
    bench = rets[[c for c in CCYS if c != 'USD']].mean(axis=1)
    all_rows_bench = split_metrics(bench, 'BENCH_eqbasket')

    # --- 表示 ---
    pd.set_option('display.width', 200, 'display.max_columns', 20)
    show = res.copy()
    for c in ['ann_ret', 'ann_vol', 'maxDD']:
        show[c] = (show[c] * 100).round(1)
    for c in ['sharpe', 'calmar', 'monthly_pf', 'yr_winrate']:
        show[c] = show[c].round(2)
    print('\n=== 全バリアント メトリクス ===')
    print(show[['variant', 'segment', 'ann_ret', 'ann_vol', 'sharpe', 'maxDD',
                'calmar', 'monthly_pf', 'yr_winrate', 'n_days']].to_string(index=False))

    print('\n=== 年次 Sharpe ===')
    print(yr.to_string(index=False))

    print('\n=== ベンチマーク(等金額バスケット) ===')
    bdf = pd.DataFrame(all_rows_bench)
    for c in ['ann_ret', 'ann_vol', 'maxDD']:
        bdf[c] = (bdf[c] * 100).round(1)
    print(bdf[['segment', 'ann_ret', 'ann_vol', 'sharpe', 'maxDD', 'calmar']].round(2).to_string(index=False))

    # --- IS-selectable 点検: IS Sharpe vs OOS Sharpe (採用バー(a)= IS↔OOS整合) ---
    print('\n=== IS-selectable 点検 (採用バー(a): IS Sharpe>0 必須) ===')
    piv = res.pivot_table(index='variant', columns='segment', values='sharpe')
    print(piv[['IS', 'OOS', 'FULL']].round(2).to_string())
    cc = piv['IS'].corr(piv['OOS'])
    print(f'corr(IS Sharpe, OOS Sharpe) across variants = {cc:.2f}'
          f'  ({"逆相関=非頑健signature" if cc < 0 else "整合" if cc > 0.5 else "弱い/無相関"})')

    # --- carry-crash 診断: 代表構成の単月最悪 ---
    print('\n=== carry-crash 診断 (N3_M_equal 月次リターン worst 8) ===')
    m = (1 + rep).resample('ME').prod() - 1
    print((m.sort_values()[:8] * 100).round(2).to_string())

    print('\nwrote carry_xsec_bt_result.csv / carry_xsec_yearly_sharpe.csv / carry_xsec_daily.csv')


if __name__ == '__main__':
    main()
