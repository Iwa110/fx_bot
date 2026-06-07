"""
stat_arb_nonjpy_bt.py - 案α: 非JPY三角スプレッド (EURUSD/GBPUSD/EURGBP) stat_arb BT.

方針転換背景:
  Grid補完弁(案A/B)はClose済み。Gridと構造的に独立した領域で新規エッジを探す。
  案α = stat_arb のOU平均回帰を非JPY三角関係に適用し、Gridと無相関なエッジを検証。

三角裁定:
  EURGBP = EURUSD / GBPUSD  (理論恒等式) なので
  spread = log(EURGBP) - log(EURUSD) + log(GBPUSD)  は理論上 0 付近で平均回帰。
  (執行/流動性/レート差で乖離 -> OU過程として逆張り)

ロジック:
  - OU推定: W日ローリングでAR(1)再推定 (毎日先頭バーで再fit, 当日はその値をhold/lookahead無)。
      X_t = a + b X_{t-1} + e   ->  theta=-ln(b)/dt, mu=a/(1-b),
      sigma_eq = std(e)/sqrt(1-b^2), half_life = ln(2)/theta  [hour, dt=1h]
  - エントリ(flat時): z=(spread-mu)/sigma_eq が +Nで spread空売り / -Nで spread買い。
  - 出口: spread が mu(0sigma)へ回帰 OR time-based上限。
  - 半減期フィルタ: half_life が cap超のOU(遅すぎ=トレンド)は新規エントリ除外。

PnL/コスト:
  - lot=0.1固定。pnl_jpy = direction*(spread_exit-spread_entry)*NOTIONAL_JPY。
  - 三角3レッグ往復の執行コストを COST(log-spread単位)で控除(各レッグ~0.5pip相当)。
  - PF/Sharpe/勝率/トレード数は線形スケールに不感(notional/capitalの選定に頑健)。
    maxDD%のみ資本前提に依存するため CAPITAL/NOTIONAL を明示。

出力(スコアカードのみ):
  optimizer/results/stat_arb_nonjpy_bt_scorecard.csv  (全81組合せ IS/OOS)
  + 独立性確認(Grid日次PnL相関 / Grid DD上位5区間stat_arb損益 / ブレンドPF)

Usage:
  python stat_arb_nonjpy_bt.py
"""

import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd

# Grid側ヘルパ/設定の再利用 (独立性確認で使用)
import grid_floatstop_bt as G
import pullback_grid_complement as PC

if platform.system() == 'Windows':
    DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
    OUT_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer\results')
else:
    DATA_DIR = str(Path(__file__).parent.parent / 'data')
    OUT_DIR = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)
SCORECARD_CSV = OUT_DIR / 'stat_arb_nonjpy_bt_scorecard.csv'
INDEP_CSV = OUT_DIR / 'stat_arb_nonjpy_independence.csv'

LEGS = ['EURGBP', 'EURUSD', 'GBPUSD']

# --- IS/OOS 分割 ---
IS_START, IS_END = '2024-04-01', '2025-06-30'
OOS_START, OOS_END = '2025-07-01', '2026-05-31'

# --- グリッドサーチ ---
ENTRY_Z = [1.5, 2.0, 2.5]
OU_WINDOW_D = [30, 60, 90]           # days
TIME_EXIT_H = [24, 48, 72]           # hours
HALFLIFE_CAP_H = [None, 48, 72]      # hours (None=フィルタ無)

# --- PnL前提 (ratio系メトリクスには不感, maxDD%表示用) ---
LOT = 0.1
CONTRACT = 100_000.0
EURJPY_APPROX = 165.0                 # base(EUR) notional -> JPY 概算
NOTIONAL_JPY = LOT * CONTRACT * EURJPY_APPROX   # 0.1*100k*165 = 1,650,000
CAPITAL = 1_000_000.0
# 三角3レッグ往復の執行コスト(log-spread単位). 各レッグ~0.5pip*2(往復)概算:
#   EURUSD 0.5pip~5e-5 / GBPUSD~4e-5 / EURGBP~6e-5 -> 片道~1.5e-4 / 往復~3e-4
COST = 3.0e-4

DT_H = 1.0  # 1h足


def load_close(pair):
    path = os.path.join(DATA_DIR, pair + '_1h.csv')
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df['close'].sort_index().dropna()


def build_spread():
    closes = {p: load_close(p) for p in LEGS}
    df = pd.DataFrame(closes).dropna()
    spread = np.log(df['EURGBP']) - np.log(df['EURUSD']) + np.log(df['GBPUSD'])
    spread.name = 'spread'
    return spread


def ou_fit(x):
    """AR(1) closed-form. 戻り: (mu, sigma_eq, half_life_h) or None(非回帰)."""
    if len(x) < 30:
        return None
    xp, xc = x[:-1], x[1:]
    vp = xp.var()
    if vp <= 0:
        return None
    b = np.cov(xp, xc, ddof=0)[0, 1] / vp
    if not (0.0 < b < 1.0):
        return None  # 非平均回帰(トレンド/発散)
    a = xc.mean() - b * xp.mean()
    resid = xc - (a + b * xp)
    sig_resid = resid.std(ddof=2)
    if sig_resid <= 0:
        return None
    mu = a / (1.0 - b)
    sigma_eq = sig_resid / np.sqrt(1.0 - b * b)
    half_life = np.log(2.0) / (-np.log(b)) * DT_H
    return mu, sigma_eq, half_life


def daily_ou_params(spread, window_d):
    """各日先頭バーで trailing window_d 日 fit -> hourly ffill (lookahead無)."""
    win = int(window_d * 24)
    vals = spread.values
    idx = spread.index
    # 日替わりバー(=その日の最初のバー)を特定
    day = idx.normalize()
    is_first = np.empty(len(idx), dtype=bool)
    is_first[0] = True
    is_first[1:] = day[1:] != day[:-1]
    mu_s = np.full(len(idx), np.nan)
    sg_s = np.full(len(idx), np.nan)
    hl_s = np.full(len(idx), np.nan)
    for i in np.where(is_first)[0]:
        if i < win:
            continue
        res = ou_fit(vals[i - win:i])   # i未満のみ使用(当該バー除外)
        if res is None:
            continue
        mu_s[i], sg_s[i], hl_s[i] = res
    mu = pd.Series(mu_s, index=idx).ffill()
    sg = pd.Series(sg_s, index=idx).ffill()
    hl = pd.Series(hl_s, index=idx).ffill()
    return mu, sg, hl


def run_sim(spread, mu, sg, hl, entry_z, time_exit_h, hl_cap):
    """イベント駆動シミュレーション (NEXT-BAR fill = 信号はバーi終値で判定し約定はi+1終値).
    intra-barのlookahead(信号バー終値で約定)を排除. 戻り: trades DataFrame."""
    s = spread.values
    mu_v, sg_v, hl_v = mu.values, sg.values, hl.values
    idx = spread.index
    n = len(s)

    pos = 0          # 0=flat, +1=long spread, -1=short spread
    entry_px = 0.0
    entry_i = 0
    entry_hl = np.nan
    trades = []

    # 約定はi+1終値なので i は n-1 未満まで
    for i in range(n - 1):
        m, g, h = mu_v[i], sg_v[i], hl_v[i]
        if np.isnan(m) or np.isnan(g) or g <= 0:
            continue
        z = (s[i] - m) / g
        fill = s[i + 1]   # 次バー終値で約定

        if pos == 0:
            if hl_cap is not None and (np.isnan(h) or h > hl_cap):
                continue
            if np.isnan(h):
                continue
            if z >= entry_z:
                pos, entry_px, entry_i, entry_hl = -1, fill, i + 1, h   # short spread
            elif z <= -entry_z:
                pos, entry_px, entry_i, entry_hl = +1, fill, i + 1, h   # long spread
        else:
            held_h = (i + 1 - entry_i) * DT_H
            reverted = (pos == 1 and z >= 0.0) or (pos == -1 and z <= 0.0)
            timed = held_h >= time_exit_h
            if reverted or timed:
                gross = pos * (fill - entry_px)
                net_spread = gross - COST
                pnl_jpy = net_spread * NOTIONAL_JPY
                trades.append({
                    'exit_time': idx[i + 1], 'dir': pos, 'held_h': held_h,
                    'pnl_jpy': pnl_jpy, 'entry_hl': entry_hl,
                    'reason': 'revert' if reverted else 'time',
                })
                pos = 0
    return pd.DataFrame(trades)


def score(trades):
    """スコアカード辞書. PF/Sharpe/maxDD%/勝率/トレード数/平均半減期."""
    if trades is None or len(trades) == 0:
        return dict(pf=np.nan, sharpe=np.nan, maxdd_pct=np.nan,
                    wr=np.nan, n=0, hl=np.nan, net=0.0)
    p = trades['pnl_jpy'].values
    gp = p[p > 0].sum()
    gl = -p[p < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    wr = 100.0 * (p > 0).mean()
    # Sharpe: 日次集計 -> 年率化(252)
    d = trades.set_index('exit_time')['pnl_jpy']
    daily = d.groupby(d.index.normalize()).sum()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else np.nan
    # maxDD% (資本CAPITAL前提)
    eq = CAPITAL + np.cumsum(p)
    peak = np.maximum.accumulate(eq)
    maxdd_pct = float((1.0 - eq / peak).max() * 100.0)
    return dict(pf=pf, sharpe=sharpe, maxdd_pct=maxdd_pct, wr=wr,
                n=len(p), hl=float(trades['entry_hl'].mean()), net=float(p.sum()))


def main():
    print('=== 案α: 非JPY三角スプレッド stat_arb BT ===\n')
    spread = build_spread()
    print(f'spread = log(EURGBP)-log(EURUSD)+log(GBPUSD)')
    print(f'期間: {spread.index.min().date()} ~ {spread.index.max().date()} ({len(spread)} bars)')
    print(f'spread mean={spread.mean():.6f} std={spread.std():.6f}\n')

    # window別 OUパラメータを事前計算(再利用)
    ou_cache = {}
    for w in OU_WINDOW_D:
        ou_cache[w] = daily_ou_params(spread, w)
        print(f'  OU params computed: window={w}d')
    print()

    def slice_period(df, start, end):
        if len(df) == 0:
            return df
        m = (df['exit_time'] >= pd.Timestamp(start, tz='UTC')) & \
            (df['exit_time'] <= pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1))
        return df[m]

    rows = []
    for w in OU_WINDOW_D:
        mu, sg, hl = ou_cache[w]
        for z in ENTRY_Z:
            for te in TIME_EXIT_H:
                for cap in HALFLIFE_CAP_H:
                    trades = run_sim(spread, mu, sg, hl, z, te, cap)
                    is_t = slice_period(trades, IS_START, IS_END)
                    oos_t = slice_period(trades, OOS_START, OOS_END)
                    sis, soos = score(is_t), score(oos_t)
                    rows.append({
                        'window_d': w, 'entry_z': z, 'time_exit_h': te,
                        'hl_cap': cap if cap is not None else 'none',
                        'is_pf': sis['pf'], 'is_sharpe': sis['sharpe'],
                        'is_maxdd%': sis['maxdd_pct'], 'is_wr': sis['wr'],
                        'is_n': sis['n'], 'is_hl': sis['hl'],
                        'oos_pf': soos['pf'], 'oos_sharpe': soos['sharpe'],
                        'oos_maxdd%': soos['maxdd_pct'], 'oos_wr': soos['wr'],
                        'oos_n': soos['n'], 'oos_hl': soos['hl'],
                    })
    sc = pd.DataFrame(rows)
    sc.to_csv(SCORECARD_CSV, index=False)

    # ---- スコアカード表示 ----
    pd.set_option('display.width', 200)
    pd.set_option('display.max_columns', 30)
    pd.set_option('display.float_format', lambda v: f'{v:.2f}')
    print('=== 全81組合せ スコアカード ===')
    show = sc.copy()
    for c in ['is_pf', 'is_sharpe', 'is_maxdd%', 'is_wr', 'is_hl',
              'oos_pf', 'oos_sharpe', 'oos_maxdd%', 'oos_wr', 'oos_hl']:
        show[c] = show[c].round(2)
    print(show.to_string(index=False))
    print(f'\nSaved scorecard: {SCORECARD_CSV}\n')

    # ---- 採用基準 ----
    crit = (sc['is_pf'] > 1.3) & (sc['is_sharpe'] > 0.8) & (sc['is_n'] > 30) & \
           (sc['oos_pf'] > 1.1) & (sc['oos_sharpe'] > 0.5)
    passed = sc[crit].copy()
    print('=== 採用基準クリア (IS:PF>1.3/Sharpe>0.8/n>30, OOS:PF>1.1/Sharpe>0.5) ===')
    if len(passed) == 0:
        print('  なし — 採用基準を満たす組合せ無し\n')
    else:
        passed['rank'] = passed['oos_pf'] * passed['oos_sharpe']
        top = passed.sort_values('rank', ascending=False).head(3)
        for _, r in top.iterrows():
            print(f"  window={r['window_d']}d z={r['entry_z']} time={r['time_exit_h']}h "
                  f"hl_cap={r['hl_cap']}")
            print(f"    IS : PF={r['is_pf']:.2f} Sharpe={r['is_sharpe']:.2f} "
                  f"DD={r['is_maxdd%']:.1f}% WR={r['is_wr']:.0f}% n={r['is_n']} hl={r['is_hl']:.0f}h")
            print(f"    OOS: PF={r['oos_pf']:.2f} Sharpe={r['oos_sharpe']:.2f} "
                  f"DD={r['oos_maxdd%']:.1f}% WR={r['oos_wr']:.0f}% n={r['oos_n']} hl={r['oos_hl']:.0f}h")
        print()

    # ---- 独立性確認 ----
    independence_check(spread, ou_cache, sc, passed)


def independence_check(spread, ou_cache, sc, passed):
    print('=== 独立性確認 (vs Grid 日次PnL) ===')
    # 代表構成を選定: 採用クリアの最良, 無ければ IS_PF*OOS_PF 最大の現実的構成
    if len(passed) > 0:
        passed = passed.copy()
        passed['rank'] = passed['oos_pf'] * passed['oos_sharpe']
        best = passed.sort_values('rank', ascending=False).iloc[0]
    else:
        cand = sc[(sc['is_n'] > 30)].copy()
        if len(cand) == 0:
            cand = sc.copy()
        cand['rank'] = cand['is_pf'].fillna(0) * cand['oos_pf'].fillna(0)
        best = cand.sort_values('rank', ascending=False).iloc[0]
    w = int(best['window_d']); z = float(best['entry_z'])
    te = int(best['time_exit_h'])
    cap = None if best['hl_cap'] == 'none' else int(best['hl_cap'])
    print(f"代表構成: window={w}d z={z} time={te}h hl_cap={best['hl_cap']}")

    mu, sg, hl = ou_cache[w]
    trades = run_sim(spread, mu, sg, hl, z, te, cap)
    if len(trades) == 0:
        print('  代表構成でトレード0 -> 独立性確認スキップ')
        return

    # stat_arb 日次PnL(JPY)
    sa = trades.set_index('exit_time')['pnl_jpy']
    sa.index = pd.to_datetime(sa.index).tz_convert(None).normalize()
    sa_daily = sa.groupby(level=0).sum()

    # Grid 日次PnL (pullback_grid_complement のロジック再利用)
    grid_daily = PC.build_grid_daily(PC.GRID_PAIRS)

    lo = max(sa_daily.index.min(), grid_daily.index.min())
    hi = min(sa_daily.index.max(), grid_daily.index.max())
    cal = pd.date_range(lo, hi, freq='D')
    sd = sa_daily.reindex(cal).fillna(0.0)
    gd = grid_daily.reindex(cal).fillna(0.0)

    rows = []
    print('\n--- 相関係数 (stat_arb日次 vs Grid日次) 目標: -0.2~+0.2 ---')
    for col in grid_daily.columns:
        g = gd[col]
        pear_all = np.corrcoef(g.values, sd.values)[0, 1]
        active = (g != 0) | (sd != 0)
        pear_act = np.corrcoef(g[active].values, sd[active].values)[0, 1] if active.sum() > 2 else np.nan
        gw = g.resample('W').sum(); sw = sd.resample('W').sum()
        pear_w = np.corrcoef(gw.values, sw.values)[0, 1]
        flag = 'OK' if abs(pear_all) <= 0.2 else 'NG'
        print(f'  {col:9s} 日次(全)={pear_all:+.3f} 日次(活動)={pear_act:+.3f} 週次={pear_w:+.3f} [{flag}]')
        rows.append({'metric': 'corr', 'grid': col, 'daily_all': round(pear_all, 3),
                     'daily_active': round(pear_act, 3), 'weekly': round(pear_w, 3)})

    print('\n--- Grid(COMBINED) DD上位5区間 と その間のstat_arb損益 ---')
    eps = PC.top_dd_windows(gd['COMBINED'], k=5)
    for j, (p, tr, end, depth) in enumerate(eps, 1):
        sa_sum = sd.loc[p:tr].sum()
        g_sum = gd['COMBINED'].loc[p:tr].sum()
        print(f'  #{j} {p.date()}->{tr.date()}: Grid={g_sum:>12,.0f} 深さ={depth:>12,.0f} '
              f'stat_arb={sa_sum:>+12,.0f}円')
        rows.append({'metric': f'ddwin{j}', 'grid': 'COMBINED', 'start': p.date(),
                     'trough': tr.date(), 'grid_pnl': round(g_sum),
                     'dd_depth': round(depth), 'statarb_jpy': round(sa_sum)})

    print('\n--- ブレンドPF (Grid:stat_arb 比率) ---')
    g_turn = np.abs(gd['COMBINED'].values).sum()
    s_turn = np.abs(sd.values).sum()
    pf0, dd0, net0 = PC.pf_and_dd(gd['COMBINED'])
    pfs, dds, nets = PC.pf_and_dd(sd)
    print(f'  Grid単体(COMBINED): PF={pf0:.3f} maxDD={dd0:,.0f} net={net0:,.0f}')
    print(f'  stat_arb単体      : PF={pfs:.3f} maxDD={dds:,.0f} net={nets:,.0f}')
    rows.append({'metric': 'blend', 'ratio': 'grid_only', 'pf': round(pf0, 3),
                 'maxDD': round(dd0), 'net': round(net0)})
    rows.append({'metric': 'blend', 'ratio': 'statarb_only', 'pf': round(pfs, 3),
                 'maxDD': round(dds), 'net': round(nets)})
    for ratio in [10, 5, 3]:
        # Grid:stat_arb = ratio:1 を資金スループット比で実現
        k = (g_turn / ratio) / s_turn if s_turn > 0 else 0.0
        combined = gd['COMBINED'] + k * sd
        pf, dd, net = PC.pf_and_dd(combined)
        print(f'  {ratio:>2d}:1 (k={k:.2f}): PF={pf:.3f} maxDD={dd:,.0f} net={net:,.0f} '
              f'(Grid単体PF={pf0:.3f})')
        rows.append({'metric': 'blend', 'ratio': f'{ratio}:1', 'k': round(k, 3),
                     'pf': round(pf, 3), 'maxDD': round(dd), 'net': round(net)})

    pd.DataFrame(rows).to_csv(INDEP_CSV, index=False)
    print(f'\nSaved independence: {INDEP_CSV}')


if __name__ == '__main__':
    main()
