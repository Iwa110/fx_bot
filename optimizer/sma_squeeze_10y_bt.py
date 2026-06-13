"""
sma_squeeze_10y_bt.py - SMA Squeeze Play 10-year IS/OOS backtest (live v4.5/v4.6 faithful).

Data: Dukascopy 1h, 11yr (2015-06 .. 2026-06), resampled to the strategy timeframe.
      data/<PAIR>_1h_dukas.csv

Fidelity to vps/sma_squeeze.py v4.5/v4.6:
  - entry timeframe per pair (USDJPY/EURUSD = 4h), indicators on that TF
  - entry: ADX14>20, squeeze_th gate (|SMAs-SMAl|/SMAl*100), SMA_long slope monotonic
           (rising/falling over slope_period), SMA cross confirm (prev_c vs prev_s),
           bullish/bearish bar (c vs o), price vs SMA_long
  - daily SMA slope filter (daily_sma / daily_slope_period): block 1h-UP vs daily-DN, etc.
  - entry fill: NEXT bar open + half-spread (live market order next cycle)
  - exit priority per bar (intrabar): SL (low/high) -> TP (low/high)
                                      -> SMA_long break (close) -> slope_exit=N (close)
                                      -> T_max=24h force-close (close)
  - NO trailing (v4.5 atr_trail_mult=0.0)
  - cooldown 180min from settle time (v4.6 re-arm after force-close)

IS = 2015-06 .. 2022-12 (7.5yr) / OOS = 2023-01 .. 2026-06 (3.5yr)
Judgement: IS & OOS both PF>1.2, n>=30, WR>50%, DD reasonable.

Output: optimizer/sma_squeeze_10y_bt_result.csv (per-pair per-window),
        optimizer/sma_squeeze_10y_bt_yearly.csv (per-year),
        optimizer/sma_squeeze_10y_bt_trades.csv (all trades for active pairs).
"""

import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

DATA_DIR   = Path(__file__).parent.parent / 'data'
OUTPUT_DIR = Path(__file__).parent

SPREAD = {'USDJPY': 0.02, 'GBPJPY': 0.03, 'EURUSD': 0.00012, 'GBPUSD': 0.00020, 'EURJPY': 0.03}

# Live v4.5/v4.6 PAIRS_CFG (from vps/sma_squeeze.py)
PAIRS_CFG = {
    'USDJPY': {'sma_short': 25, 'sma_long': 150, 'squeeze_th': 1.5,
               'slope_period': 5,  'rr': 2.5, 'sl_atr_mult': 1.5,
               'timeframe': '4h', 'slope_exit': 3,
               'daily_sma': 20, 'daily_slope_period': 3, 'tmax_hours': 24, 'enabled': True},
    'EURUSD': {'sma_short': 25, 'sma_long': 200, 'squeeze_th': 2.0,
               'slope_period': 10, 'rr': 2.5, 'sl_atr_mult': 1.0,
               'timeframe': '4h', 'slope_exit': 3,
               'daily_sma': 50, 'daily_slope_period': 3, 'tmax_hours': 24, 'enabled': True},
    # disabled in live -- included to confirm the decision over 10yr
    'GBPJPY': {'sma_short': 25, 'sma_long': 250, 'squeeze_th': 0.5,
               'slope_period': 10, 'rr': 2.0, 'sl_atr_mult': 1.5,
               'timeframe': '1h', 'slope_exit': 3,
               'daily_sma': 20, 'daily_slope_period': 3, 'tmax_hours': 24, 'enabled': False},
}

IS_END   = pd.Timestamp('2023-01-01')
COOLDOWN = pd.Timedelta(minutes=180)


# ── data ────────────────────────────────────────────────────────────────
def load_resampled(pair, tf):
    """Load 1h dukas, return (df_tf, df_daily) both datetime-indexed OHLC."""
    path = DATA_DIR / f'{pair}_1h_dukas.csv'
    if not path.exists():
        return None, None
    df = pd.read_csv(path, parse_dates=['datetime']).set_index('datetime')
    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close']].dropna().sort_index()
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if tf == '1h':
        df_tf = df
    else:
        rule = {'4h': '4h'}[tf]
        df_tf = df.resample(rule, closed='left', label='left').agg(agg).dropna()
    df_daily = df.resample('1D').agg(agg).dropna()
    return df_tf, df_daily


# ── indicators ──────────────────────────────────────────────────────────
def calc_atr14(df):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def calc_adx14(df):
    high, low, close = df['high'], df['low'], df['close']
    pdm = high.diff(); ndm = low.diff().mul(-1)
    pdm = pdm.where((pdm > ndm) & (pdm > 0), 0.0)
    ndm = ndm.where((ndm > pdm) & (ndm > 0), 0.0)
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    ndi = 100 * ndm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()


def slope_state(arr_slice):
    """True=strictly rising, False=strictly falling, None=indeterminate."""
    if np.any(np.isnan(arr_slice)):
        return None
    diffs = np.diff(arr_slice)
    if np.all(diffs > 0):
        return True
    if np.all(diffs < 0):
        return False
    return None


# ── daily filter precompute: map each tf-bar time -> daily slope state ────
def build_daily_slope_map(df_daily, daily_sma, daily_sp):
    sma_d = df_daily['close'].rolling(daily_sma).mean().values
    times = df_daily.index
    states = []
    for i in range(len(df_daily)):
        s = i - daily_sp + 1
        states.append(slope_state(sma_d[s:i+1]) if s >= 0 else None)
    # daily bar at date D is only *complete* after D ends; for a tf-bar at time t,
    # use the most recent daily bar with date < t.date() (no look-ahead).
    return pd.Series(states, index=times.normalize())


# ── core backtest ─────────────────────────────────────────────────────────
def run_bt(df, df_daily, cfg, spread):
    sma_short, sma_long = cfg['sma_short'], cfg['sma_long']
    squeeze_th, slope_period = cfg['squeeze_th'], cfg['slope_period']
    rr, sl_atr_mult = cfg['rr'], cfg['sl_atr_mult']
    slope_exit_bars = cfg['slope_exit']
    tmax = pd.Timedelta(hours=cfg['tmax_hours']) if cfg.get('tmax_hours') else None

    close_a = df['close'].values
    open_a  = df['open'].values
    high_a  = df['high'].values
    low_a   = df['low'].values
    times   = df.index
    sma_s   = df['close'].rolling(sma_short).mean().values
    sma_l   = df['close'].rolling(sma_long).mean().values
    atr14   = calc_atr14(df).values
    adx14   = calc_adx14(df).values
    n       = len(df)
    half    = spread / 2.0

    daily_map = None
    if cfg.get('daily_sma'):
        daily_map = build_daily_slope_map(df_daily, cfg['daily_sma'], cfg['daily_slope_period'])
        d_dates = daily_map.index  # normalized daily dates

    warmup = max(sma_long, slope_period, 28) + 2
    trades = []
    in_trade = False
    t_dir = ''; t_entry = 0.0; t_sl_dist = 0.0; t_tp_dist = 0.0
    t_sl_lvl = 0.0; t_entry_time = None
    last_exit_time = None

    def daily_state_for(t):
        if daily_map is None:
            return None
        # most recent daily bar strictly before this tf-bar's date
        cutoff = t.normalize()
        idx = d_dates.searchsorted(cutoff) - 1
        if idx < 0:
            return None
        return daily_map.iloc[idx]

    for i in range(warmup, n):
        c, o, hi, lo = close_a[i], open_a[i], high_a[i], low_a[i]
        sl_v, ss_v, atr_v, adx_v = sma_l[i], sma_s[i], atr14[i], adx14[i]
        t = times[i]
        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            continue

        if in_trade:
            is_long = (t_dir == 'long')
            pnl = None; reason = ''
            if is_long and lo <= t_sl_lvl:
                pnl = (t_sl_lvl - t_entry) - half; reason = 'SL'
            elif (not is_long) and hi >= t_sl_lvl:
                pnl = (t_entry - t_sl_lvl) - half; reason = 'SL'
            if pnl is None:
                if is_long and hi >= t_entry + t_tp_dist:
                    pnl = t_tp_dist - half; reason = 'TP'
                elif (not is_long) and lo <= t_entry - t_tp_dist:
                    pnl = t_tp_dist - half; reason = 'TP'
            if pnl is None and ((is_long and c < sl_v) or (not is_long and c > sl_v)):
                pnl = ((c - t_entry) if is_long else (t_entry - c)) - half; reason = 'SMAbreak'
            if pnl is None:
                s = i - slope_exit_bars + 1
                if s >= 0:
                    seg = sma_l[s:i+1]
                    if not np.any(np.isnan(seg)):
                        d = np.diff(seg)
                        if (is_long and np.all(d < 0)) or ((not is_long) and np.all(d > 0)):
                            pnl = ((c - t_entry) if is_long else (t_entry - c)) - half; reason = 'slope_exit'
            if pnl is None and tmax is not None and (t - t_entry_time) >= tmax:
                pnl = ((c - t_entry) if is_long else (t_entry - c)) - half; reason = 'Tmax'

            if pnl is not None:
                trades.append({'time': t, 'dir': t_dir, 'pnl': pnl, 'reason': reason,
                               'entry': t_entry, 'hold_h': (t - t_entry_time).total_seconds()/3600})
                in_trade = False
                last_exit_time = t
            else:
                continue

        # entry
        if last_exit_time is not None and (t - last_exit_time) < COOLDOWN:
            continue
        if adx_v <= 20.0 or sl_v == 0.0:
            continue
        div_rate = abs(ss_v - sl_v) / sl_v * 100.0
        if div_rate > squeeze_th:
            continue
        s = i - slope_period + 1
        if s < 0:
            continue
        slp = slope_state(sma_l[s:i+1])
        if slp is None:
            continue
        # daily filter
        ds = daily_state_for(t)
        if ds is not None:
            if slp is True and ds is False:
                continue
            if slp is False and ds is True:
                continue
        prev_c, prev_s = close_a[i-1], sma_s[i-1]
        if np.isnan(prev_s) or np.isnan(prev_c):
            continue
        direction = None
        if slp is True and c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            direction = 'long'
        elif slp is False and c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            direction = 'short'
        if direction is None or i + 1 >= n:
            continue
        is_long_new = (direction == 'long')
        sl_dist = atr_v * sl_atr_mult
        entry_px = open_a[i+1] + half * (1 if is_long_new else -1)
        t_dir = direction; t_entry = entry_px
        t_sl_dist = sl_dist; t_tp_dist = sl_dist * rr
        t_sl_lvl = (entry_px - sl_dist) if is_long_new else (entry_px + sl_dist)
        t_entry_time = times[i+1]
        in_trade = True

    return pd.DataFrame(trades)


def metrics(tr):
    if len(tr) == 0:
        return None
    pnl = tr['pnl'].values
    wins = pnl[pnl > 0]; loss = pnl[pnl <= 0]
    gp = wins.sum(); gl = abs(loss.sum())
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    eq = np.concatenate([[0.0], np.cumsum(pnl)])
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min())
    return {'PF': round(pf, 3), 'WR': round(len(wins)/len(pnl), 3), 'n': len(pnl),
            'net': round(float(pnl.sum()), 5), 'maxDD': round(-dd, 5),
            'avg_win': round(float(wins.mean()), 6) if len(wins) else 0.0,
            'avg_loss': round(float(abs(loss.mean())), 6) if len(loss) else 0.0}


def main():
    rows = []; yearly_rows = []; all_trades = []
    for pair, cfg in PAIRS_CFG.items():
        tf = cfg['timeframe']
        df, dd = load_resampled(pair, tf)
        if df is None:
            print(f'[SKIP] {pair}: no dukas data'); continue
        sp = SPREAD[pair]
        tr = run_bt(df, dd, cfg, sp)
        tr['pair'] = pair
        all_trades.append(tr)
        full = metrics(tr)
        is_tr = tr[tr['time'] < IS_END]
        oos_tr = tr[tr['time'] >= IS_END]
        m_is = metrics(is_tr); m_oos = metrics(oos_tr)
        print(f"\n=== {pair} ({tf}) {'ENABLED' if cfg['enabled'] else 'disabled'} | "
              f"rows={len(df)} {df.index[0].date()}..{df.index[-1].date()} ===")
        for label, m in [('FULL', full), ('IS', m_is), ('OOS', m_oos)]:
            if m:
                print(f"  {label:4} PF={m['PF']:.3f} WR={m['WR']:.1%} n={m['n']:4} "
                      f"net={m['net']:>10.2f} maxDD={m['maxDD']:>9.2f}")
                rows.append({'pair': pair, 'window': label, **m})
        # reason breakdown
        if len(tr):
            rc = tr['reason'].value_counts().to_dict()
            print(f"  exits: {rc}")
        # yearly
        for yr, g in tr.groupby(tr['time'].dt.year):
            ym = metrics(g)
            if ym:
                yearly_rows.append({'pair': pair, 'year': int(yr), **ym})

    pd.DataFrame(rows).to_csv(OUTPUT_DIR / 'sma_squeeze_10y_bt_result.csv', index=False)
    pd.DataFrame(yearly_rows).to_csv(OUTPUT_DIR / 'sma_squeeze_10y_bt_yearly.csv', index=False)
    if all_trades:
        pd.concat(all_trades).to_csv(OUTPUT_DIR / 'sma_squeeze_10y_bt_trades.csv', index=False)
    print('\n[INFO] saved result/yearly/trades CSVs')

    # yearly profitability summary
    yd = pd.DataFrame(yearly_rows)
    if len(yd):
        print('\n=== yearly net by pair (sign) ===')
        for pair in PAIRS_CFG:
            g = yd[yd['pair'] == pair].sort_values('year')
            if len(g):
                pos = (g['net'] > 0).sum()
                print(f"  {pair}: {pos}/{len(g)} yrs positive | "
                      + ' '.join(f"{r.year}:{'+' if r.net>0 else '-'}" for r in g.itertuples()))


if __name__ == '__main__':
    main()
