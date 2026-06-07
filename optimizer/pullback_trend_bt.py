"""
pullback_trend_bt.py - 案B: Donchian breakout trend-following backtest (GBPJPY).

  ※ 旧版(EURJPY pullback)は pullback_trend_bt_eurjpy_pullback.py.bak に退避済み。

戦略 (Grid戦略の補完 = トレンド順行レッグ / magic=20260050 SCANN_TAG='TREND_BO'):
  - ペア      : GBPJPY
  - 方向判定  : D1 Donchian 高値/安値ブレイク + ADX(14) フィルタ
      * Donchian上限 = 直近 dc_period 日(完了日のみ / shift(1)で当日除外)の D1 高値の最大
      * Donchian下限 = 同 安値の最小
  - エントリー: 4H足の終値が Donchian上限を上抜け -> 翌4H足の寄りで成行買い
                4H足の終値が Donchian下限を下抜け -> 翌4H足の寄りで成行売り
      * フィルタ: ADX(14, 4H足) > adx_th  (adx_th=None なら OFF)
      * MAX_POS=1 (同時1ポジ)
  - 出口      : ATRトレイル(係数 trail_mult × ATR(atr_period, H1足), 1H更新)。固定TPなし。
      * 初期SL    = entry ∓ trail_mult * ATR_entry  (= 1R)
      * トレイル  : 順行extreme を1H毎更新し stop = extreme ∓ trail_mult*ATR_now を片側更新
      * 約定      : bar安値/高値が stop到達で stop約定。寄りがstop超ギャップなら寄り約定(保守)
  - スプレッド: GBPJPY 2pips を往復コストとして控除
  - 会計      : R倍率ベース (risk=初期SL距離=1R)。Sharpe=日次R系列の年率(√252)。
                maxDD% は 1トレード=1%リスク前提の近似 (= maxDD_R)。

期間 (IS/OOS):
  - IS : 2024-04-01 .. 2025-06-30
  - OOS: 2025-07-01 .. 2026-05-31

探索グリッド: dc_period[10,15,20,30] x adx_th[None,20,25,30]
              x trail_mult[2.0,3.0,4.0] x atr_period[10,14,20]  = 144通り
              (エントリー足は 4H 固定)

補完評価(Grid DD上位5区間でのTrend損益): Grid(COMBINED / grid_floatstop_bt 設定)の
  DD episode 上位5区間を1度だけ算出し、各Trend構成のTrend日次R をその窓内で合算。
  trough日付で IS/OOS に振り分け、採用基準(IS>+5R / OOS>0R)に使用。

出力:
  - optimizer/pullback_trend_bt_result.csv  : グリッド x (IS/OOS) スコアカード
  - optimizer/pullback_trend_bt_trades.csv  : ベスト構成の全トレード台帳(補完分析用)
"""

import itertools
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# --- paths (VPS=Windows / ローカル=repo data 両対応) -------------------------
OUTPUT_DIR = Path(__file__).resolve().parent
_DATA_CANDIDATES = [
    os.environ.get('FX_DATA_DIR'),
    r'C:\Users\Administrator\fx_bot\data',
    str(OUTPUT_DIR.parent / 'data'),
]
DATA_DIR = next((d for d in _DATA_CANDIDATES if d and os.path.isdir(d)), _DATA_CANDIDATES[-1])

RESULT_CSV = str(OUTPUT_DIR / 'pullback_trend_bt_result.csv')
TRADES_CSV = str(OUTPUT_DIR / 'pullback_trend_bt_trades.csv')

# --- config -----------------------------------------------------------------
PAIR        = 'GBPJPY'
MAGIC       = 20260050        # 新規採番 (既存: ...40 news, ...30-34 grid)
PIP         = 0.01           # JPYクロス
SPREAD      = 2 * PIP        # 2pips 往復控除
MIN_TRADES  = 15            # セグメント当たり最低トレード数
ANN         = 252           # Sharpe年率化日数

IS_START,  IS_END  = '2024-04-01', '2025-06-30 23:59'
OOS_START, OOS_END = '2025-07-01', '2026-05-31 23:59'

GRID = {
    'dc_period':   [10, 15, 20, 30],
    'adx_th':      [None, 20, 25, 30],
    'trail_mult':  [2.0, 3.0, 4.0],
    'atr_period':  [10, 14, 20],
}


# --- data loading -----------------------------------------------------------
def _coalesce_ohlc(df):
    """大小文字2系統(連結データ)を1系統にまとめる。"""
    out = {}
    for col in ['open', 'high', 'low', 'close', 'volume']:
        cands = [c for c in df.columns if c.lower() == col]
        if not cands:
            continue
        s = df[cands[0]]
        for c in cands[1:]:
            s = s.fillna(df[c])
        out[col] = s
    return pd.DataFrame(out, index=df.index)


def load_h1(pair):
    path = os.path.join(DATA_DIR, f'{pair}_1h.csv')
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df = _coalesce_ohlc(df)
    df = df.dropna(subset=['open', 'high', 'low', 'close']).sort_index()
    df = df[~df.index.duplicated(keep='last')]
    return df


def load_d1(pair):
    path = os.path.join(DATA_DIR, f'{pair}_D1.csv')
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()


def resample_h4(h1):
    """H1 -> 4H足 (UTC 00/04/08/12/16/20 起点)。"""
    o = h1['open'].resample('4h', label='left', closed='left').first()
    h = h1['high'].resample('4h', label='left', closed='left').max()
    l = h1['low'].resample('4h', label='left', closed='left').min()
    c = h1['close'].resample('4h', label='left', closed='left').last()
    h4 = pd.DataFrame({'open': o, 'high': h, 'low': l, 'close': c}).dropna()
    return h4


# --- indicators -------------------------------------------------------------
def calc_atr(df, period):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calc_adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calc_max_dd(equity):
    eq = np.asarray(equity, dtype=float)
    if eq.size == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float((peak - eq).max())


# --- feature prep -----------------------------------------------------------
def prepare(pair):
    """H1足ベースの統合フレームを返す。
    各H1バーに以下を付与:
      - is_h4_close : この H1 が 4H足の最終足か (hour in {3,7,11,15,19,23})
      - h4_close    : 直近確定 4H足の終値 (=境界H1のclose)
      - adx4        : 直近確定 4H足の ADX14
      - donch_up_{p}/donch_lo_{p} : dc_period 別 D1 Donchian (当日有効値)
      - atr_{p}     : atr_period 別 H1 ATR
    """
    h1 = load_h1(pair)
    h4 = resample_h4(h1)
    h4['adx'] = calc_adx(h4, 14)

    # 4H確定値を H1 の境界バーへマップ (4H足のlabel=開始時刻 -> 終了時刻はlabel+3h)
    h4_close_time = h4.index + pd.Timedelta(hours=3)
    h4_map = pd.DataFrame({'h4_close': h4['close'].values, 'adx4': h4['adx'].values},
                          index=h4_close_time)
    h1 = h1.join(h4_map)
    h1['is_h4_close'] = h1.index.isin(h4_close_time)

    # D1 Donchian (完了日のみ / shift(1)で当日除外) を dc_period 別に作成し日付でマップ
    d1 = load_d1(pair)
    day_key = h1.index.normalize()
    for p in GRID['dc_period']:
        up = d1['high'].rolling(p).max().shift(1)
        lo = d1['low'].rolling(p).min().shift(1)
        up.index = up.index.normalize()
        lo.index = lo.index.normalize()
        h1[f'donch_up_{p}'] = day_key.map(up)
        h1[f'donch_lo_{p}'] = day_key.map(lo)

    for p in GRID['atr_period']:
        h1[f'atr_{p}'] = calc_atr(h1, p)

    return h1.reset_index(names='time')


# --- backtest ---------------------------------------------------------------
def run_backtest(df, dc_period, adx_th, trail_mult, atr_period):
    """全H1バーをbar順にシミュレートし、トレード台帳(DataFrame)を返す。"""
    opn = df['open'].values
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    atr = df[f'atr_{atr_period}'].values
    adx4 = df['adx4'].values
    is_h4_close = df['is_h4_close'].values
    donch_up = df[f'donch_up_{dc_period}'].values
    donch_lo = df[f'donch_lo_{dc_period}'].values
    time = df['time'].values

    trades = []
    pos = None
    pending = None  # +1 / -1 : 次バー寄りでエントリー予約
    N = len(df)

    for i in range(N):
        # 1) 予約エントリーを当バー寄りで約定
        just_entered = False
        if pending is not None and pos is None:
            atr_e = atr[i]
            if atr_e > 0 and not np.isnan(atr_e):
                side = pending
                entry = opn[i] + (SPREAD / 2) * side   # buy=ask / sell=bid
                risk = trail_mult * atr_e
                stop0 = entry - risk if side == 1 else entry + risk
                pos = {'side': side, 'entry': entry, 'entry_time': time[i],
                       'stop': stop0, 'risk': risk, 'ext': entry, 'bars': 0}
                just_entered = True
            pending = None

        # 2) 出口管理 (エントリー足はスキップ / 既存stopで先に判定 -> その後trail更新)
        if pos is not None and not just_entered:
            pos['bars'] += 1
            exit_price = None
            if pos['side'] == 1:
                if opn[i] <= pos['stop']:
                    exit_price = opn[i]            # 寄りギャップ割れ
                elif low[i] <= pos['stop']:
                    exit_price = pos['stop']
            else:
                if opn[i] >= pos['stop']:
                    exit_price = opn[i]
                elif high[i] >= pos['stop']:
                    exit_price = pos['stop']

            if exit_price is not None:
                gross = (exit_price - pos['entry']) * pos['side']
                net = gross - SPREAD
                trades.append({
                    'entry_time': pos['entry_time'], 'exit_time': time[i],
                    'side': pos['side'], 'entry': pos['entry'], 'exit': exit_price,
                    'risk': pos['risk'], 'pnl_r': net / pos['risk'], 'bars': pos['bars'],
                })
                pos = None
            else:
                # トレイル更新 (1H毎・片側)
                if pos['side'] == 1:
                    pos['ext'] = max(pos['ext'], high[i])
                    pos['stop'] = max(pos['stop'], pos['ext'] - trail_mult * atr[i])
                else:
                    pos['ext'] = min(pos['ext'], low[i])
                    pos['stop'] = min(pos['stop'], pos['ext'] + trail_mult * atr[i])

        # 3) 新規シグナル (4H足確定時 / ポジ・予約なし)
        if pos is None and pending is None and is_h4_close[i]:
            up, lo, adxv = donch_up[i], donch_lo[i], adx4[i]
            if np.isnan(up) or np.isnan(lo):
                continue
            adx_ok = (adx_th is None) or (not np.isnan(adxv) and adxv > adx_th)
            if not adx_ok:
                continue
            if close[i] > up:
                pending = 1
            elif close[i] < lo:
                pending = -1

    return pd.DataFrame(trades)


# --- metrics ----------------------------------------------------------------
def _daily_R(tr):
    """決済日でまとめた日次R系列(セグメント連続日0埋め)。"""
    if len(tr) == 0:
        return pd.Series(dtype=float)
    s = tr.copy()
    s['date'] = pd.to_datetime(s['exit_time']).dt.normalize()
    d = s.groupby('date')['pnl_r'].sum()
    cal = pd.date_range(d.index.min(), d.index.max(), freq='D')
    return d.reindex(cal).fillna(0.0)


def seg_metrics(tr):
    if len(tr) == 0:
        return dict(N=0, WR=np.nan, PF=np.nan, net_R=0.0, avg_R=np.nan,
                    Sharpe=np.nan, maxDD_pct=np.nan)
    r = tr['pnl_r'].values
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()
    pf = wins / losses if losses > 0 else np.inf
    dd = calc_max_dd(np.cumsum(r))
    daily = _daily_R(tr)
    sd = daily.std(ddof=1)
    sharpe = (daily.mean() / sd * np.sqrt(ANN)) if sd > 0 else np.nan
    return dict(
        N=len(tr),
        WR=round(100 * (r > 0).mean(), 1),
        PF=round(pf, 3),
        net_R=round(r.sum(), 2),
        avg_R=round(r.mean(), 3),
        Sharpe=round(sharpe, 3) if np.isfinite(sharpe) else np.nan,
        maxDD_pct=round(dd, 2),   # 1トレード=1%リスク前提
    )


def split_seg(tr, start, end):
    if len(tr) == 0:
        return tr
    et = pd.to_datetime(tr['entry_time'])
    return tr[(et >= pd.Timestamp(start)) & (et <= pd.Timestamp(end))]


# --- Grid DD窓 (補完評価) ----------------------------------------------------
def load_grid_dd_windows(k=5):
    """grid_floatstop_bt 設定で Grid(COMBINED) 日次PnL -> DD episode 上位k窓。
    戻り: list[(peak_date, trough_date)] / 失敗時 None。"""
    try:
        import grid_floatstop_bt as G
        from pullback_grid_complement import grid_pnl_events, top_dd_windows
    except Exception as e:
        print(f'[WARN] Grid DD窓 算出スキップ: {e}')
        return None
    pairs = ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD']
    per_pair = {}
    for pair in pairs:
        try:
            df = G.load_data(pair)
        except Exception:
            continue
        ev = grid_pnl_events(pair, G.PAIR_CONFIG[pair], df,
                             G.compute_atr_series(df), G.compute_ci_series(df))
        if not ev:
            continue
        s = pd.Series([v for _, v in ev], index=[t for t, _ in ev])
        s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
        per_pair[pair] = s.groupby(level=0).sum()
    if not per_pair:
        return None
    combined = pd.DataFrame(per_pair).fillna(0.0).sum(axis=1)
    eps = top_dd_windows(combined, k=k)
    return [(p, tr) for (p, tr, _end, _depth) in eps]


def dd_window_trend_R(tr, windows):
    """Trend台帳 tr の日次R を Grid DD各窓で合算し IS/OOS に振り分け。"""
    if not windows or len(tr) == 0:
        return 0.0, 0.0
    daily = _daily_R(tr)
    is_sum = oos_sum = 0.0
    is_end = pd.Timestamp(IS_END)
    for peak, trough in windows:
        wsum = daily.loc[(daily.index >= peak) & (daily.index <= trough)].sum()
        if trough <= is_end:
            is_sum += wsum
        else:
            oos_sum += wsum
    return round(float(is_sum), 2), round(float(oos_sum), 2)


# --- main -------------------------------------------------------------------
def main():
    print(f'DATA_DIR = {DATA_DIR}  |  PAIR={PAIR}  magic={MAGIC}')
    df = prepare(PAIR)
    print(f'{PAIR} H1 bars: {len(df)}  ({df["time"].min()} .. {df["time"].max()})')

    dd_windows = load_grid_dd_windows(k=5)
    if dd_windows:
        print('Grid(COMBINED) DD上位5区間:')
        for j, (p, t) in enumerate(dd_windows, 1):
            seg = 'IS' if t <= pd.Timestamp(IS_END) else 'OOS'
            print(f'  #{j} {p.date()} -> {t.date()}  [{seg}]')

    rows = []
    best = None  # (is_pf, params, trades)
    for dc, adx_th, trail_mult, atr_p in itertools.product(
            GRID['dc_period'], GRID['adx_th'], GRID['trail_mult'], GRID['atr_period']):
        tr = run_backtest(df, dc, adx_th, trail_mult, atr_p)
        is_tr = split_seg(tr, IS_START, IS_END)
        oos_tr = split_seg(tr, OOS_START, OOS_END)
        m_is = seg_metrics(is_tr)
        m_oos = seg_metrics(oos_tr)
        is_dd, oos_dd = dd_window_trend_R(tr, dd_windows)
        adx_lbl = 'OFF' if adx_th is None else adx_th
        for seg, m, ddR in [('IS', m_is, is_dd), ('OOS', m_oos, oos_dd)]:
            rows.append({'dc': dc, 'adx_th': adx_lbl, 'trail': trail_mult,
                         'atr_p': atr_p, 'seg': seg, **m, 'ddwin_R': ddR})

        if m_is['N'] >= MIN_TRADES and np.isfinite(m_is['PF']):
            key = m_is['PF']
            if best is None or key > best[0]:
                best = (key, dict(dc=dc, adx_th=adx_th, trail_mult=trail_mult, atr_p=atr_p), tr)

    res = pd.DataFrame(rows)
    res.to_csv(RESULT_CSV, index=False)
    print(f'\n=== grid scorecard -> {RESULT_CSV} ===')
    piv = res.pivot_table(index=['dc', 'adx_th', 'trail', 'atr_p'], columns='seg',
                          values=['N', 'WR', 'PF', 'Sharpe', 'maxDD_pct', 'ddwin_R'])
    with pd.option_context('display.width', 240, 'display.max_columns', 60, 'display.max_rows', 300):
        print(piv.to_string())

    if best is not None:
        bp, btr = best[1], best[2]
        btr_full = btr.copy()
        btr_full['entry_time'] = pd.to_datetime(btr_full['entry_time'])
        btr_full = btr_full.sort_values('entry_time')
        btr_full['cum_R'] = btr_full['pnl_r'].cumsum()
        btr_full['seg'] = np.where(
            btr_full['entry_time'] <= pd.Timestamp(IS_END), 'IS',
            np.where(btr_full['entry_time'] >= pd.Timestamp(OOS_START), 'OOS', 'GAP'))
        btr_full.to_csv(TRADES_CSV, index=False)
        print(f'\n=== BEST (IS PF) params: {bp} ===')
        print(f'  IS : {seg_metrics(split_seg(btr, IS_START, IS_END))}')
        print(f'  OOS: {seg_metrics(split_seg(btr, OOS_START, OOS_END))}')
        print(f'  trades ledger -> {TRADES_CSV}')
    else:
        print('\n[!] IS で MIN_TRADES を満たす構成なし')


if __name__ == '__main__':
    main()
