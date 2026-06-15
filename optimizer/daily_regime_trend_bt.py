"""
daily_regime_trend_bt.py - 日足レジーム・ゲート付きトレンドフォロー BT。

対象 = Grid(平均回帰)で救済不可と確定した3ペア: GBPJPY / CHFJPY / EURUSD。

経済的根拠(なぜこの建付けがこのペアの構造に合うか):
    年次PF診断(grid_yearly_pf_diag.py)で判明した救済不可の原因は3ペア共通で
    「レンジ/平均回帰しない=年単位の持続トレンドや政策ジャンプ」だった。
      - GBPJPY : JPYキャリーで年単位の持続トレンド(平均回帰グリッドが焼ける)
      - CHFJPY : SNB政策レジーム転換・キャリー(政策ジャンプ)
      - EURUSD : USD強弱サイクル(マクロ・トレンド。レンジ・エッジ皆無)
    => 平均回帰の真逆=トレンドフォローこそ本来の土俵のはず。だが既検証の
       「1h順張り4戦略」「日足の素のTSMOM」は全滅/弱いだけだった。
       素の順張りの敗因は『choppy局面でのmomentumフリップ往復(whipsaw)』。
       本スクリプトの仮説 = "いつ張るか" を制御する=トレンドが本当に
       進行中(ADX/効率比が高い)のバーだけ建て、それ以外は待機する。
       choppyな往復損を母集団から除けば、日足TSMOMの弱い正エッジが
       採用バーを越える(=構造的)かを検証する。

既検証(本スクリプトの起点):
    日足 TSMOM_100 (trend_10y_bt.py --tf daily):
      CHFJPY full1.34 / IS1.53 / OOS1.17 / n139 / yr+75%  (OOSのみ1.2未達)
      EURUSD full1.21 / IS1.14 / OOS1.30 / n163 / yr+67%  (ISが弱め)
      GBPJPY full0.79 (順張りでも負け=whipsawの典型)

検証する軸:
    entry  : TSMOM(lookback本リターン符号) を方向シグナルとする(state的)。
    gate   : ADX(adx_n) >= adx_th  かつ/または  efficiency_ratio(er_n) >= er_th。
             (どちらも t-1 で評価=ルックアヘッド無し)
    dir    : both / long (JPYキャリー=long-tilt の構造仮説の切り分け用)
    exit   : 初期SL = sl_atr*ATR / シャンデリアATRトレイル(trail_atr) /
             time stop(max_hold) / 反対momentumでドテン。

執行(現実的・trend_10y_bt.pyと同一規律):
    日足(1hからリサンプル)。シグナルは確定足、約定は次足open(next-bar fill)。
    1ポジ。往復スプレッド差引(JPY 2.0pip / 非JPY 1.0pip)。ATR=日足ATR14(ewm)。
    全特徴量 t-1 shift。

評価: IS=2015-2021凍結 / OOS=2022-2026 / 年次WFO(各年をOOS foldとして PF)。
      採用バー: IS PF>=IS_BAR(selectable) ∧ OOS PF>1.2 ∧ WFO各fold>1.0。
実行: .venv_dukas/bin/python optimizer/daily_regime_trend_bt.py
"""
import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
ATR_PERIOD = 14
IS_END = pd.Timestamp('2021-12-31 23:59:59')
IS_BAR = 1.20  # IS-selectable の閾値(BB/Grid と同基準)


def load_1h(sym):
    df = pd.read_csv(DATA_DIR / f'{sym}_1h_dukas.csv', parse_dates=['datetime'])
    df = df.dropna(subset=['open', 'high', 'low', 'close']).sort_values('datetime')
    return df.drop_duplicates(subset=['datetime'], keep='first').reset_index(drop=True)


def resample_daily(df):
    g = df.set_index('datetime').resample('1D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    return g.dropna(subset=['open', 'high', 'low', 'close']).reset_index()


def atr_ewm(df, period=ATR_PERIOD):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def adx(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def efficiency_ratio(close, n):
    # Kaufman ER = |net change| / sum(|change|)。1に近いほどトレンド、0でchoppy。
    change = close.diff(n).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (change / vol).replace([np.inf, -np.inf], np.nan)


def pip_size(pair):
    return 0.01 if pair.endswith('JPY') else 0.0001


def spread_price(pair):
    return (2.0 if pair.endswith('JPY') else 1.0) * pip_size(pair)


def build(df):
    df = df.copy()
    df['atr'] = atr_ewm(df)
    df['adx'] = adx(df)
    return df


def run_bt(df, pair, lookback, sl_atr, trail_atr, max_hold,
           adx_th=0.0, er_n=0, er_th=0.0, direction='both'):
    """TSMOM方向 + レジーム・ゲート。全特徴量 t-1, 約定 i+1 open。"""
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    atr = df['atr'].values
    adxv = df['adx'].values
    t = df['datetime'].values
    n = len(df)
    spr = spread_price(pair)

    mom = c - np.concatenate([[np.nan] * lookback, c[:-lookback]])
    desired = np.sign(np.nan_to_num(mom))  # +1/-1/0
    if er_n > 0:
        er = efficiency_ratio(df['close'], er_n).values
    else:
        er = np.ones(n)

    trades = []
    pos = 0
    entry_px = entry_atr = 0.0
    entry_i = 0
    stop = extreme = 0.0

    i = 1
    while i < n:
        if pos == 0:
            d = desired[i - 1]
            gate = (not np.isnan(adxv[i - 1]) and adxv[i - 1] >= adx_th
                    and (er_n == 0 or (not np.isnan(er[i - 1]) and er[i - 1] >= er_th)))
            if direction == 'long' and d < 0:
                d = 0
            if d != 0 and gate and not np.isnan(atr[i - 1]) and atr[i - 1] > 0:
                pos = int(d)
                entry_px = o[i]
                entry_atr = atr[i - 1]
                entry_i = i
                extreme = h[i] if pos == 1 else l[i]
                stop = entry_px - pos * sl_atr * entry_atr
            i += 1
            continue

        exit_px = None
        if pos == 1:
            extreme = max(extreme, h[i])
            cur_stop = max(stop, extreme - trail_atr * entry_atr)
            if l[i] <= cur_stop:
                exit_px = cur_stop
        else:
            extreme = min(extreme, l[i])
            cur_stop = min(stop, extreme + trail_atr * entry_atr)
            if h[i] >= cur_stop:
                exit_px = cur_stop
        if exit_px is None and (i - entry_i) >= max_hold:
            exit_px = o[i]
        if exit_px is None and desired[i - 1] == -pos:  # ドテン
            exit_px = o[i]
        if exit_px is not None:
            pnl = pos * (exit_px - entry_px) - spr
            trades.append({'entry_time': t[entry_i], 'dir': pos,
                           'pnl_price': pnl, 'pnl_R': pnl / (sl_atr * entry_atr),
                           'bars': i - entry_i})
            pos = 0
            continue
        i += 1
    return pd.DataFrame(trades)


def pf_of(tr):
    if len(tr) == 0:
        return 0.0
    p = tr['pnl_price'].values
    w = p[p > 0].sum()
    los = -p[p < 0].sum()
    return float(w / los) if los > 0 else np.inf


def metrics(tr, pip):
    if len(tr) == 0:
        return dict(n=0, pf=0, wr=0, net_pip=0, sharpe=0, maxdd_R=0)
    p = tr['pnl_price'].values
    R = tr['pnl_R'].values
    eq = np.cumsum(R)
    peak = np.maximum.accumulate(np.concatenate([[0], eq]))
    dd = np.concatenate([[0], eq]) - peak
    sh = R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0.0
    return dict(n=len(tr), pf=round(pf_of(tr), 3), wr=round((p > 0).mean() * 100, 1),
                net_pip=round(p.sum() / pip, 1), sharpe=round(sh, 2),
                maxdd_R=round(abs(dd.min()), 1))


def split(tr):
    if len(tr) == 0:
        return tr, tr
    et = pd.to_datetime(tr['entry_time'])
    return tr[et <= IS_END], tr[et > IS_END]


def wfo_folds(tr):
    """各年(entry_time)を1 foldとし fold別 PF。返り値: {year: (pf, n)}"""
    if len(tr) == 0:
        return {}
    g = tr.copy()
    g['year'] = pd.to_datetime(g['entry_time']).dt.year
    out = {}
    for y, sub in g.groupby('year'):
        out[int(y)] = (round(pf_of(sub), 2), len(sub))
    return out


def yr_pos_rate(tr):
    if len(tr) == 0:
        return 0.0
    g = tr.copy()
    g['year'] = pd.to_datetime(g['entry_time']).dt.year
    yr = g.groupby('year')['pnl_price'].sum()
    return round((yr > 0).mean() * 100, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', nargs='+',
                    default=['GBPJPY', 'CHFJPY', 'EURUSD', 'USDJPY', 'NZDJPY'])
    ap.add_argument('--out', default='daily_regime_trend_bt_result.csv')
    args = ap.parse_args()

    # 探索グリッド: lookback x adx_th x (er_n,er_th) x direction
    lookbacks = [60, 100, 150]
    adx_ths = [0, 20, 25, 30]
    er_grid = [(0, 0.0), (20, 0.30), (20, 0.40), (30, 0.35)]
    directions = ['both', 'long']
    sl_atr, trail_atr, max_hold = 2.0, 3.0, 90

    dfs = {}
    for p in args.pairs:
        dfs[p] = build(resample_daily(load_1h(p))).dropna(subset=['atr', 'adx']).reset_index(drop=True)
        print(f'{p}: {len(dfs[p])} daily bars '
              f'{dfs[p].datetime.iloc[0].date()}..{dfs[p].datetime.iloc[-1].date()}')

    rows = []
    for p in args.pairs:
        df = dfs[p]
        pip = pip_size(p)
        for lb, adx_th, (er_n, er_th), d in itertools.product(
                lookbacks, adx_ths, er_grid, directions):
            tr = run_bt(df, p, lb, sl_atr, trail_atr, max_hold,
                        adx_th=adx_th, er_n=er_n, er_th=er_th, direction=d)
            full = metrics(tr, pip)
            tis, toos = split(tr)
            mis, moos = metrics(tis, pip), metrics(toos, pip)
            folds = wfo_folds(toos)  # OOS年のみをWFO foldとする
            fold_pfs = [v[0] for v in folds.values() if v[1] >= 3]  # n>=3年のみ
            wfo_min = min(fold_pfs) if fold_pfs else 0.0
            wfo_med = round(float(np.median(fold_pfs)), 2) if fold_pfs else 0.0
            passed = (mis['pf'] >= IS_BAR and moos['pf'] > 1.2 and
                      mis['n'] >= 40 and moos['n'] >= 25 and wfo_min > 1.0)
            rows.append(dict(
                pair=p, lb=lb, adx=adx_th, er_n=er_n, er_th=er_th, dir=d,
                full_pf=full['pf'], full_n=full['n'], full_net=full['net_pip'],
                full_sh=full['sharpe'], full_dd=full['maxdd_R'],
                is_pf=mis['pf'], is_n=mis['n'], oos_pf=moos['pf'], oos_n=moos['n'],
                wfo_min=wfo_min, wfo_med=wfo_med, yr_pos=yr_pos_rate(tr),
                PASS='YES' if passed else ''))

    res = pd.DataFrame(rows)
    outpath = Path(__file__).parent / args.out
    res.to_csv(outpath, index=False)
    print(f'\nsaved -> {outpath}  ({len(res)} configs)')

    npass = (res['PASS'] == 'YES').sum()
    print(f'\n=== PASS (IS>={IS_BAR} & OOS>1.2 & wfoMin>1.0): {npass} ===')
    cols = ['pair', 'lb', 'adx', 'er_n', 'er_th', 'dir', 'full_pf', 'is_pf',
            'oos_pf', 'oos_n', 'wfo_min', 'wfo_med', 'yr_pos', 'full_sh']
    if npass:
        print(res[res['PASS'] == 'YES'][cols].to_string(index=False))
    for p in args.pairs:
        sub = res[res.pair == p].sort_values('oos_pf', ascending=False).head(4)
        print(f'\n--- {p}: top4 by OOS PF ---')
        print(sub[cols].to_string(index=False))


if __name__ == '__main__':
    main()
