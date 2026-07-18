"""
grid_floatstop_bt.py - Grid realized-edge survival test through adverse trends.

Purpose (answers two questions for live-config grid):
  (a) Run over the full 2yr history so the simulation INCLUDES adverse-trend
      windows that trigger FLOAT_STOP and B48 forced exits.
  (b) Confirm whether realized edge SURVIVES after those stops fire, i.e.
      is net PnL / PF still positive once forced-exit losses are booked.

Faithful to live grid_monitor.py config (1.00 lot):
  CHFJPY: atr_mult=2.0, max_levels=7, b48_hours=48, FLOAT_STOP=-1,500,000/dir
  GBPJPY: atr_mult=1.5, max_levels=7, b48_hours=48, FLOAT_STOP=-1,500,000/dir

Float-stop modeled intrabar at the adverse bar extreme (low for longs, high for
shorts) = conservative/faithful worst-case detection, realized at that extreme.

2026-07-18 additions (AI-loop prep task #1, grid_loop_engineering_design.md
sec 7.5; guarded by test_grid_floatstop_static.py - all new cfg keys default
OFF and reproduce the frozen pre-change baseline exactly):
  tp_mult        : uniform per-leg TP distance multiplier (TP = gw * tp_mult).
                   Default 1.0 = legacy TP = gw.
  tp_level_mults : list of TP multipliers by ladder depth (asymmetric TP).
                   Leg added as k-th open leg uses tp_level_mults[k-1]
                   (last element extends for deeper legs). Overrides tp_mult.
                   Default None = uniform.
  ptp_frac       : partial take-profit. Close this fraction of a leg's lot at
                   a nearer target, remainder rides to the full TP. 0 < f < 1.
                   Default None = off.
  ptp_mult       : partial target distance = gw * ptp_mult (default 0.5).
                   Only used when ptp_frac is set.
Partial closes keep the leg open (counts toward max_levels) with reduced lot,
matching an MT5 partial-close implementation on the live side.

Usage:
  python grid_floatstop_bt.py
"""

import math
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd

if platform.system() == 'Windows':
    DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
    OUTPUT_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')
else:
    DATA_DIR = str(Path(__file__).parent.parent / 'data')
    OUTPUT_DIR = Path(__file__).parent

OUTPUT_CSV = str(OUTPUT_DIR / 'grid_floatstop_bt_result.csv')

# Live config faithful to grid_monitor.py LOT_PER_PAIR / FLOAT_STOP_PER_PAIR.
# quote_jpy: factor converting 1 unit of quote-currency PnL to JPY.
#   JPY-quote pairs (GBPJPY/CHFJPY/NZDJPY) = 1.0
#   NZDUSD (USD quote)  ~= USDJPY avg over 2024-2026 ~= 155
#   AUDCAD (CAD quote)  ~= CADJPY avg ~= 108 (grid_monitor comment uses ~102)
# CONTRACT = 100,000 units/lot. pnl_jpy = price_diff * lot * CONTRACT * quote_jpy.
CONTRACT = 100_000.0
PAIR_CONFIG = {
    'GBPJPY': {'atr_mult': 1.5, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'CHFJPY': {'atr_mult': 2.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -1_500_000.0, 'quote_jpy': 1.0},
    'NZDJPY': {'atr_mult': 1.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -500_000.0, 'quote_jpy': 1.0},
    'AUDCAD': {'atr_mult': 1.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 1.0, 'max_levels': 7, 'float_stop': -500_000.0, 'quote_jpy': 108.0},
    'NZDUSD': {'atr_mult': 2.0, 'ci_threshold': 61.8, 'b48_hours': 48,
               'lot': 0.01, 'max_levels': 7, 'float_stop': -15_000.0, 'quote_jpy': 155.0},
}

ATR_PERIOD = 14
CI_PERIOD = 14


def load_data(pair):
    path = os.path.join(DATA_DIR, pair + '_1h.csv')
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[['open', 'high', 'low', 'close']].sort_index().dropna()
    return df


def compute_atr_series(df, period=ATR_PERIOD):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def compute_ci_series(df_h1, period=CI_PERIOD):
    df_d1 = df_h1.resample('D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna(subset=['open'])
    h, l, c = df_d1['high'], df_d1['low'], df_d1['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    tr_sum = tr.rolling(period, min_periods=period).sum()
    h_max = h.rolling(period, min_periods=period).max()
    l_min = l.rolling(period, min_periods=period).min()
    hl_range = h_max - l_min
    valid = (hl_range > 0) & (tr_sum > 0)
    ci_d1 = pd.Series(np.nan, index=df_d1.index, dtype=float)
    ci_d1[valid] = 100.0 * np.log10(tr_sum[valid] / hl_range[valid]) / math.log10(period)
    ci_shifted = ci_d1.copy()
    ci_shifted.index = ci_shifted.index + pd.Timedelta(days=1)
    return ci_shifted.reindex(df_h1.index, method='ffill')


_QJ = 1.0  # quote->JPY factor, set per run_backtest call


def pnl_jpy(price_diff, lot):
    return price_diff * lot * CONTRACT * _QJ


def run_backtest(pair, cfg, df, atr_series, ci_series):
    atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']
    b48_hours = cfg['b48_hours']
    lot = cfg['lot']
    max_levels = cfg['max_levels']
    float_stop = cfg['float_stop']
    global _QJ
    _QJ = cfg.get('quote_jpy', 1.0)

    # 2026-07-18 knobs (all default OFF = legacy behaviour, see docstring)
    tp_mult = cfg.get('tp_mult', 1.0)
    tp_level_mults = cfg.get('tp_level_mults')   # list by ladder depth, or None
    ptp_frac = cfg.get('ptp_frac')               # 0<f<1 partial close, None = off
    ptp_mult = cfg.get('ptp_mult', 0.5)
    if ptp_frac is not None and not (0.0 < ptp_frac < 1.0):
        raise ValueError('ptp_frac must be in (0, 1) or None')

    def tp_mult_for(depth):
        if tp_level_mults:
            return tp_level_mults[min(depth - 1, len(tp_level_mults) - 1)]
        return tp_mult

    def new_pos(side, price, gw, depth):
        m = tp_mult_for(depth)
        p = {'entry': price, 'lot': lot, 'lot0': lot,
             'tp': price + gw * m if side > 0 else price - gw * m}
        if ptp_frac:
            p['ptp'] = price + gw * ptp_mult if side > 0 else price - gw * ptp_mult
            p['ptp_done'] = False
        return p

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None

    tp_pnls, b48_pnls, b48_pos_pnls = [], [], []
    ptp_pnls = []                        # partial take-profit closes
    fs_pnls, fs_pos_pnls = [], []        # float-stop events / per-position
    skip_count = 0

    realized_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    worst_event = 0.0                    # most negative single forced-exit event
    monthly = {}                         # YYYY-MM -> realized pnl

    def add_month(ts, v):
        k = ts.strftime('%Y-%m')
        monthly[k] = monthly.get(k, 0.0) + v

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci = ci_series.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue

        gw = atr * atr_mult
        bar_h, bar_l, bar_cl = row['high'], row['low'], row['close']

        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # ── Partial TP check (before full TP; if both hit in one bar the
        #    partial books at ptp price, remainder at full tp price) ──
        if ptp_frac:
            for p in long_pos:
                if not p['ptp_done'] and bar_h >= p['ptp']:
                    part = p['lot0'] * ptp_frac
                    pnl = pnl_jpy(p['ptp'] - p['entry'], part)
                    ptp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
                    p['lot'] -= part; p['ptp_done'] = True
            for p in short_pos:
                if not p['ptp_done'] and bar_l <= p['ptp']:
                    part = p['lot0'] * ptp_frac
                    pnl = pnl_jpy(p['entry'] - p['ptp'], part)
                    ptp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
                    p['lot'] -= part; p['ptp_done'] = True

        # ── TP check ──
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            pnl = pnl_jpy(p['tp'] - p['entry'], p['lot'])
            tp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
            long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            pnl = pnl_jpy(p['entry'] - p['tp'], p['lot'])
            tp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
            short_pos.remove(p)

        # ── FLOAT STOP (intrabar adverse extreme) ──
        # Longs hurt by low; shorts hurt by high.
        if long_pos:
            unreal = sum(pnl_jpy(bar_l - p['entry'], p['lot']) for p in long_pos)
            if unreal <= float_stop:
                pos_pnls = [pnl_jpy(bar_l - p['entry'], p['lot']) for p in long_pos]
                ev = sum(pos_pnls)
                fs_pos_pnls.extend(pos_pnls); fs_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                long_pos = []; b48_long_start = None
        if short_pos:
            unreal = sum(pnl_jpy(p['entry'] - bar_h, p['lot']) for p in short_pos)
            if unreal <= float_stop:
                pos_pnls = [pnl_jpy(p['entry'] - bar_h, p['lot']) for p in short_pos]
                ev = sum(pos_pnls)
                fs_pos_pnls.extend(pos_pnls); fs_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                short_pos = []; b48_short_start = None

        # ── B48 timer reset ──
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # ── B48 expiry ──
        if b48_long_start is not None:
            if (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
                pos_pnls = [pnl_jpy(bar_cl - p['entry'], p['lot']) for p in long_pos]
                ev = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls); b48_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                long_pos = []; b48_long_start = None
        if b48_short_start is not None:
            if (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
                pos_pnls = [pnl_jpy(p['entry'] - bar_cl, p['lot']) for p in short_pos]
                ev = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls); b48_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                short_pos = []; b48_short_start = None

        # ── DD tracking ──
        peak_pnl = max(peak_pnl, realized_pnl)
        max_dd = max(max_dd, peak_pnl - realized_pnl)

        # ── New entries ──
        ci_ok = (not pd.isna(ci)) and (ci > ci_threshold)
        if len(long_pos) == 0:
            if ci_ok:
                long_pos.append(new_pos(+1, bar_cl, gw, len(long_pos) + 1))
                if len(long_pos) == max_levels: b48_long_start = ts
        elif len(long_pos) < max_levels:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                long_pos.append(new_pos(+1, bar_cl, gw, len(long_pos) + 1))
                if len(long_pos) == max_levels: b48_long_start = ts
        else:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                skip_count += 1

        if len(short_pos) == 0:
            if ci_ok:
                short_pos.append(new_pos(-1, bar_cl, gw, len(short_pos) + 1))
                if len(short_pos) == max_levels: b48_short_start = ts
        elif len(short_pos) < max_levels:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                short_pos.append(new_pos(-1, bar_cl, gw, len(short_pos) + 1))
                if len(short_pos) == max_levels: b48_short_start = ts
        else:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                skip_count += 1

    all_pnls = tp_pnls + ptp_pnls + b48_pos_pnls + fs_pos_pnls
    wins = [p for p in all_pnls if p >= 0]
    losses = [p for p in all_pnls if p < 0]
    gp = sum(wins); gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else float('inf')

    return {
        'pf': round(pf, 4),
        'total_pnl': round(realized_pnl, 0),
        'n_tp': len(tp_pnls),
        'n_ptp': len(ptp_pnls),
        'ptp_total': round(sum(ptp_pnls), 0),
        'n_b48': len(b48_pnls),
        'b48_total': round(sum(b48_pnls), 0),
        'n_fstop': len(fs_pnls),
        'fstop_total': round(sum(fs_pnls), 0),
        'worst_event': round(worst_event, 0),
        'max_dd': round(max_dd, 0),
        'skip_count': skip_count,
        'monthly': monthly,
        'fs_events': fs_pnls,    # per float-stop event PnL (for gap/slippage distribution)
        'b48_events': b48_pnls,  # per B48 event PnL
    }


def main():
    rows = []
    for pair, cfg in PAIR_CONFIG.items():
        try:
            df = load_data(pair)
        except FileNotFoundError:
            print(f'[SKIP] {pair}_1h.csv not found'); continue

        atr_series = compute_atr_series(df)
        ci_series = compute_ci_series(df)
        res = run_backtest(pair, cfg, df, atr_series, ci_series)

        print(f'\n=== {pair} (atr_mult={cfg["atr_mult"]}, lot={cfg["lot"]}, '
              f'max_levels={cfg["max_levels"]}, float_stop={cfg["float_stop"]:,.0f}) ===')
        print(f'  period   : {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} bars)')
        print(f'  PF (net) : {res["pf"]}')
        print(f'  total    : {res["total_pnl"]:>14,.0f} JPY')
        print(f'  TP       : n={res["n_tp"]}')
        if res['n_ptp']:
            print(f'  PTP      : n={res["n_ptp"]}  total={res["ptp_total"]:>12,.0f}')
        print(f'  B48      : n={res["n_b48"]}  total={res["b48_total"]:>12,.0f}')
        print(f'  FLOAT-STOP: n={res["n_fstop"]}  total={res["fstop_total"]:>12,.0f}')
        print(f'  worst single forced-exit event: {res["worst_event"]:>12,.0f}')
        print(f'  max_dd   : {res["max_dd"]:>14,.0f}')
        print(f'  skip_cnt : {res["skip_count"]}')
        print('  --- monthly realized PnL ---')
        for k in sorted(res['monthly']):
            print(f'    {k}: {res["monthly"][k]:>14,.0f}')

        rows.append({k: v for k, v in res.items() if k != 'monthly'} | {'pair': pair})

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved: {OUTPUT_CSV}')


if __name__ == '__main__':
    main()
