"""
trend_10y_bt.py - 順張り(トレンドフォロー)戦略 10年バックテスト。

動機:
    BB逆張り(10年で頑健エッジ無し)・Grid平均回帰(AUDCAD/EURGBPのみGo)に対し、
    「平均回帰が負ける=トレンド性ペア(JPYクロス=キャリーで一方向に伸びる)」こそ
    順張りの出番では?という仮説を10年スケールで検証する。
    逆にAUDCAD/EURGBP(レンジ性=Grid Go)では順張りは不利のはず=対照群。

データ:
    data/<SYM>_1h_dukas.csv  (11年, fetch_dukascopy_ohlc.py 由来, UTC naive, BID OHLC)
    columns: datetime,open,high,low,close,volume

検証する4戦略(全て順張り):
    DON   : Donchian channel breakout (タートル型)。直近dc本高値ブレイクでLong。
    DON200: DON + 200SMAレジームフィルタ(上ならLongのみ/下ならShortのみ)。
    EMAX  : EMAクロス(fast上抜けでLong/下抜けでShort)。
    TSMOM : 時系列モメンタム。過去N本リターン符号方向にエントリー。

共通執行モデル(全戦略で同一・現実的):
    - 1h足。シグナルは確定足(close)で判定し、次足openで約定(next-bar fill)。
    - 1ペア同時1ポジション(ピラミッディング無し)。
    - 初期SL = entry -/+ sl_atr*ATR。シャンデリア型ATRトレイル(trail_atr)。
    - time stop: max_hold_h 本超過で成行決済。
    - スプレッド: 1トレード当たり往復コストを price で差引(JPY 2.0pip / 非JPY 1.0pip 相当)。
    - ATR = 1h足ATR14(ewm) ... 実機 risk_manager 整合。

評価:
    IS = 2015-06 ~ 2021末 / OOS = 2022 ~ 2026-06。
    指標: PF / WR / n / net(pip) / 年次黒字率 / per-trade Sharpe / maxDD(R)。
    採用ハードル: IS/OOS 両方 PF>1.2 ∧ n>100(BB/Grid と同基準)。

実行:
    .venv_dukas/bin/python optimizer/trend_10y_bt.py
    (pandas/numpy のみ。.venv_dukas でも素のpythonでも可)
"""

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

PAIRS = ['USDJPY', 'GBPJPY', 'NZDJPY', 'CHFJPY', 'EURUSD', 'EURGBP', 'AUDCAD']

ATR_PERIOD = 14
IS_END   = pd.Timestamp('2021-12-31 23:59:59')
OOS_END  = pd.Timestamp('2026-12-31')

MIN_N = 100   # 採用に必要な最小トレード数(IS/OOSそれぞれ)


# ── データ / インジケーター ──────────────────────────────────
def load_1h(sym: str) -> pd.DataFrame:
    path = DATA_DIR / f'{sym}_1h_dukas.csv'
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.dropna(subset=['open', 'high', 'low', 'close']).sort_values('datetime')
    df = df.drop_duplicates(subset=['datetime'], keep='first').reset_index(drop=True)
    return df


def resample_daily(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index('datetime').resample('1D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    g = g.dropna(subset=['open', 'high', 'low', 'close']).reset_index()
    return g


def atr_ewm(df, period=ATR_PERIOD):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def pip_size(pair):
    return 0.01 if pair.endswith('JPY') else 0.0001


def spread_price(pair):
    # 往復コスト(price)。JPY 2.0pip / 非JPY 1.0pip 相当(保守的)。
    return (2.0 if pair.endswith('JPY') else 1.0) * pip_size(pair)


# ── シグナル生成(戦略ごと) ──────────────────────────────────
# 各戦略は確定足iの情報のみで「方向(+1/-1/0)」を返す配列を作る(t情報, 約定はi+1)。
def sig_donchian(df, dc):
    up = df['high'].rolling(dc).max().shift(1)   # 直近dc本の高値(自分含まず)
    dn = df['low'].rolling(dc).min().shift(1)
    s = pd.Series(0, index=df.index)
    s[df['close'] > up] = 1
    s[df['close'] < dn] = -1
    return s


def sig_donchian_200(df, dc):
    base = sig_donchian(df, dc)
    sma = df['close'].rolling(200).mean()
    s = base.copy()
    s[(base == 1) & (df['close'] < sma)] = 0   # 下降レジームでLong禁止
    s[(base == -1) & (df['close'] > sma)] = 0  # 上昇レジームでShort禁止
    return s


def sig_emax(df, fast, slow):
    ef = df['close'].ewm(span=fast, adjust=False).mean()
    es = df['close'].ewm(span=slow, adjust=False).mean()
    diff = ef - es
    cross_up = (diff > 0) & (diff.shift(1) <= 0)
    cross_dn = (diff < 0) & (diff.shift(1) >= 0)
    s = pd.Series(0, index=df.index)
    s[cross_up] = 1
    s[cross_dn] = -1
    return s


def sig_tsmom(df, lookback):
    ret = df['close'] - df['close'].shift(lookback)
    s = pd.Series(0, index=df.index)
    s[ret > 0] = 1
    s[ret < 0] = -1
    # モメンタムは状態的。状態が変わった足のみ「新規シグナル」とする。
    flip = s != s.shift(1)
    return s.where(flip, 0)


# ── 共通バックテストエンジン ────────────────────────────────
def run_bt(df, pair, signal, sl_atr, trail_atr, max_hold_h):
    """signal: +1/-1/0 のSeries(確定足t)。約定は次足open。
    シャンデリアATRトレイル + 初期SL + time stop。1ポジ。
    返り値: trades DataFrame(entry_time, dir, pnl_price, pnl_R, bars)。"""
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    atr = df['atr'].values
    sig = signal.values
    t = df['datetime'].values
    n = len(df)
    spr = spread_price(pair)

    trades = []
    pos = 0          # 0/+1/-1
    entry_px = 0.0
    entry_atr = 0.0
    entry_i = 0
    stop = 0.0
    extreme = 0.0    # ロングなら最高値, ショートなら最安値

    i = 1
    while i < n:
        if pos == 0:
            s = sig[i - 1]
            if s != 0 and not np.isnan(atr[i - 1]) and atr[i - 1] > 0:
                pos = int(s)
                entry_px = o[i]
                entry_atr = atr[i - 1]
                entry_i = i
                extreme = h[i] if pos == 1 else l[i]
                stop = entry_px - pos * sl_atr * entry_atr
            i += 1
            continue

        # --- ポジション保有中: このバーでの決済判定 ---
        exit_px = None
        if pos == 1:
            extreme = max(extreme, h[i])
            trail = extreme - trail_atr * entry_atr
            cur_stop = max(stop, trail)
            if l[i] <= cur_stop:
                exit_px = cur_stop
        else:
            extreme = min(extreme, l[i])
            trail = extreme + trail_atr * entry_atr
            cur_stop = min(stop, trail)
            if h[i] >= cur_stop:
                exit_px = cur_stop

        # time stop
        if exit_px is None and (i - entry_i) >= max_hold_h:
            exit_px = o[i]

        # 反対シグナルでドテン決済(EMAX/TSMOM向け。DONも反対ブレイクで手仕舞い)
        if exit_px is None and sig[i - 1] == -pos:
            exit_px = o[i]

        if exit_px is not None:
            pnl = pos * (exit_px - entry_px) - spr
            trades.append({
                'entry_time': t[entry_i], 'dir': pos,
                'pnl_price': pnl, 'pnl_R': pnl / (sl_atr * entry_atr),
                'bars': i - entry_i,
            })
            pos = 0
            # 同バーで反対シグナルがあれば次ループで即再エントリー可
            continue
        i += 1

    return pd.DataFrame(trades)


# ── 評価 ─────────────────────────────────────────────────────
def metrics(tr, pip):
    if len(tr) == 0:
        return dict(n=0, pf=0, wr=0, net_pip=0, sharpe=0, maxdd_R=0)
    pnl = tr['pnl_price'].values
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    pf = wins / losses if losses > 0 else np.inf
    R = tr['pnl_R'].values
    sharpe = R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0.0
    eq = np.cumsum(R)
    peak = np.maximum.accumulate(np.concatenate([[0], eq]))
    dd = (np.concatenate([[0], eq]) - peak)
    return dict(
        n=len(tr), pf=round(pf, 3), wr=round((pnl > 0).mean() * 100, 1),
        net_pip=round(pnl.sum() / pip, 1),
        sharpe=round(sharpe, 2), maxdd_R=round(abs(dd.min()), 1),
    )


def yearly_pos_rate(tr):
    if len(tr) == 0:
        return 0.0, 0
    g = tr.copy()
    g['year'] = pd.to_datetime(g['entry_time']).dt.year
    yr = g.groupby('year')['pnl_price'].sum()
    return round((yr > 0).mean() * 100, 0), len(yr)


def split(tr):
    if len(tr) == 0:
        return tr, tr
    et = pd.to_datetime(tr['entry_time'])
    return tr[et <= IS_END], tr[et > IS_END]


# ── 戦略×パラメータ定義 ──────────────────────────────────────
def strategies():
    out = []
    for dc in (20, 40, 55):
        out.append((f'DON_dc{dc}', lambda df, dc=dc: sig_donchian(df, dc)))
        out.append((f'DON200_dc{dc}', lambda df, dc=dc: sig_donchian_200(df, dc)))
    for fast, slow in ((20, 50), (50, 100), (50, 200)):
        out.append((f'EMAX_{fast}_{slow}', lambda df, f=fast, s=slow: sig_emax(df, f, s)))
    for lb in (100, 200, 400):
        out.append((f'TSMOM_{lb}', lambda df, lb=lb: sig_tsmom(df, lb)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sl-atr', type=float, default=2.0)
    ap.add_argument('--trail-atr', type=float, default=3.0)
    ap.add_argument('--max-hold-h', type=int, default=240)  # 10日(1h) / daily時は60本=約3ヶ月
    ap.add_argument('--tf', choices=['1h', 'daily'], default='1h')
    ap.add_argument('--out', default='trend_10y_bt_result.csv')
    args = ap.parse_args()
    if args.tf == 'daily' and args.max_hold_h == 240:
        args.max_hold_h = 60  # 日足では60本(約3ヶ月)

    rows = []
    for pair in PAIRS:
        df = load_1h(pair)
        if args.tf == 'daily':
            df = resample_daily(df)
        df['atr'] = atr_ewm(df)
        df = df.dropna(subset=['atr']).reset_index(drop=True)
        pip = pip_size(pair)
        print(f'\n=== {pair} ({len(df)} bars, {df.datetime.iloc[0].date()}..{df.datetime.iloc[-1].date()}) ===')
        for name, sigfn in strategies():
            sig = sigfn(df)
            tr = run_bt(df, pair, sig, args.sl_atr, args.trail_atr, args.max_hold_h)
            full = metrics(tr, pip)
            tis, toos = split(tr)
            mis, moos = metrics(tis, pip), metrics(toos, pip)
            pr, nyr = yearly_pos_rate(tr)
            passed = (mis['pf'] >= 1.2 and moos['pf'] >= 1.2 and
                      mis['n'] >= MIN_N and moos['n'] >= MIN_N)
            rows.append(dict(
                pair=pair, strat=name,
                full_pf=full['pf'], full_n=full['n'], full_net=full['net_pip'],
                full_wr=full['wr'], full_sharpe=full['sharpe'], full_maxdd_R=full['maxdd_R'],
                is_pf=mis['pf'], is_n=mis['n'], oos_pf=moos['pf'], oos_n=moos['n'],
                oos_net=moos['net_pip'], yr_pos_rate=pr, n_years=nyr,
                PASS='YES' if passed else '',
            ))
            print(f'  {name:14s} full PF={full["pf"]:>5} n={full["n"]:>4} '
                  f'net={full["net_pip"]:>8}pip Sh={full["sharpe"]:>5} | '
                  f'IS PF={mis["pf"]:>5}(n{mis["n"]}) OOS PF={moos["pf"]:>5}(n{moos["n"]}) '
                  f'yr+%={pr:>3} {"<<PASS" if passed else ""}')

    res = pd.DataFrame(rows)
    outpath = Path(__file__).parent / args.out
    res.to_csv(outpath, index=False)
    print(f'\nsaved -> {outpath}')
    npass = (res['PASS'] == 'YES').sum()
    print(f'\nPASS (IS&OOS PF>1.2, n>{MIN_N}): {npass} / {len(res)}')
    if npass:
        print(res[res['PASS'] == 'YES'].to_string(index=False))
    else:
        print('--- 採用基準クリアはゼロ。参考: full PF 上位10 ---')
        print(res.sort_values('full_pf', ascending=False).head(10).to_string(index=False))


if __name__ == '__main__':
    main()
