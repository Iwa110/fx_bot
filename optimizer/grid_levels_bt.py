"""
grid_levels_bt.py - Grid strategy max_levels sensitivity analysis backtest.

Sweeps max_levels=[5,7,9,11,13] for GBPJPY / CHFJPY and compares:
  PF, total_pnl, n_tp, n_b48, b48_avg_pnl, max_dd, skip_count, skip_rate

Fixed params per pair:
  GBPJPY: atr_mult=1.5, ci_threshold=61.8, b48_hours=48, lot=0.02
  CHFJPY: atr_mult=2.0, ci_threshold=61.8, b48_hours=48, lot=0.02

Usage:
  python grid_levels_bt.py
"""

import math
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths (cross-platform) ──────────────────────────────────────────────────
if platform.system() == 'Windows':
    DATA_DIR   = r'C:\Users\Administrator\fx_bot\data'
    OUTPUT_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')
else:
    DATA_DIR   = str(Path(__file__).parent.parent / 'data')
    OUTPUT_DIR = Path(__file__).parent

OUTPUT_CSV = str(OUTPUT_DIR / 'grid_levels_bt_result.csv')

# ── Fixed parameters ────────────────────────────────────────────────────────
PAIR_CONFIG = {
    'GBPJPY': {'atr_mult': 1.5, 'ci_threshold': 61.8, 'b48_hours': 48, 'lot': 0.02},
    'CHFJPY': {'atr_mult': 2.0, 'ci_threshold': 61.8, 'b48_hours': 48, 'lot': 0.02},
}

MAX_LEVELS_LIST = [5, 7, 9, 11, 13]
ATR_PERIOD      = 14
CI_PERIOD       = 14
PIP_SIZE        = 0.01    # 1 pip in price units for JPY pairs
PIP_JPY_PER_LOT = 1000.0  # JPY per pip per 1 lot


# ── Data helpers ─────────────────────────────────────────────────────────────
def load_data(pair):
    path = os.path.join(DATA_DIR, pair + '_1h.csv')
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[['open', 'high', 'low', 'close']].sort_index().dropna()
    return df


def compute_atr_series(df, period=ATR_PERIOD):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def compute_ci_series(df_h1, period=CI_PERIOD):
    """
    Compute D1 Choppiness Index and align to H1 bars.
    For each H1 bar at time T, uses CI from the last completed D1 bar before T.
    """
    df_d1 = df_h1.resample('D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna(subset=['open'])

    h, l, c = df_d1['high'], df_d1['low'], df_d1['close']
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)

    tr_sum  = tr.rolling(period, min_periods=period).sum()
    h_max   = h.rolling(period, min_periods=period).max()
    l_min   = l.rolling(period, min_periods=period).min()
    hl_range = h_max - l_min

    valid = (hl_range > 0) & (tr_sum > 0)
    ci_d1 = pd.Series(np.nan, index=df_d1.index, dtype=float)
    ci_d1[valid] = (
        100.0 * np.log10(tr_sum[valid] / hl_range[valid]) / math.log10(period)
    )

    # Shift D1 CI forward 1 day: D1[date d] becomes available on d+1
    ci_shifted = ci_d1.copy()
    ci_shifted.index = ci_shifted.index + pd.Timedelta(days=1)

    # Forward-fill to H1 timestamps
    ci_h1 = ci_shifted.reindex(df_h1.index, method='ffill')
    return ci_h1


def pnl_jpy(price_diff, lot):
    """Convert price difference (in price units) to JPY."""
    return (price_diff / PIP_SIZE) * lot * PIP_JPY_PER_LOT


# ── Core simulation ──────────────────────────────────────────────────────────
def run_backtest(pair, max_levels, cfg, df, atr_series, ci_series):
    atr_mult     = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']
    b48_hours    = cfg['b48_hours']
    lot          = cfg['lot']

    long_pos        = []
    short_pos       = []
    b48_long_start  = None
    b48_short_start = None

    tp_pnls   = []   # individual TP trade PnLs (always > 0)
    b48_pnls  = []   # one entry per B48 event (can be negative)
    b48_pos_pnls = []  # per-position PnLs from B48 (for PF denominator)
    skip_count   = 0

    realized_pnl = 0.0
    peak_pnl     = 0.0
    max_dd       = 0.0

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci  = ci_series.get(ts)

        if pd.isna(atr) or atr <= 0:
            continue

        gw     = atr * atr_mult
        bar_h  = row['high']
        bar_l  = row['low']
        bar_cl = row['close']

        long_was_max  = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # ── TP check ──
        long_hits  = [p for p in long_pos  if bar_h >= p['tp']]
        short_hits = [p for p in short_pos if bar_l <= p['tp']]

        for p in long_hits:
            pnl = pnl_jpy(p['tp'] - p['entry'], lot)
            tp_pnls.append(pnl)
            realized_pnl += pnl
            long_pos.remove(p)

        for p in short_hits:
            pnl = pnl_jpy(p['entry'] - p['tp'], lot)
            tp_pnls.append(pnl)
            realized_pnl += pnl
            short_pos.remove(p)

        # ── B48 timer reset ──
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # ── B48 expiry ──
        if b48_long_start is not None:
            elapsed_h = (ts - b48_long_start).total_seconds() / 3600.0
            if elapsed_h >= b48_hours:
                pos_pnls   = [pnl_jpy(bar_cl - p['entry'], lot) for p in long_pos]
                event_pnl  = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls)
                b48_pnls.append(event_pnl)
                realized_pnl += event_pnl
                long_pos = []
                b48_long_start = None

        if b48_short_start is not None:
            elapsed_h = (ts - b48_short_start).total_seconds() / 3600.0
            if elapsed_h >= b48_hours:
                pos_pnls   = [pnl_jpy(p['entry'] - bar_cl, lot) for p in short_pos]
                event_pnl  = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls)
                b48_pnls.append(event_pnl)
                realized_pnl += event_pnl
                short_pos = []
                b48_short_start = None

        # ── DD tracking ──
        if realized_pnl > peak_pnl:
            peak_pnl = realized_pnl
        dd = peak_pnl - realized_pnl
        if dd > max_dd:
            max_dd = dd

        # ── New entries ──
        ci_ok = (not pd.isna(ci)) and (ci > ci_threshold)

        if len(long_pos) == 0:
            if ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels:
                    b48_long_start = ts
        elif len(long_pos) < max_levels:
            min_le = min(p['entry'] for p in long_pos)
            if bar_cl <= min_le - gw and ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels:
                    b48_long_start = ts
        else:
            min_le = min(p['entry'] for p in long_pos)
            if bar_cl <= min_le - gw and ci_ok:
                skip_count += 1

        if len(short_pos) == 0:
            if ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels:
                    b48_short_start = ts
        elif len(short_pos) < max_levels:
            max_se = max(p['entry'] for p in short_pos)
            if bar_cl >= max_se + gw and ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels:
                    b48_short_start = ts
        else:
            max_se = max(p['entry'] for p in short_pos)
            if bar_cl >= max_se + gw and ci_ok:
                skip_count += 1

    # ── Compute PF over all trade PnLs ──
    all_pnls = tp_pnls + b48_pos_pnls
    wins     = [p for p in all_pnls if p >= 0]
    losses   = [p for p in all_pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    n_tp   = len(tp_pnls)
    n_b48  = len(b48_pnls)
    total_events = n_tp + n_b48 + skip_count
    skip_rate = (skip_count / total_events) if total_events > 0 else 0.0

    b48_avg = (sum(b48_pnls) / n_b48) if n_b48 > 0 else 0.0

    return {
        'pf':          round(pf, 4),
        'total_pnl':   round(realized_pnl, 0),
        'n_tp':        n_tp,
        'n_b48':       n_b48,
        'b48_avg_pnl': round(b48_avg, 0),
        'max_dd':      round(max_dd, 0),
        'skip_count':  skip_count,
        'skip_rate':   round(skip_rate, 4),
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    rows = []

    for pair, cfg in PAIR_CONFIG.items():
        print(f'\n=== {pair} (atr_mult={cfg["atr_mult"]}, lot={cfg["lot"]}) ===')

        try:
            df = load_data(pair)
        except FileNotFoundError:
            print(f'  [SKIP] data file not found: {pair}_1h.csv')
            continue

        print(f'  rows={len(df)}  {df.index[0].date()} ~ {df.index[-1].date()}')

        atr_series = compute_atr_series(df)
        ci_series  = compute_ci_series(df)

        # Header
        print(
            f'\n  {"levels":>7} | {"PF":>6} | {"total_pnl":>10} | '
            f'{"n_tp":>6} | {"n_b48":>6} | {"b48_avg":>9} | '
            f'{"max_dd":>9} | {"skip_cnt":>8} | {"skip_rate":>9}'
        )
        print('  ' + '-' * 97)

        for ml in MAX_LEVELS_LIST:
            res = run_backtest(pair, ml, cfg, df, atr_series, ci_series)

            pf_str = f'{res["pf"]:.4f}' if res['pf'] != float('inf') else '  inf'
            print(
                f'  {ml:>7} | {pf_str:>6} | {res["total_pnl"]:>10,.0f} | '
                f'{res["n_tp"]:>6} | {res["n_b48"]:>6} | {res["b48_avg_pnl"]:>9,.0f} | '
                f'{res["max_dd"]:>9,.0f} | {res["skip_count"]:>8} | {res["skip_rate"]:>9.4f}'
            )

            rows.append({
                'pair':        pair,
                'max_levels':  ml,
                'pf':          res['pf'],
                'total_pnl':   res['total_pnl'],
                'n_tp':        res['n_tp'],
                'n_b48':       res['n_b48'],
                'b48_avg_pnl': res['b48_avg_pnl'],
                'max_dd':      res['max_dd'],
                'skip_count':  res['skip_count'],
                'skip_rate':   res['skip_rate'],
            })

    # Save CSV
    df_out = pd.DataFrame(rows)
    df_out.to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved: {OUTPUT_CSV}')


if __name__ == '__main__':
    main()
