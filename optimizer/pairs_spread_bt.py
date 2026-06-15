"""
pairs_spread_bt.py - Stage B: 日足ペアトレード(共和分スプレッドのOU平均回帰)フルBT

Stage A(pairs_cointegration_screen.py)で合格ペアのみを回すのが原則。Stage A 合格は
ゼロ(共和分は antipodean=beta≈1=AUDNZD縮約 に限られ、しかも OOS で residual が
random walk 化)。ここでは「採用バーを実BTでも越えないこと」を実証して Close を確定するため、
IS で最も共和分の強い候補群に対し、過去Close(三角stat_arb)の敗因を全是正した執行で回す:

  - 日足(1h でない) / シグナルは t-1 close / **全約定 next-bar(=当日) open** / 三角恒等式は除外。
  - スプレッド = log(P_A) - beta*log(P_B)。beta と z 統計(mu,sd)は **IS=2015-2021 で凍結**。
  - z = (resid - mu)/sd。|z|>z_in でフェード(高=spread short / 低=spread long)。
    決済: |z|<z_out で回帰。ストップ: |z|>z_stop。時間切れ: max_hold 日。
  - サイジング: ドル中立(beta調整・脚Aを単位グロス, 脚Bを beta グロス)・vol-target(IS spread日次vol)。
  - コスト: **両脚それぞれにフル bid-ask を1往復差引**(2脚=コスト2倍が pairs-trade の鬼門)。
    現実値とその 1.5倍で感応度を点検。

実行: .venv_dukas/bin/python optimizer/pairs_spread_bt.py
出力: pairs_spread_bt_result.csv + console
"""
import numpy as np
import pandas as pd
from pathlib import Path
import statsmodels.api as sm

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'pairs_spread_bt_result.csv'

IS_START, IS_END = '2015-01-01', '2021-12-31'
OOS_START, OOS_END = '2022-01-01', '2026-12-31'

# 採用バー(事前登録): OOS PF>1.2 ∧ OOS Sharpe>0 ∧ WFO各fold(年次)正 ∧ IS-selectable。
Z_IN, Z_OUT, Z_STOP, MAX_HOLD = 2.0, 0.5, 3.5, 60

# 候補(Stage A で EGp<0.15 = IS で最も共和分が強い群。三角恒等式は無し)。
# pip_a/pip_b: フル bid-ask(price単位)。majors~1.0pip, crosses~2.0pip 相当を保守的に設定。
PAIRS = [
    ('AUDUSD', 'NZDUSD', 0.00010, 0.00010),
    ('AUDCAD', 'NZDCAD', 0.00020, 0.00020),
    ('AUDCHF', 'NZDCHF', 0.00020, 0.00020),
    ('EURCAD', 'GBPCAD', 0.00020, 0.00025),
    ('AUDCHF', 'CADCHF', 0.00020, 0.00020),
    ('NZDCHF', 'CADCHF', 0.00020, 0.00020),
    ('NZDUSD', 'USDCAD', 0.00010, 0.00010),
]


def load_ohlc_d1(sym):
    d1 = DATA / f'{sym}_D1_dukas.csv'
    h1 = DATA / f'{sym}_1h_dukas.csv'
    if d1.exists():
        df = pd.read_csv(d1, parse_dates=['datetime']).set_index('datetime')
        out = df[['open', 'close']]
    elif h1.exists():
        df = pd.read_csv(h1, parse_dates=['datetime']).set_index('datetime')
        out = pd.DataFrame({'open': df['open'].resample('1D').first(),
                            'close': df['close'].resample('1D').last()}).dropna()
    else:
        raise FileNotFoundError(sym)
    out = out[~out.index.duplicated(keep='last')].sort_index()
    return out


def frozen_beta(la_is, lb_is):
    X = sm.add_constant(lb_is)
    res = sm.OLS(la_is, X).fit()
    return res.params.iloc[1], res.params.iloc[0]


def run_pair(a, b, pip_a, pip_b, cost_mult=1.0):
    da, db = load_ohlc_d1(a), load_ohlc_d1(b)
    df = pd.concat([da.add_prefix('a_'), db.add_prefix('b_')], axis=1, sort=True).dropna()
    is_m = (df.index >= IS_START) & (df.index <= IS_END)

    la_c, lb_c = np.log(df['a_close']), np.log(df['b_close'])
    beta, const = frozen_beta(la_c[is_m], lb_c[is_m])
    resid_c = la_c - (const + beta * lb_c)
    mu, sd = resid_c[is_m].mean(), resid_c[is_m].std()
    z = (resid_c - mu) / sd

    # 往復コスト(return単位): 各脚フル bid-ask を entry+exit で計4回クロス -> 2*(cost_a + beta*cost_b)
    # (entry: A buy/sell + B; exit: A + B = full spread per leg once over round trip => use 1x spread per leg)
    idx = df.index.to_numpy()
    o_a = df['a_open'].to_numpy(); o_b = df['b_open'].to_numpy()
    z_arr = z.to_numpy()
    n = len(df)

    # cost(return) per leg = full spread fraction, charged once per leg over the round trip.
    pos = 0          # 0 flat, +1 long spread, -1 short spread
    entry_i = -1
    trades = []      # (exit_i, dir, gross_ret, cost_ret, net_ret, hold)
    daily = np.zeros(n)  # daily MTM net return (vol未調整, per unit gross-A)

    def cost_ret(i):
        ca = pip_a / o_a[i]
        cb = pip_b / o_b[i]
        return cost_mult * (ca + beta * cb)

    for t in range(1, n - 1):
        # MTM while in position: open(t)->open(t+1) increment realized at t+1; account at t open-to-open
        if pos != 0:
            ra = o_a[t] / o_a[t - 1] - 1.0
            rb = o_b[t] / o_b[t - 1] - 1.0
            daily[t] = pos * (ra - beta * rb)
        # signal on t-1 close (z_arr[t-1]); execute at open[t]
        zt = z_arr[t - 1]
        if pos == 0:
            if zt > Z_IN:
                pos, entry_i = -1, t  # short spread (fade high)
                daily[t] -= cost_ret(t)
            elif zt < -Z_IN:
                pos, entry_i = +1, t
                daily[t] -= cost_ret(t)
        else:
            hold = t - entry_i
            exit_now = (abs(zt) < Z_OUT) or (abs(zt) > Z_STOP) or (hold >= MAX_HOLD)
            if exit_now:
                # close at open[t]
                gross = pos * ((o_a[t] / o_a[entry_i] - 1.0) - beta * (o_b[t] / o_b[entry_i] - 1.0))
                c = cost_ret(entry_i) + cost_ret(t)
                trades.append((t, pos, gross, c, gross - c, hold))
                daily[t] -= cost_ret(t)
                pos = 0
    # force-close at end
    if pos != 0:
        t = n - 1
        gross = pos * ((o_a[t] / o_a[entry_i] - 1.0) - beta * (o_b[t] / o_b[entry_i] - 1.0))
        c = cost_ret(entry_i) + cost_ret(t)
        trades.append((t, pos, gross, c, gross - c, t - entry_i))

    tr = pd.DataFrame(trades, columns=['i', 'dir', 'gross', 'cost', 'net', 'hold'])
    tr['date'] = idx[tr['i'].to_numpy()] if len(tr) else pd.Series([], dtype='datetime64[ns]')
    dser = pd.Series(daily, index=df.index)
    return beta, tr, dser, is_m


def stats(tr, dser, mask_dates):
    """tr: trades df; dser: daily ret; mask: boolean on dser.index for the segment."""
    seg_tr = tr[tr['date'].between(mask_dates[0], mask_dates[1])] if len(tr) else tr
    seg_d = dser[(dser.index >= mask_dates[0]) & (dser.index <= mask_dates[1])]
    net = seg_tr['net']
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    pf = gp / gl if gl > 1e-12 else np.inf
    dd = seg_d.cumsum()
    maxdd = (dd.cummax() - dd).max()
    sh = seg_d.mean() / seg_d.std() * np.sqrt(252) if seg_d.std() > 0 else 0.0
    return dict(n=len(seg_tr), pf=round(pf, 3), net=round(net.sum(), 5),
                wr=round((net > 0).mean(), 3) if len(net) else 0.0,
                sharpe=round(sh, 2), maxdd=round(maxdd, 5))


def main():
    rows = []
    for a, b, pa, pb in PAIRS:
        beta, tr, dser, is_m = run_pair(a, b, pa, pb, cost_mult=1.0)
        is_s = stats(tr, dser, (pd.Timestamp(IS_START), pd.Timestamp(IS_END)))
        oos_s = stats(tr, dser, (pd.Timestamp(OOS_START), pd.Timestamp(OOS_END)))
        # cost sensitivity 1.5x
        _, tr15, dser15, _ = run_pair(a, b, pa, pb, cost_mult=1.5)
        oos15 = stats(tr15, dser15, (pd.Timestamp(OOS_START), pd.Timestamp(OOS_END)))
        # yearly WFO (OOS years)
        wfo = {}
        for yr in range(2022, 2027):
            s = stats(tr, dser, (pd.Timestamp(f'{yr}-01-01'), pd.Timestamp(f'{yr}-12-31')))
            wfo[yr] = s['pf'] if s['n'] >= 3 else None
        wfo_vals = [v for v in wfo.values() if v is not None]
        wfo_min = min(wfo_vals) if wfo_vals else None
        wfo_pos = sum(1 for v in wfo_vals if v > 1.0)
        rows.append(dict(
            pair=f'{a}/{b}', beta=round(beta, 3),
            is_n=is_s['n'], is_pf=is_s['pf'], is_sh=is_s['sharpe'],
            oos_n=oos_s['n'], oos_pf=oos_s['pf'], oos_sh=oos_s['sharpe'], oos_maxdd=oos_s['maxdd'],
            oos_pf_cost15=oos15['pf'],
            wfo_min=wfo_min, wfo_pos=f'{wfo_pos}/{len(wfo_vals)}',
        ))
        print(f"{a}/{b:8s} beta={beta:+.2f} | IS pf={is_s['pf']:.2f} sh={is_s['sharpe']:+.1f} n={is_s['n']} "
              f"| OOS pf={oos_s['pf']:.2f} sh={oos_s['sharpe']:+.1f} n={oos_s['n']} dd={oos_s['maxdd']:.3f} "
              f"| cost1.5x OOSpf={oos15['pf']:.2f} | WFO min={wfo_min} pos={wfo_pos}/{len(wfo_vals)}")

    res = pd.DataFrame(rows)
    res.to_csv(OUT, index=False)
    print(f'\nwrote {OUT}')
    passB = res[(res.oos_pf > 1.2) & (res.oos_sh > 0) & (res.is_pf > 1.2)]
    print('\n=== 採用バー(OOS PF>1.2 ∧ OOS Sharpe>0 ∧ IS-selectable) 通過 ===')
    print('  なし' if not len(passB) else passB.to_string(index=False))


if __name__ == '__main__':
    main()
