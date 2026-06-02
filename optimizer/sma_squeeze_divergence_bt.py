"""
sma_squeeze_divergence_bt.py - Diagnose BT vs live divergence for SMA_SQ.

Compares the current BT entry model against fixes:
  - Task 1: next-bar OPEN entry (live executes at next bar open, not signal-bar close)
  - Task 2: squeeze "release" trigger vs current "in-squeeze" gate
  - Task 3: spread cost applied at entry/exit

Runs with the LIVE PAIRS_CFG params (the params actually trading), so the
numbers are directly comparable to live results.

Usage: /opt/homebrew/bin/python3 optimizer/sma_squeeze_divergence_bt.py
"""

import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# Local data dir (repo). On VPS this is C:\Users\Administrator\fx_bot\data
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# Live params from vps/sma_squeeze.py PAIRS_CFG (the params actually trading)
LIVE_CFG = {
    'USDJPY': dict(sma_short=25, sma_long=150, squeeze_th=1.5, slope_period=5,
                   rr=2.5, sl_atr_mult=1.5, timeframe='4h'),
    'GBPJPY': dict(sma_short=25, sma_long=250, squeeze_th=0.5, slope_period=10,
                   rr=2.0, sl_atr_mult=1.5, timeframe='1h'),
    'EURUSD': dict(sma_short=25, sma_long=200, squeeze_th=2.0, slope_period=10,
                   rr=2.5, sl_atr_mult=1.0, timeframe='4h'),
}

# Typical spread (price units). JPY pairs ~ 0.02 yen, USD pairs ~ 0.0003.
SPREAD = {'USDJPY': 0.02, 'GBPJPY': 0.03, 'EURUSD': 0.00012, 'GBPUSD': 0.00020, 'EURJPY': 0.03}


def load_csv(pair, tf):
    path = os.path.join(DATA_DIR, f'{pair}_1h.csv')
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    df = df[['open', 'high', 'low', 'close']].dropna(subset=['close']).sort_index()
    df = df.reset_index(drop=True)
    if tf == '4h':
        # resample by fixed 4-bar blocks to mirror live resample('4h')
        idx = df.index // 4
        df = df.groupby(idx).agg({'open': 'first', 'high': 'max',
                                  'low': 'min', 'close': 'last'}).reset_index(drop=True)
    return df


def calc_atr14(df):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def calc_adx14(df):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()


def run_bt(df, cfg, entry_mode='close', squeeze_mode='in_squeeze', spread=0.0):
    """
    entry_mode:   'close'    -> enter at signal-bar close (current BT)
                  'next_open'-> enter at next bar open (matches live execution)
    squeeze_mode: 'in_squeeze' -> div_rate <= th on signal bar (current BT/live)
                  'release'    -> prev div_rate <= th AND current div_rate > th
    spread:       price units subtracted as cost (half-spread on each of entry+exit)
    """
    sma_s = df['close'].rolling(cfg['sma_short']).mean().values
    sma_l = df['close'].rolling(cfg['sma_long']).mean().values
    atr14 = calc_atr14(df).values
    adx14 = calc_adx14(df).values
    close_a = df['close'].values
    open_a = df['open'].values
    n = len(df)

    slope_period = cfg['slope_period']
    squeeze_th = cfg['squeeze_th']
    sl_atr_mult = cfg['sl_atr_mult']
    rr = cfg['rr']

    warmup = max(cfg['sma_long'], slope_period, 28) + 2
    trades = []
    equity = [0.0]
    in_trade = False
    t_dir = t_entry = t_sl = t_tp = 0.0
    half = spread / 2.0

    for i in range(warmup, n):
        c = close_a[i]
        o = open_a[i]
        sl_v = sma_l[i]
        ss_v = sma_s[i]
        atr_v = atr14[i]
        adx_v = adx14[i]
        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            continue

        if in_trade:
            force = (t_dir == 'long' and c < sl_v) or (t_dir == 'short' and c > sl_v)
            if force:
                pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
                pnl -= half  # exit cost
                trades.append(pnl)
                equity.append(equity[-1] + pnl)
                in_trade = False
            elif t_dir == 'long':
                if c <= t_entry - t_sl:
                    trades.append(-t_sl - half)
                    equity.append(equity[-1] - t_sl - half)
                    in_trade = False
                elif c >= t_entry + t_tp:
                    trades.append(t_tp - half)
                    equity.append(equity[-1] + t_tp - half)
                    in_trade = False
            else:
                if c >= t_entry + t_sl:
                    trades.append(-t_sl - half)
                    equity.append(equity[-1] - t_sl - half)
                    in_trade = False
                elif c <= t_entry - t_tp:
                    trades.append(t_tp - half)
                    equity.append(equity[-1] + t_tp - half)
                    in_trade = False

        if in_trade:
            continue

        if adx_v <= 20.0 or sl_v == 0.0:
            continue

        div_rate = abs(ss_v - sl_v) / sl_v * 100.0
        if squeeze_mode == 'in_squeeze':
            if div_rate > squeeze_th:
                continue
        else:  # release: was squeezed last bar, expanding now
            prev_ss = sma_s[i - 1]
            prev_sl = sma_l[i - 1]
            if np.isnan(prev_ss) or np.isnan(prev_sl):
                continue
            prev_div = abs(prev_ss - prev_sl) / prev_sl * 100.0
            if not (prev_div <= squeeze_th and div_rate > squeeze_th):
                continue

        slp_start = i - slope_period + 1
        if slp_start < 0:
            continue
        slp = sma_l[slp_start: i + 1]
        if np.any(np.isnan(slp)):
            continue
        diffs = np.diff(slp)
        rising = bool(np.all(diffs > 0))
        falling = bool(np.all(diffs < 0))
        if not (rising or falling):
            continue

        prev_c = close_a[i - 1]
        prev_s = sma_s[i - 1]
        if np.isnan(prev_s) or np.isnan(prev_c):
            continue

        sl_dist = atr_v * sl_atr_mult
        tp_dist = sl_dist * rr
        if sl_dist == 0.0:
            continue

        sig_long = rising and c > sl_v and prev_c < prev_s and c > ss_v and c > o
        sig_short = falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o
        if not (sig_long or sig_short):
            continue

        # Determine entry price
        if entry_mode == 'close':
            entry_px = c
        else:  # next_open
            if i + 1 >= n:
                continue
            entry_px = open_a[i + 1]
        entry_px += half * (1 if sig_long else -1)  # entry cost (pay the spread)

        if sig_long:
            t_dir, t_entry, t_sl, t_tp = 'long', entry_px, sl_dist, tp_dist
        else:
            t_dir, t_entry, t_sl, t_tp = 'short', entry_px, sl_dist, tp_dist
        in_trade = True

    if in_trade:
        c = close_a[-1]
        pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
        trades.append(pnl - half)
        equity.append(equity[-1] + pnl - half)

    arr = np.array(trades)
    if len(arr) == 0:
        return dict(PF=0.0, WR=0.0, n=0, net=0.0)
    wins = arr[arr > 0]
    loss = arr[arr <= 0]
    gp = float(wins.sum())
    gl = float(abs(loss.sum()))
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    eq = np.array(equity)
    dd = float((eq - np.maximum.accumulate(eq)).min())
    return dict(PF=round(pf, 3), WR=round(len(wins) / len(arr), 3),
                n=len(arr), net=round(float(arr.sum()), 4), max_dd=round(-dd, 4))


def run_bt_live_exits(df, cfg, spread=0.0, atr_trail_mult=0.5, slope_exit=3, tmax_bars=None):
    """
    Mirror the LIVE exit stack that the plain BT omits:
      - initial SL = sl_atr_mult*ATR, TP = SL*rr
      - force-close on SMA_long break (bar close)
      - slope_exit: close if SMA_long slope over last `slope_exit` bars reverses
      - ATR trailing stop = atr_trail_mult*ATR, ratchets each bar, intrabar hit via bar low/high
    Entry: next bar open (live execution). This is the realistic-exit BT.
    """
    sma_s = df['close'].rolling(cfg['sma_short']).mean().values
    sma_l = df['close'].rolling(cfg['sma_long']).mean().values
    atr14 = calc_atr14(df).values
    adx14 = calc_adx14(df).values
    close_a, open_a = df['close'].values, df['open'].values
    high_a, low_a = df['high'].values, df['low'].values
    n = len(df)
    sp = cfg['slope_period']; sq_th = cfg['squeeze_th']
    sl_mult = cfg['sl_atr_mult']; rr = cfg['rr']
    warmup = max(cfg['sma_long'], sp, 28) + 2
    trades, equity = [], [0.0]
    in_trade = False
    t_dir = t_entry = t_sl_px = t_tp_px = 0.0
    bars_held = 0
    half = spread / 2.0

    def slope_rev(i, k, is_long):
        seg = sma_l[i - k + 1:i + 1]
        if np.any(np.isnan(seg)):
            return False
        d = np.diff(seg)
        if is_long:
            return bool(np.all(d < 0))
        return bool(np.all(d > 0))

    for i in range(warmup, n):
        c, o = close_a[i], open_a[i]
        sl_v, ss_v = sma_l[i], sma_s[i]
        atr_v, adx_v = atr14[i], adx14[i]
        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            continue

        if in_trade:
            bars_held += 1
            exited = False
            # 1) check existing SL/trail against this bar's range FIRST
            if t_dir == 'long' and low_a[i] <= t_sl_px:
                pnl = (t_sl_px - t_entry) - half; exited = True
            elif t_dir == 'short' and high_a[i] >= t_sl_px:
                pnl = (t_entry - t_sl_px) - half; exited = True
            # TP hit
            elif t_dir == 'long' and high_a[i] >= t_tp_px:
                pnl = (t_tp_px - t_entry) - half; exited = True
            elif t_dir == 'short' and low_a[i] <= t_tp_px:
                pnl = (t_entry - t_tp_px) - half; exited = True
            # force-close on SMA_long break (bar close)
            elif (t_dir == 'long' and c < sl_v) or (t_dir == 'short' and c > sl_v):
                pnl = ((c - t_entry) if t_dir == 'long' else (t_entry - c)) - half; exited = True
            # slope-exit reversal
            elif slope_exit and slope_rev(i, slope_exit, t_dir == 'long'):
                pnl = ((c - t_entry) if t_dir == 'long' else (t_entry - c)) - half; exited = True
            # T_max
            elif tmax_bars is not None and bars_held >= tmax_bars:
                pnl = ((c - t_entry) if t_dir == 'long' else (t_entry - c)) - half; exited = True
            if exited:
                trades.append(pnl); equity.append(equity[-1] + pnl); in_trade = False
            else:
                # 2) ratchet trail using this bar's close (effective from next bar)
                if atr_trail_mult > 0:
                    trail_dist = atr_v * atr_trail_mult
                    if t_dir == 'long':
                        t_sl_px = max(t_sl_px, c - trail_dist)
                    else:
                        t_sl_px = min(t_sl_px, c + trail_dist)
                continue

        if in_trade or adx_v <= 20.0 or sl_v == 0.0:
            continue
        if abs(ss_v - sl_v) / sl_v * 100.0 > sq_th:
            continue
        slp = sma_l[i - sp + 1:i + 1]
        if np.any(np.isnan(slp)):
            continue
        d = np.diff(slp)
        rising, falling = bool(np.all(d > 0)), bool(np.all(d < 0))
        if not (rising or falling):
            continue
        prev_c, prev_s = close_a[i - 1], sma_s[i - 1]
        if np.isnan(prev_s):
            continue
        sl_dist = atr_v * sl_mult
        sig_long = rising and c > sl_v and prev_c < prev_s and c > ss_v and c > o
        sig_short = falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o
        if not (sig_long or sig_short) or i + 1 >= n:
            continue
        entry_px = open_a[i + 1] + half * (1 if sig_long else -1)
        t_dir = 'long' if sig_long else 'short'
        t_entry = entry_px
        bars_held = 0
        if sig_long:
            t_sl_px = entry_px - sl_dist; t_tp_px = entry_px + sl_dist * rr
        else:
            t_sl_px = entry_px + sl_dist; t_tp_px = entry_px - sl_dist * rr
        in_trade = True

    arr = np.array(trades)
    if len(arr) == 0:
        return dict(PF=0.0, WR=0.0, n=0, net=0.0)
    wins, loss = arr[arr > 0], arr[arr <= 0]
    gp, gl = float(wins.sum()), float(abs(loss.sum()))
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    return dict(PF=round(pf, 3), WR=round(len(wins) / len(arr), 3),
                n=len(arr), net=round(float(arr.sum()), 4))


def fmt(m):
    return f"PF={m['PF']:>5.3f}  WR={m['WR']:>5.1%}  n={m['n']:>3}  net={m['net']:>10.4f}"


def main():
    print('=' * 78)
    print('SMA_SQ divergence BT  (live params, period 2024-04-24 ~ 2026-04-24)')
    print('=' * 78)
    for pair, cfg in LIVE_CFG.items():
        df = load_csv(pair, cfg['timeframe'])
        if df is None:
            print(f'{pair}: data NOT FOUND')
            continue
        sp = SPREAD[pair]
        print(f"\n### {pair}  tf={cfg['timeframe']}  bars={len(df)}  "
              f"sma={cfg['sma_short']}/{cfg['sma_long']}  sq_th={cfg['squeeze_th']}  "
              f"rr={cfg['rr']}  sl_mult={cfg['sl_atr_mult']}  spread={sp}")

        base = run_bt(df, cfg, 'close', 'in_squeeze', 0.0)
        print(f"  [0] current BT (close entry, in-squeeze, no spread) : {fmt(base)}")

        t1 = run_bt(df, cfg, 'next_open', 'in_squeeze', 0.0)
        print(f"  [1] next-open entry                                 : {fmt(t1)}")

        t1s = run_bt(df, cfg, 'next_open', 'in_squeeze', sp)
        print(f"  [3] next-open + spread                              : {fmt(t1s)}")

        t2 = run_bt(df, cfg, 'close', 'release', 0.0)
        print(f"  [2a] release-trigger (close entry, no spread)       : {fmt(t2)}")

        t2b = run_bt(df, cfg, 'next_open', 'release', sp)
        print(f"  [2b] release-trigger + next-open + spread           : {fmt(t2b)}")

        tmax = 24 if cfg['timeframe'] == '1h' else 6  # 24h
        e_full = run_bt_live_exits(df, cfg, sp, atr_trail_mult=0.5, slope_exit=3, tmax_bars=tmax)
        print(f"  [4] LIVE EXITS (trail0.5+slope3+SMAbrk+Tmax+spread)  : {fmt(e_full)}")
        e_notrail = run_bt_live_exits(df, cfg, sp, atr_trail_mult=0.0, slope_exit=3, tmax_bars=tmax)
        print(f"  [4b] live exits, NO trail                           : {fmt(e_notrail)}")
        e_noslope = run_bt_live_exits(df, cfg, sp, atr_trail_mult=0.5, slope_exit=None, tmax_bars=tmax)
        print(f"  [4c] live exits, NO slope-exit                      : {fmt(e_noslope)}")
        e_only_sltp = run_bt_live_exits(df, cfg, sp, atr_trail_mult=0.0, slope_exit=None, tmax_bars=None)
        print(f"  [4d] SL/TP + SMA-break only (no trail/slope/tmax)   : {fmt(e_only_sltp)}")


if __name__ == '__main__':
    main()
