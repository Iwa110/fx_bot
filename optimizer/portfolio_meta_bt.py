"""
portfolio_meta_bt.py - Portfolio meta-layer edge search.

The signal layer is exhausted (BB/CORR/stat_arb/London/pullback/pre_event/
session all closed). This explores a META layer on top of the already-confirmed
edges (Grid v7 pairs + BB USDJPY) to lift risk-adjusted return:

  A. Allocation layer - inverse-vol / risk-parity / vol-targeting weighting of
     each sleeve's daily PnL vs the current fixed-lot baseline.
  B. Regime overlay - gate the Grid (trend-tail weakness) with a trend-strength
     filter (efficiency ratio / ADX) so float-stop tails shrink.

Guardrails (hard-won):
  - next-bar fill only; no signal-bar-close execution (case alpha lookahead).
  - full 2yr data + IS/OOS split; months of positive PF are window luck.
  - allocation/regime weights use ONLY info available up to t-1 (no lookahead).

Sleeves (materials, NOT new signals):
  Grid v7: GBPJPY/CHFJPY/NZDJPY/AUDCAD  (grid_floatstop logic, v7 PAIR_CONFIG)
  BB USDJPY: next-bar 1h proxy of bb_monitor v27 (sigma=2.0). 5m unavailable
             over 2yr (yfinance ~3mo), so a 1h proxy stands in for the PnL
             *shape*; it is a diversifying sleeve, Grid dominates portfolio risk.

Usage:  python portfolio_meta_bt.py
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G

OUT_DIR = Path(__file__).resolve().parent
IS_END = pd.Timestamp('2025-06-30')          # IS: 2024-04 .. 2025-06
OOS_START = pd.Timestamp('2025-07-01')        # OOS: 2025-07 .. 2026-05

# v7 live PAIR_CONFIG (grid_monitor v7). Faithful to CLAUDE.md confirmed configs.
V7_CONFIG = {
    'GBPJPY': {'atr_mult': 1.5, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'CHFJPY': {'atr_mult': 1.0, 'ci_threshold': 65.0, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 3, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'NZDJPY': {'atr_mult': 1.5, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_000_000.0, 'quote_jpy': 1.0},
    'AUDCAD': {'atr_mult': 1.0, 'ci_threshold': 65.0, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 5, 'float_stop': -750_000.0, 'quote_jpy': 108.0},
}
GRID_PAIRS = list(V7_CONFIG.keys())


# ───────────────────────── Grid engine (regime-gateable) ─────────────────────
def grid_pnl_events(cfg, df, atr_series, ci_series, regime_block=None):
    """Realised grid PnL events as list[(ts, pnl_jpy)].
    regime_block: optional bool Series indexed like df; True => no NEW entries
    this bar (positions still managed normally). None => baseline (no gate)."""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot, atr_mult, ci_th = cfg['lot'], cfg['atr_mult'], cfg['ci_threshold']
    b48_hours, max_levels, float_stop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    events = []

    def pj(d):
        return G.pnl_jpy(d, lot)

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci = ci_series.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue
        gw = atr * atr_mult
        bar_h, bar_l, bar_cl = row['high'], row['low'], row['close']
        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # TP
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            events.append((ts, pj(p['tp'] - p['entry']))); long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            events.append((ts, pj(p['entry'] - p['tp']))); short_pos.remove(p)

        # FLOAT STOP (intrabar adverse extreme)
        if long_pos:
            if sum(pj(bar_l - p['entry']) for p in long_pos) <= float_stop:
                events.append((ts, sum(pj(bar_l - p['entry']) for p in long_pos)))
                long_pos = []; b48_long_start = None
        if short_pos:
            if sum(pj(p['entry'] - bar_h) for p in short_pos) <= float_stop:
                events.append((ts, sum(pj(p['entry'] - bar_h) for p in short_pos)))
                short_pos = []; b48_short_start = None

        # B48 timer reset
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # B48 expiry
        if b48_long_start is not None and (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
            events.append((ts, sum(pj(bar_cl - p['entry']) for p in long_pos)))
            long_pos = []; b48_long_start = None
        if b48_short_start is not None and (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
            events.append((ts, sum(pj(p['entry'] - bar_cl) for p in short_pos)))
            short_pos = []; b48_short_start = None

        # New entries (regime gate blocks adds only; CI gate unchanged)
        ci_ok = (not pd.isna(ci)) and (ci > ci_th)
        blocked = bool(regime_block.get(ts, False)) if regime_block is not None else False
        if ci_ok and not blocked:
            if len(long_pos) == 0:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
            elif len(long_pos) < max_levels and bar_cl <= min(p['entry'] for p in long_pos) - gw:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
            if len(short_pos) == 0:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts
            elif len(short_pos) < max_levels and bar_cl >= max(p['entry'] for p in short_pos) + gw:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts

    return events


def events_to_daily(events):
    if not events:
        return pd.Series(dtype=float)
    s = pd.Series([v for _, v in events], index=[t for t, _ in events])
    s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
    return s.groupby(level=0).sum()


# ───────────────────────── BB USDJPY 1h proxy ───────────────────────────────
def bb_usdjpy_daily():
    """Next-bar BB(20,2.0) mean-reversion on USDJPY 1h, SL=2.5*ATR14, TP=1.5*SL.
    Entry: bar t closes outside band -> enter at bar t+1 OPEN (next-bar fill).
    Exit: SL/TP intrabar, else T_max=8 bars (proxy of v27 8h). lot=0.2 (real)."""
    df = G.load_data('USDJPY')
    n = 20
    mid = df['close'].rolling(n).mean()
    sd = df['close'].rolling(n).std()
    upper, lower = mid + 2.0 * sd, mid - 2.0 * sd
    atr = G.compute_atr_series(df, 14)
    o, h, l, c = df['open'], df['high'], df['low'], df['close']
    idx = df.index
    lot, contract = 0.2, 100_000.0
    t_max = 8
    events = []
    i = n + 1
    cooldown_until = -1
    while i < len(df) - 1:
        if i <= cooldown_until:
            i += 1; continue
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            i += 1; continue
        sig = None
        if c.iloc[i] > upper.iloc[i]:
            sig = 'sell'
        elif c.iloc[i] < lower.iloc[i]:
            sig = 'buy'
        if sig is None:
            i += 1; continue
        # next-bar fill at open of i+1
        entry = o.iloc[i + 1]
        sl_d = 2.5 * a
        tp_d = 1.5 * sl_d
        sign = 1.0 if sig == 'buy' else -1.0
        sl = entry - sign * sl_d
        tp = entry + sign * tp_d
        exit_px = None
        for j in range(i + 1, min(i + 1 + t_max, len(df))):
            hj, lj = h.iloc[j], l.iloc[j]
            if sig == 'buy':
                if lj <= sl: exit_px = sl; break
                if hj >= tp: exit_px = tp; break
            else:
                if hj >= sl: exit_px = sl; break
                if lj <= tp: exit_px = tp; break
            ex_ts = idx[j]
        else:
            j = min(i + t_max, len(df) - 1)
        if exit_px is None:
            exit_px = c.iloc[j]
        ex_ts = idx[j]
        pnl = sign * (exit_px - entry) * lot * contract  # JPY (quote=JPY)
        events.append((ex_ts, pnl))
        cooldown_until = j + 3
        i = i + 1
    s = pd.Series([v for _, v in events], index=[t for t, _ in events])
    s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
    return s.groupby(level=0).sum()


# ───────────────────────── metrics ──────────────────────────────────────────
def metrics(daily):
    """PF, Sharpe(daily,ann), maxDD(abs), maxDD% (of peak equity from 0 base via
    running peak of cumulative), net, worst-day. daily: pd.Series indexed by day."""
    v = daily.values.astype(float)
    if len(v) == 0 or v.std() == 0:
        return dict(pf=np.nan, sharpe=np.nan, maxdd=np.nan, net=0.0, worst=0.0)
    gp = v[v > 0].sum(); gl = -v[v < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    sharpe = v.mean() / v.std() * np.sqrt(252) if v.std() > 0 else np.nan
    return dict(pf=pf, sharpe=sharpe, maxdd=dd, net=float(v.sum()), worst=float(v.min()))


def build_sleeves():
    sleeves = {}
    for pair in GRID_PAIRS:
        df = G.load_data(pair)
        atr_s = G.compute_atr_series(df)
        ci_s = G.compute_ci_series(df)
        ev = grid_pnl_events(V7_CONFIG[pair], df, atr_s, ci_s)
        sleeves['GRID_' + pair] = events_to_daily(ev)
    sleeves['BB_USDJPY'] = bb_usdjpy_daily()
    lo = min(s.index.min() for s in sleeves.values())
    hi = max(s.index.max() for s in sleeves.values())
    cal = pd.date_range(lo, hi, freq='D')
    return pd.DataFrame({k: s.reindex(cal).fillna(0.0) for k, s in sleeves.items()})


if __name__ == '__main__':
    daily = build_sleeves()
    daily.to_csv(OUT_DIR / 'portfolio_meta_sleeves.csv')
    print('period:', daily.index.min().date(), '~', daily.index.max().date(), f'({len(daily)}d)')
    print('\n=== per-sleeve full-2yr metrics (fixed live lot) ===')
    print(f'{"sleeve":14s} {"PF":>6s} {"Sharpe":>7s} {"maxDD":>12s} {"net":>13s} {"worstDay":>12s} {"dailyStd":>11s}')
    for col in daily.columns:
        m = metrics(daily[col])
        print(f'{col:14s} {m["pf"]:6.2f} {m["sharpe"]:7.2f} {m["maxdd"]:12,.0f} '
              f'{m["net"]:13,.0f} {m["worst"]:12,.0f} {daily[col].std():11,.0f}')
    print('\n=== daily corr matrix (full 2yr) ===')
    print(daily.corr().round(2).to_string())
    print('\n=== corr matrix (active days: any sleeve != 0 same day kept) ===')
    print(daily.corr().round(2).to_string())
