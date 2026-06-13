"""
breakout_bt.py - Donchian channel breakout strategy grid search backtest.

Data:   {PAIR}_1h.csv from DATA_DIR (columns: time,open,high,low,close,volume)
Output: optimizer/results/breakout_bt_results.csv

Logic:
  Long  : close breaks above prior dc_period high (Donchian upper)
  Short : close breaks below prior dc_period low  (Donchian lower)
  Filters (all must pass): ADX>=threshold, 200MA direction, Choppiness<50
  Exit  : TP=ATR*tp_mult, SL=ATR*sl_mult, or 72h time stop.
  One position per pair at a time (no overlapping entries).
"""

import itertools
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

DATA_DIR   = os.environ.get('FX_DATA_DIR', r'C:\Users\Administrator\fx_bot\data')
OUTPUT_DIR = Path(__file__).parent / 'results'
OUTPUT_CSV = str(OUTPUT_DIR / 'breakout_bt_results.csv')

PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'GBPJPY', 'EURJPY']

GRID = {
    'dc_period':     [20, 40, 60],
    'tp_atr_mult':   [2.0, 3.0, 4.0],
    'sl_atr_mult':   [1.0, 1.5, 2.0],
    'adx_threshold': [20, 25, 30],
    'use_200ma':     [True, False],
    'use_chop':      [True, False],
}

ATR_PERIOD   = 14
MA_PERIOD    = 200
CHOP_PERIOD  = 14
MAX_HOLD_H   = 72            # forced exit after 72 bars (1h data)
MIN_TRADES   = 20
INIT_EQ_PIPS = 1000.0        # notional starting equity (pips) for DD% scaling


def load_csv(pair):
    """Load 1h OHLC. CSV first (unnamed) column is the timestamp index."""
    for fname in (f'{pair}_1h.csv', f'{pair}_H1.csv', f'{pair.lower()}_1h.csv'):
        path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index)
        try:
            df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass
        df.columns = [c.lower() for c in df.columns]
        # CSVs carry duplicate lower/upper OHLC columns; keep first of each label.
        df = df.loc[:, ~df.columns.duplicated()]
        keep = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        df = df[keep]
        df = df.dropna(subset=['close']).sort_index().reset_index(drop=True)
        return df
    return None


def calc_atr(df, period=ATR_PERIOD):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df, period=ATR_PERIOD):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl       = high - low
    hc       = (high - close.shift()).abs()
    lc       = (low  - close.shift()).abs()
    tr       = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s    = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calc_chop(df, period=CHOP_PERIOD):
    """Choppiness Index: ~100 = choppy/range, ~0 = trending."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_sum  = tr.rolling(period).sum()
    high_max = df['high'].rolling(period).max()
    low_min  = df['low'].rolling(period).min()
    rng      = (high_max - low_min).replace(0, np.nan)
    return 100 * np.log10(atr_sum / rng) / np.log10(period)


def calc_max_dd_pct(equity):
    """Peak-to-trough drawdown as pct of the running peak.
    `equity` must include the notional starting balance so peaks never sit
    near zero (which would blow the ratio up)."""
    eq = np.array(equity, dtype=float)
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide='ignore', invalid='ignore'):
        dd_pct = np.where(peak > 0, (eq - peak) / peak, 0.0)
    return abs(dd_pct.min()) * 100


def pip_size(pair):
    return 0.01 if pair.endswith('JPY') else 0.0001


def spread_price(pair):
    """Spread in price units per the spec."""
    pip = pip_size(pair)
    if pair == 'USDJPY':
        return 0.3 * pip
    if pair.endswith('JPY'):
        return 0.5 * pip
    return 0.8 * pip


def precompute(df, pair):
    df = df.copy()
    df['atr']  = calc_atr(df)
    df['adx']  = calc_adx(df)
    df['ma']   = df['close'].rolling(MA_PERIOD).mean()
    df['chop'] = calc_chop(df)
    return df


def run_combo(df, pair, dc_period, tp_atr_mult, sl_atr_mult,
              adx_threshold, use_200ma, use_chop):
    spread = spread_price(pair)
    pip    = pip_size(pair)
    close  = df['close'].values
    high   = df['high'].values
    low    = df['low'].values
    atr    = df['atr'].values
    adx    = df['adx'].values
    ma     = df['ma'].values
    chop   = df['chop'].values

    # Donchian channel from the PRIOR dc_period bars (shifted to avoid lookahead).
    dc_high = df['high'].rolling(dc_period).max().shift(1).values
    dc_low  = df['low'].rolling(dc_period).min().shift(1).values

    n = len(df)
    warmup = max(dc_period + 1, MA_PERIOD, ATR_PERIOD, CHOP_PERIOD)

    trades = []          # realized pnl in price units (net of spread)
    equity = [INIT_EQ_PIPS]   # notional balance in pips (for DD% scaling)
    hold_hours = []

    in_pos = False
    cooldown_until = 0   # next bar index allowed to (re)enter

    i = warmup
    while i < n:
        if in_pos:
            i += 1
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(dc_high[i]) or not np.isfinite(dc_low[i]):
            i += 1
            continue

        long_sig  = close[i] > dc_high[i]
        short_sig = close[i] < dc_low[i]
        if not (long_sig or short_sig):
            i += 1
            continue
        direction = 1 if long_sig else -1

        # Filters
        if adx_threshold > 0 and (not np.isfinite(adx[i]) or adx[i] < adx_threshold):
            i += 1
            continue
        if use_200ma:
            if not np.isfinite(ma[i]):
                i += 1
                continue
            if direction == 1 and not close[i] > ma[i]:
                i += 1
                continue
            if direction == -1 and not close[i] < ma[i]:
                i += 1
                continue
        if use_chop:
            if not np.isfinite(chop[i]) or chop[i] >= 50:
                i += 1
                continue

        # --- Enter at this bar's close, pay half-spread on each side ---
        entry = close[i]
        if direction == 1:
            tp = entry + tp_atr_mult * a
            sl = entry - sl_atr_mult * a
        else:
            tp = entry - tp_atr_mult * a
            sl = entry + sl_atr_mult * a

        in_pos = True
        exit_price = None
        held = 0
        j = i + 1
        while j < n and held < MAX_HOLD_H:
            held += 1
            hi, lo = high[j], low[j]
            if direction == 1:
                hit_sl = lo <= sl
                hit_tp = hi >= tp
                # conservative: if both in same bar, assume SL first
                if hit_sl:
                    exit_price = sl
                    break
                if hit_tp:
                    exit_price = tp
                    break
            else:
                hit_sl = hi >= sl
                hit_tp = lo <= tp
                if hit_sl:
                    exit_price = sl
                    break
                if hit_tp:
                    exit_price = tp
                    break
            j += 1

        if exit_price is None:
            # time stop at bar j (or last bar)
            j = min(j, n - 1)
            exit_price = close[j]
            held = j - i

        gross = direction * (exit_price - entry)
        pnl   = gross - spread          # round-turn spread cost (price units)
        trades.append(pnl)
        equity.append(equity[-1] + pnl / pip)   # track equity in pips
        hold_hours.append(held)

        in_pos = False
        i = j + 1   # no overlapping entries; resume after exit bar

    if len(trades) < MIN_TRADES:
        return None

    trades = np.array(trades)
    wins   = trades[trades > 0]
    losses = trades[trades < 0]
    gross_win  = wins.sum()
    gross_loss = -losses.sum()
    pf = (gross_win / gross_loss) if gross_loss > 0 else (np.inf if gross_win > 0 else 0.0)
    win_rate = 100 * len(wins) / len(trades)
    max_dd = calc_max_dd_pct(equity)

    return {
        'pair':             pair,
        'dc_period':        dc_period,
        'tp_atr_mult':      tp_atr_mult,
        'sl_atr_mult':      sl_atr_mult,
        'adx_threshold':    adx_threshold,
        'use_200ma':        use_200ma,
        'use_chop':         use_chop,
        'total_trades':     len(trades),
        'win_rate':         round(win_rate, 2),
        'profit_factor':    round(pf, 3) if np.isfinite(pf) else 999.0,
        'max_drawdown_pct': round(max_dd, 2),
        'avg_hold_hours':   round(float(np.mean(hold_hours)), 1),
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    keys   = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    print(f'Pairs: {PAIRS}')
    print(f'Param combos per pair: {len(combos)}  (total {len(combos) * len(PAIRS)})')

    results = []
    for pair in PAIRS:
        df = load_csv(pair)
        if df is None:
            print(f'  [skip] {pair}: data not found')
            continue
        df = precompute(df, pair)
        span = ''
        try:
            idx = pd.to_datetime(pd.read_csv(os.path.join(DATA_DIR, f'{pair}_1h.csv'), index_col=0).index)
            span = f'{idx.min()} -> {idx.max()}'
        except Exception:
            pass
        print(f'  {pair}: {len(df)} bars  {span}')

        for combo in combos:
            params = dict(zip(keys, combo))
            r = run_combo(df, pair, **params)
            if r is not None:
                results.append(r)

    if not results:
        print('No results (insufficient trades everywhere).')
        return

    res_df = pd.DataFrame(results)
    res_df = res_df.sort_values('profit_factor', ascending=False).reset_index(drop=True)
    res_df.to_csv(OUTPUT_CSV, index=False)
    print(f'\nSaved {len(res_df)} rows -> {OUTPUT_CSV}')

    cols = ['pair', 'dc_period', 'tp_atr_mult', 'sl_atr_mult', 'adx_threshold',
            'use_200ma', 'use_chop', 'total_trades', 'win_rate',
            'profit_factor', 'max_drawdown_pct', 'avg_hold_hours']
    print('\n=== Top 10 by Profit Factor ===')
    with pd.option_context('display.width', 200, 'display.max_columns', None):
        print(res_df[cols].head(10).to_string(index=False))


if __name__ == '__main__':
    main()
