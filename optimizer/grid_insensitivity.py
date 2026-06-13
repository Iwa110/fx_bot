"""
grid_insensitivity.py - Define "grid 不感症 (insensitivity)" windows.

The Grid (mean-reversion) only enters when CI(choppiness) > threshold = RANGE.
When CI is low (TREND) the grid is asleep (no new ladders) or gets its open
ladder run over (float-stop/B48 losses). A complement sleeve should earn exactly
in those windows. This module instruments the v7 grid engine (faithful to
grid_floatstop_bt / portfolio_meta_bt) to emit, per pair, a DAILY state table:

  grid_realized   : realized PnL booked that day (TP + float-stop + B48), JPY
  has_pos         : grid held >=1 open position at some point that day
  n_tp / n_force  : count of TP / forced (float-stop+B48) events that day
  unreal_worst    : worst intrabar aggregate unrealized PnL of open ladder, JPY

From these, three insensitivity-day flags (all t-1 safe; CI is D1 shifted +1d):
  (1) idle  : CI <= ci_threshold all day  -> grid cannot open new ladders
  (2) bleed : a forced-exit loss fired that day, OR open ladder went deeply
              underwater (unreal_worst <= bleed_frac * float_stop)
  (3) flat  : rolling-20d realized-PnL slope ~ 0 (|slope| < flat_eps * dailyStd)

Union(idle|bleed|flat) = insensitivity days. Used by grid_insensitivity_complement.py.

Usage:  python grid_insensitivity.py        # prints day counts IS/OOS per pair
"""

import numpy as np
import pandas as pd

import grid_floatstop_bt as G

IS_END = pd.Timestamp('2025-06-30')
OOS_START = pd.Timestamp('2025-07-01')

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

BLEED_FRAC = 0.30     # open ladder underwater past 30% of float_stop budget = bleed
FLAT_WIN = 20         # rolling window (days) for flat detection
FLAT_EPS = 0.10       # |slope| < FLAT_EPS * dailyStd  => flat


def grid_state(cfg, df, atr_series, ci_series):
    """Run v7 grid; return (events, per-bar state DataFrame).
    events: list[(ts, pnl, kind)] kind in {'tp','fstop','b48'}.
    bar state columns: n_long,n_short,unreal_worst,realized,n_tp,n_force,ci."""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot, atr_mult, ci_th = cfg['lot'], cfg['atr_mult'], cfg['ci_threshold']
    b48_hours, max_levels, float_stop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    events = []
    recs = []

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
        day_realized = 0.0
        n_tp = n_force = 0

        # TP
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            v = pj(p['tp'] - p['entry']); events.append((ts, v, 'tp'))
            day_realized += v; n_tp += 1; long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            v = pj(p['entry'] - p['tp']); events.append((ts, v, 'tp'))
            day_realized += v; n_tp += 1; short_pos.remove(p)

        # worst intrabar unrealized BEFORE forced exits (adverse extreme)
        unreal_worst = 0.0
        if long_pos:
            unreal_worst = min(unreal_worst, sum(pj(bar_l - p['entry']) for p in long_pos))
        if short_pos:
            unreal_worst = min(unreal_worst, sum(pj(p['entry'] - bar_h) for p in short_pos))

        # FLOAT STOP
        if long_pos and sum(pj(bar_l - p['entry']) for p in long_pos) <= float_stop:
            v = sum(pj(bar_l - p['entry']) for p in long_pos)
            events.append((ts, v, 'fstop')); day_realized += v; n_force += 1
            long_pos = []; b48_long_start = None
        if short_pos and sum(pj(p['entry'] - bar_h) for p in short_pos) <= float_stop:
            v = sum(pj(p['entry'] - bar_h) for p in short_pos)
            events.append((ts, v, 'fstop')); day_realized += v; n_force += 1
            short_pos = []; b48_short_start = None

        # B48 timer reset
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # B48 expiry
        if b48_long_start is not None and (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
            v = sum(pj(bar_cl - p['entry']) for p in long_pos)
            events.append((ts, v, 'b48')); day_realized += v; n_force += 1
            long_pos = []; b48_long_start = None
        if b48_short_start is not None and (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
            v = sum(pj(p['entry'] - bar_cl) for p in short_pos)
            events.append((ts, v, 'b48')); day_realized += v; n_force += 1
            short_pos = []; b48_short_start = None

        # New entries
        ci_ok = (not pd.isna(ci)) and (ci > ci_th)
        if ci_ok:
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

        recs.append({'ts': ts, 'n_long': len(long_pos), 'n_short': len(short_pos),
                     'unreal_worst': unreal_worst, 'realized': day_realized,
                     'n_tp': n_tp, 'n_force': n_force,
                     'ci': ci if not pd.isna(ci) else np.nan, 'ci_th': ci_th})
    state = pd.DataFrame(recs).set_index('ts')
    return events, state


def daily_state(state):
    """Collapse per-bar grid state to per-DAY state + insensitivity flags."""
    s = state.copy()
    s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
    g = s.groupby(level=0)
    d = pd.DataFrame({
        'grid_realized': g['realized'].sum(),
        'has_pos': (g['n_long'].max() + g['n_short'].max()) > 0,
        'n_tp': g['n_tp'].sum(),
        'n_force': g['n_force'].sum(),
        'unreal_worst': g['unreal_worst'].min(),
        'ci': g['ci'].last(),
        'ci_th': g['ci_th'].last(),
    })
    return d


def add_flags(d, float_stop):
    """Attach idle/bleed/flat/insens boolean columns to a daily-state frame."""
    d = d.copy()
    # idle = grid genuinely out of market that day (no open ladder, no TP booked)
    d['idle'] = (~d['has_pos']) & (d['n_tp'] == 0)
    d['bleed'] = (d['n_force'] > 0) | (d['unreal_worst'] <= BLEED_FRAC * abs(float_stop) * -1.0)
    # flat: rolling slope of cumulative realized ~ 0 relative to daily volatility
    cum = d['grid_realized'].cumsum()
    x = np.arange(FLAT_WIN)
    xm = x - x.mean()
    denom = (xm ** 2).sum()
    slope = cum.rolling(FLAT_WIN).apply(
        lambda y: ((np.arange(len(y)) - np.arange(len(y)).mean()) * (y - y.mean())).sum() / denom,
        raw=True)
    std = d['grid_realized'].std()
    d['flat'] = slope.abs() < (FLAT_EPS * std)
    d['flat'] = d['flat'].fillna(False)
    # union = grid dormant OR bleeding. flat kept as diagnostic only (not
    # discriminating: lumpy grid PnL makes most 20d slopes ~0).
    d['insens'] = d['idle'] | d['bleed']
    return d


def build_all():
    """Return dict pair -> daily-state frame with flags, plus a COMBINED frame."""
    out = {}
    for pair in GRID_PAIRS:
        df = G.load_data(pair)
        atr_s = G.compute_atr_series(df)
        ci_s = G.compute_ci_series(df)
        _, state = grid_state(V7_CONFIG[pair], df, atr_s, ci_s)
        d = daily_state(state)
        d = add_flags(d, V7_CONFIG[pair]['float_stop'])
        out[pair] = d
    return out


def main():
    allp = build_all()
    lo = min(d.index.min() for d in allp.values())
    hi = max(d.index.max() for d in allp.values())
    print(f'period: {lo.date()} ~ {hi.date()}\n')
    hdr = f'{"pair":8s} {"seg":4s} {"days":>5s} {"idle":>6s} {"bleed":>6s} {"flat":>6s} {"insens":>7s} {"grid_net":>13s} {"insNet":>13s} {"normNet":>13s}'
    print(hdr)
    for pair, d in allp.items():
        for seg, mask in [('IS', d.index <= IS_END), ('OOS', d.index >= OOS_START)]:
            ds = d[mask]
            ins = ds['insens']
            print(f'{pair:8s} {seg:4s} {len(ds):5d} {ds["idle"].sum():6d} '
                  f'{ds["bleed"].sum():6d} {ds["flat"].sum():6d} {ins.sum():7d} '
                  f'{ds["grid_realized"].sum():13,.0f} {ds.loc[ins,"grid_realized"].sum():13,.0f} '
                  f'{ds.loc[~ins,"grid_realized"].sum():13,.0f}')


if __name__ == '__main__':
    main()
