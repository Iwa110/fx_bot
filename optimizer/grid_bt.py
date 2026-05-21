"""
grid_bt.py  --  Grid Strategy Backtest  (Magic: 20260030)

Multi-pair grid strategy backtest.
AUDNZD is synthesized from AUDUSD_1h / NZDUSD_1h.
Quote-currency-to-JPY conversion applied per pair.

Output: optimizer/grid_bt_result.csv
        optimizer/grid_bt_equity_top5.png
"""

import itertools
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ---------------------------------------------------------------
# Paths
# ---------------------------------------------------------------
DATA_DIR   = Path(__file__).parent.parent / 'data'
OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'grid_bt_result.csv')
CHART_PATH = str(OUTPUT_DIR / 'grid_bt_equity_top5.png')

# ---------------------------------------------------------------
# Target pairs  (EURCAD / GBPCAD skipped: only ~1,500 rows)
# ---------------------------------------------------------------
PAIRS = [
    'AUDCAD', 'AUDJPY', 'AUDUSD', 'AUDNZD',
    'CHFJPY', 'EURGBP', 'EURJPY', 'EURUSD',
    'GBPAUD', 'GBPJPY', 'GBPUSD',
    'NZDJPY', 'NZDUSD',
    'USDCAD', 'USDCHF', 'USDJPY',
]

# Quote currency for each pair (determines JPY conversion)
QUOTE_CCY = {
    'AUDCAD': 'CAD', 'AUDJPY': 'JPY', 'AUDUSD': 'USD', 'AUDNZD': 'NZD',
    'CHFJPY': 'JPY', 'EURGBP': 'GBP', 'EURJPY': 'JPY', 'EURUSD': 'USD',
    'GBPAUD': 'AUD', 'GBPJPY': 'JPY', 'GBPUSD': 'USD',
    'NZDJPY': 'JPY', 'NZDUSD': 'USD',
    'USDCAD': 'CAD', 'USDCHF': 'CHF', 'USDJPY': 'JPY',
}

# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------
LOT_UNITS      = 1000      # 0.01 lot = 1,000 base currency units
DD_DAILY_JPY   = 5_000.0
DD_WEEKLY_JPY  = 15_000.0
ACCOUNT_SIZE   = 100_000.0
MIN_TRADES     = 30
EMA_SLOPE_TH   = 0.0005    # 0.05%/week for up/down vs flat

# ---------------------------------------------------------------
# Grid search parameters
# ---------------------------------------------------------------
GRID_PARAMS = {
    'grid_mult':  [0.2, 0.3, 0.4, 0.5],
    'max_levels': [3, 5, 7],
    'atr_th':     [1.2, 1.5, 2.0],
    'ema_filter': [True, False],
}

ADOPT_PF      = 1.3
ADOPT_DD_PCT  = 10.0
ADOPT_MIN_N   = 30


# ---------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------

def _load_csv(fname):
    """Load a single OHLC CSV from DATA_DIR. Returns DataFrame with DatetimeIndex."""
    path = DATA_DIR / fname
    if not path.exists():
        return None
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
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    keep = [c for c in ['open', 'high', 'low', 'close'] if c in df.columns]
    df = df[keep].dropna(subset=['close']).sort_index()
    df = df.loc[~df.index.duplicated(keep='first')]
    return df


def load_ref_rates():
    """
    Load reference JPY rates for quote-currency conversion.
    Returns dict: {'USD': Series, 'NZD': Series, 'GBP': Series,
                   'AUD': Series, 'CHF': Series, 'CAD': Series, 'JPY': None}
    CAD is synthesized as USDJPY / USDCAD.
    """
    refs = {}
    for ccy, fname in [
        ('USD', 'USDJPY_1h.csv'),
        ('NZD', 'NZDJPY_1h.csv'),
        ('GBP', 'GBPJPY_1h.csv'),
        ('AUD', 'AUDJPY_1h.csv'),
        ('CHF', 'CHFJPY_1h.csv'),
    ]:
        df = _load_csv(fname)
        refs[ccy] = df['close'] if df is not None else None

    # CAD: CADJPY = USDJPY / USDCAD
    usdjpy = refs.get('USD')
    usdcad = _load_csv('USDCAD_1h.csv')
    if usdjpy is not None and usdcad is not None:
        common = usdjpy.index.intersection(usdcad.index)
        cadjpy = (usdjpy.loc[common] / usdcad.loc[common, 'close']).replace(
            [np.inf, -np.inf], np.nan).ffill().bfill()
        refs['CAD'] = cadjpy
    else:
        refs['CAD'] = None

    refs['JPY'] = None  # no conversion needed
    return refs


def load_pair_data(pair, ref_rates):
    """
    Load OHLC data for a pair and the corresponding JPY conversion array.

    Returns:
        df          : DataFrame with DatetimeIndex (open/high/low/close)
        conv_arr    : np.ndarray of JPY/quote_ccy rates, aligned to df.index
    """
    quote = QUOTE_CCY[pair]

    if pair == 'AUDNZD':
        # Synthesize AUDNZD = AUDUSD / NZDUSD
        aud = _load_csv('AUDUSD_1h.csv')
        nzd = _load_csv('NZDUSD_1h.csv')
        if aud is None or nzd is None:
            return None, None
        common = aud.index.intersection(nzd.index)
        df = pd.DataFrame({
            'open':  aud.loc[common, 'open']  / nzd.loc[common, 'open'],
            'high':  aud.loc[common, 'high']  / nzd.loc[common, 'low'],
            'low':   aud.loc[common, 'low']   / nzd.loc[common, 'high'],
            'close': aud.loc[common, 'close'] / nzd.loc[common, 'close'],
        }, index=common)
        df = df.replace([np.inf, -np.inf], np.nan).dropna()
    else:
        df = _load_csv(f'{pair}_1h.csv')
        if df is None:
            return None, None

    if len(df) < 500:
        return None, None  # skip if data is too short

    # Build JPY conversion array
    if quote == 'JPY':
        conv_arr = np.ones(len(df))
    else:
        rate_series = ref_rates.get(quote)
        if rate_series is None:
            return None, None
        conv = rate_series.reindex(df.index, method='ffill').bfill()
        conv = conv.fillna(1.0).values
        conv_arr = conv

    return df, conv_arr


# ---------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------

def calc_atr14_h1(df):
    """ATR14 on 1h data. Returns np.ndarray."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean().values


def calc_atr14_d1(df):
    """
    ATR14 on D1 (resampled from 1h) + 90-day rolling mean.
    Returns (atr_d1_arr, avg90_arr) aligned to df.index.
    """
    daily = df.resample('D').agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'),
    ).dropna()
    hl = daily['high'] - daily['low']
    hc = (daily['high'] - daily['close'].shift()).abs()
    lc = (daily['low']  - daily['close'].shift()).abs()
    tr_d  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_d = tr_d.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean()
    avg90 = atr_d.rolling(90, min_periods=90).mean()
    s_d   = atr_d.reindex(df.index, method='ffill')
    s_avg = avg90.reindex(df.index, method='ffill')
    return s_d.values, s_avg.values


def calc_weekly_ema20_direction(df):
    """
    Weekly EMA20 slope direction mapped back to 1h index.
    Previous week's slope used to avoid lookahead.

    Returns np.ndarray of int:  1=up,  -1=down,  0=flat
    """
    weekly    = df['close'].resample('W').last().dropna()
    ema_w     = weekly.ewm(span=20, min_periods=5, adjust=False).mean()
    slope_pct = ema_w.diff() / ema_w
    slope_lag = slope_pct.shift(1)

    direction = pd.Series(0, index=slope_lag.index, dtype=int)
    direction[slope_lag >  EMA_SLOPE_TH] = 1
    direction[slope_lag < -EMA_SLOPE_TH] = -1

    dir_h = direction.reindex(df.index, method='ffill').fillna(0).astype(int)
    return dir_h.values


# ---------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------

def run_backtest(df, conv_arr, atr_h1, atr_d1, atr_d1_avg90, direction_h,
                 grid_mult, max_levels, atr_th, ema_filter):
    """
    Simulate grid strategy for a single parameter set.

    Returns (metrics_dict, equity_list) or (None, None) if n < MIN_TRADES.
    """
    n       = len(df)
    close_a = df['close'].values
    warmup  = 50

    open_longs  = []   # (entry_price, tp_price)
    open_shorts = []   # (entry_price, tp_price)

    equity            = 0.0
    equity_list       = [0.0]
    trades            = []

    day_equity_start  = 0.0
    week_equity_start = 0.0
    day_stop          = False
    week_stop         = False
    prev_day          = None
    prev_week         = None

    for i in range(warmup, n):
        c   = close_a[i]
        cnv = conv_arr[i]           # JPY per quote currency unit
        gw  = atr_h1[i] * grid_mult

        if np.isnan(c) or np.isnan(gw) or gw <= 0:
            continue

        ts        = df.index[i]
        curr_day  = ts.date()
        iso       = ts.isocalendar()
        curr_week = (iso[0], iso[1])

        # Day / week boundary reset
        if curr_day != prev_day:
            day_equity_start = equity
            day_stop         = False
            prev_day         = curr_day
        if curr_week != prev_week:
            week_equity_start = equity
            week_stop         = False
            prev_week         = curr_week

        # ---- TP check (runs even while stopped) ----
        rem_l = []
        for entry, tp in open_longs:
            if c >= tp:
                pnl = (tp - entry) * LOT_UNITS * cnv
                trades.append(pnl)
                equity += pnl
            else:
                rem_l.append((entry, tp))
        open_longs = rem_l

        rem_s = []
        for entry, tp in open_shorts:
            if c <= tp:
                pnl = (entry - tp) * LOT_UNITS * cnv
                trades.append(pnl)
                equity += pnl
            else:
                rem_s.append((entry, tp))
        open_shorts = rem_s

        equity_list.append(equity)

        # ---- DD stop update ----
        if equity - day_equity_start < -DD_DAILY_JPY:
            day_stop = True
        if equity - week_equity_start < -DD_WEEKLY_JPY:
            week_stop = True

        if day_stop or week_stop:
            continue

        # ---- Volatility filter ----
        if not (np.isnan(atr_d1[i]) or np.isnan(atr_d1_avg90[i])):
            if atr_d1[i] > atr_d1_avg90[i] * atr_th:
                continue

        # ---- Direction ----
        dir_val   = direction_h[i]
        if ema_filter:
            can_long  = (dir_val >= 0)
            can_short = (dir_val <= 0)
        else:
            can_long  = True
            can_short = True

        # ---- Grid entries ----
        if can_long:
            if len(open_longs) == 0:
                open_longs.append((c, c + gw))
            elif len(open_longs) < max_levels:
                lowest = min(e for e, _ in open_longs)
                if c <= lowest - gw:
                    open_longs.append((c, c + gw))

        if can_short:
            if len(open_shorts) == 0:
                open_shorts.append((c, c - gw))
            elif len(open_shorts) < max_levels:
                highest = max(e for e, _ in open_shorts)
                if c >= highest + gw:
                    open_shorts.append((c, c - gw))

    # Force-close remaining positions at final bar price
    c_last  = close_a[-1]
    cnv_last = conv_arr[-1]
    for entry, tp in open_longs:
        pnl = (c_last - entry) * LOT_UNITS * cnv_last
        trades.append(pnl)
        equity += pnl
    for entry, tp in open_shorts:
        pnl = (entry - c_last) * LOT_UNITS * cnv_last
        trades.append(pnl)
        equity += pnl
    equity_list.append(equity)

    if len(trades) < MIN_TRADES:
        return None, None

    arr  = np.array(trades)
    wins = arr[arr > 0]
    loss = arr[arr <= 0]
    gp   = float(wins.sum())  if len(wins) > 0 else 0.0
    gl   = float(abs(loss.sum())) if len(loss) > 0 else 0.0
    pf   = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)

    eq_arr = np.array(equity_list)
    peak   = np.maximum.accumulate(eq_arr)
    dd_abs = float((eq_arr - peak).min())
    dd_pct = abs(dd_abs) / ACCOUNT_SIZE * 100.0

    # Sharpe: annualised from daily-window PnL (24h proxy)
    eq_s  = pd.Series(equity_list)
    dp    = eq_s.diff(24).dropna()
    sharpe = (dp.mean() / dp.std() * np.sqrt(365)) if dp.std() > 0 else 0.0

    metrics = {
        'PF':         round(pf, 3),
        'WR':         round(len(wins) / len(arr), 3),
        'n_trades':   int(len(arr)),
        'max_dd_pct': round(dd_pct, 2),
        'sharpe':     round(float(sharpe), 3),
    }
    return metrics, equity_list


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    print('[INFO] Loading reference JPY rates...')
    ref_rates = load_ref_rates()
    for ccy, s in ref_rates.items():
        if s is not None:
            print(f'  {ccy}/JPY  avg={s.mean():.2f}  n={len(s)}')
        else:
            print(f'  JPY  (no conversion)')

    combos = list(itertools.product(
        GRID_PARAMS['grid_mult'],
        GRID_PARAMS['max_levels'],
        GRID_PARAMS['atr_th'],
        GRID_PARAMS['ema_filter'],
    ))
    total_runs = len(PAIRS) * len(combos)
    print(f'\n[INFO] {len(PAIRS)} pairs x {len(combos)} combos = {total_runs} runs\n')

    all_results  = []
    equity_cache = {}   # (pair, gm, ml, at, ef) -> equity_list
    done         = 0

    for pair in PAIRS:
        df, conv_arr = load_pair_data(pair, ref_rates)
        if df is None:
            print(f'[SKIP] {pair}: data unavailable')
            done += len(combos)
            continue

        atr_h1              = calc_atr14_h1(df)
        atr_d1, atr_d1_avg90 = calc_atr14_d1(df)
        direction_h         = calc_weekly_ema20_direction(df)

        up   = int((direction_h == 1).sum())
        flat = int((direction_h == 0).sum())
        down = int((direction_h == -1).sum())
        print(f'[{pair:8s}] bars={len(df)}  '
              f'close={df["close"].iloc[-1]:.5f}  '
              f'dir: up={up} flat={flat} dn={down}')

        for gm, ml, at, ef in combos:
            done += 1
            if done % 200 == 0:
                print(f'  ... {done}/{total_runs}')

            m, eq_list = run_backtest(
                df, conv_arr, atr_h1, atr_d1, atr_d1_avg90, direction_h,
                grid_mult=gm, max_levels=int(ml), atr_th=at, ema_filter=ef,
            )
            if m is None:
                continue

            row = {
                'pair':       pair,
                'grid_mult':  gm,
                'max_levels': int(ml),
                'atr_th':     at,
                'ema_filter': ef,
                **m,
            }
            all_results.append(row)
            equity_cache[(pair, gm, int(ml), at, ef)] = eq_list

    if not all_results:
        print('[WARN] No results produced.')
        return

    result_df = pd.DataFrame(all_results)
    result_df = result_df.sort_values('PF', ascending=False).reset_index(drop=True)
    result_df.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[INFO] Results saved: {OUTPUT_CSV}  ({len(result_df)} rows)')

    # ---- Per-pair best summary ----
    print('\n=== Best PF per pair ===')
    best = result_df.groupby('pair').first().reset_index()[
        ['pair', 'grid_mult', 'max_levels', 'atr_th', 'ema_filter',
         'PF', 'WR', 'n_trades', 'max_dd_pct', 'sharpe']
    ]
    pd.set_option('display.width', 140)
    pd.set_option('display.max_columns', 20)
    print(best.to_string(index=False))

    # ---- Qualifying runs ----
    qual = result_df[
        (result_df['PF']         >= ADOPT_PF) &
        (result_df['max_dd_pct'] <= ADOPT_DD_PCT) &
        (result_df['n_trades']   >= ADOPT_MIN_N)
    ]
    print(f'\n=== Qualifying (PF>={ADOPT_PF}, DD<={ADOPT_DD_PCT}%, n>={ADOPT_MIN_N}) ===')
    if qual.empty:
        print('  (none)')
    else:
        print(qual.to_string(index=False))

    # ---- Top-5 equity curves (across all pairs) ----
    top5    = result_df.head(5)
    n_plots = min(5, len(top5))
    fig, axes = plt.subplots(n_plots, 1, figsize=(13, 3.5 * n_plots))
    if n_plots == 1:
        axes = [axes]

    for ax, (_, row) in zip(axes, top5.iterrows()):
        key = (row['pair'], row['grid_mult'], int(row['max_levels']),
               row['atr_th'], row['ema_filter'])
        eq  = equity_cache.get(key)
        if eq is None:
            continue
        ax.plot(eq, linewidth=0.8)
        ax.set_title(
            f"{row['pair']}  gm={row['grid_mult']} lv={int(row['max_levels'])} "
            f"atr={row['atr_th']} ema={row['ema_filter']}  |  "
            f"PF={row['PF']}  WR={row['WR']}  n={int(row['n_trades'])}  "
            f"DD={row['max_dd_pct']}%  Sharpe={row['sharpe']}",
            fontsize=8,
        )
        ax.set_ylabel('Equity (JPY)')
        ax.axhline(0, color='gray', linewidth=0.5, linestyle='--')
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHART_PATH, dpi=100)
    print(f'\n[INFO] Equity chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
