"""
grid_bt.py  --  AUDNZD Grid Strategy Backtest  (Magic: 20260030)

AUDNZD is synthesized from AUDUSD_1h and NZDUSD_1h.
Weekly EMA20 slope determines trade direction.
Grid width = ATR(H1, 14) x grid_mult.

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
# Constants
# ---------------------------------------------------------------
LOT_UNITS      = 1000      # 0.01 lot = 1000 units (base = AUD)
DD_DAILY_JPY   = 5_000.0   # realized daily DD stop
DD_WEEKLY_JPY  = 15_000.0  # realized weekly DD stop
ACCOUNT_SIZE   = 100_000.0 # for DD% calc
MIN_TRADES     = 30
EMA_SLOPE_TH   = 0.0005    # 0.05%/week threshold for up/down vs flat

# ---------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------
GRID_PARAMS = {
    'grid_mult':  [0.2, 0.3, 0.4, 0.5],
    'max_levels': [3, 5, 7],
    'atr_th':     [1.2, 1.5, 2.0],
    'ema_filter': [True, False],
}

# Adoption criteria
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
    # Strip timezone if present
    try:
        df.index = df.index.tz_convert(None)
    except Exception:
        try:
            df.index = df.index.tz_localize(None)
        except Exception:
            pass
    df.columns = [c.lower() for c in df.columns]
    # Remove duplicate column names (e.g. open/Open pair)
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    keep = [c for c in ['open', 'high', 'low', 'close'] if c in df.columns]
    df = df[keep].dropna(subset=['close']).sort_index()
    df = df.loc[~df.index.duplicated(keep='first')]
    return df


def load_data():
    """
    Synthesize AUDNZD 1h from AUDUSD / NZDUSD.
    Also load NZDJPY for JPY PnL conversion.

    Returns:
        df_audnzd : DataFrame with DatetimeIndex, columns open/high/low/close
        nzdjpy_arr: np.ndarray aligned to df_audnzd.index
    """
    aud = _load_csv('AUDUSD_1h.csv')
    nzd = _load_csv('NZDUSD_1h.csv')
    jpy = _load_csv('NZDJPY_1h.csv')

    if aud is None or nzd is None:
        raise FileNotFoundError('AUDUSD_1h.csv or NZDUSD_1h.csv not found in data/')

    # Intersect on common timestamps
    common = aud.index.intersection(nzd.index)
    if len(common) == 0:
        raise ValueError('No overlapping timestamps between AUDUSD and NZDUSD')

    au = aud.loc[common]
    nz = nzd.loc[common]

    # Synthesize AUDNZD = AUDUSD / NZDUSD
    # Conservative H/L: buying AUDNZD high requires AUDUSD high and NZDUSD low
    audnzd = pd.DataFrame({
        'open':  au['open']  / nz['open'],
        'high':  au['high']  / nz['low'],
        'low':   au['low']   / nz['high'],
        'close': au['close'] / nz['close'],
    }, index=common)
    audnzd = audnzd.replace([np.inf, -np.inf], np.nan).dropna()

    # NZDJPY for conversion; fall back to fixed 86 if unavailable
    if jpy is not None:
        nzdjpy = jpy['close'].reindex(audnzd.index, method='ffill').bfill()
    else:
        nzdjpy = pd.Series(86.0, index=audnzd.index)
    nzdjpy = nzdjpy.fillna(86.0)

    return audnzd, nzdjpy.values


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
    ATR14 on D1 (resampled from 1h), plus 90-day rolling mean.
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
    # Map back to 1h; ffill so each hourly bar uses that day's D1 ATR
    s_d   = atr_d.reindex(df.index, method='ffill')
    s_avg = avg90.reindex(df.index, method='ffill')
    return s_d.values, s_avg.values


def calc_weekly_ema20_direction(df):
    """
    Weekly EMA20 slope direction mapped to 1h index.
    Uses previous week's slope to avoid lookahead.

    Returns np.ndarray of int:  1 = up,  -1 = down,  0 = flat
    """
    weekly    = df['close'].resample('W').last().dropna()
    ema_w     = weekly.ewm(span=20, min_periods=5, adjust=False).mean()
    slope_pct = ema_w.diff() / ema_w            # fractional change per week
    slope_lag = slope_pct.shift(1)              # use prev week (no lookahead)

    direction = pd.Series(0, index=slope_lag.index, dtype=int)
    direction[slope_lag >  EMA_SLOPE_TH] = 1
    direction[slope_lag < -EMA_SLOPE_TH] = -1

    dir_h = direction.reindex(df.index, method='ffill').fillna(0).astype(int)
    return dir_h.values


# ---------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------

def run_backtest(df, nzdjpy_arr, atr_h1, atr_d1, atr_d1_avg90, direction_h,
                 grid_mult, max_levels, atr_th, ema_filter):
    """
    Simulate AUDNZD grid strategy.

    Grid logic:
      - Long:  add when close drops >= grid_width below lowest current long entry
      - Short: add when close rises >= grid_width above highest current short entry
      - First position (when empty) opens immediately
      - TP = entry +/- grid_width  (no SL; capped at max_levels)

    Stops (new order only):
      - ATR(D1) > avg90 * atr_th  --> volatility stop
      - Realized daily DD > 5,000 JPY
      - Realized weekly DD > 15,000 JPY

    Returns (metrics_dict, equity_list) or (None, None) if n < MIN_TRADES.
    """
    n       = len(df)
    close_a = df['close'].values
    warmup  = 50  # H1 ATR14 warm-up; NaN checks handle D1/weekly

    # --- State ---
    open_longs  = []    # list of (entry_price, tp_price)
    open_shorts = []    # list of (entry_price, tp_price)

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
        nzj = nzdjpy_arr[i]
        gw  = atr_h1[i] * grid_mult

        if np.isnan(c) or np.isnan(gw) or gw <= 0:
            continue

        # Day / week boundary
        ts        = df.index[i]
        curr_day  = ts.date()
        iso       = ts.isocalendar()
        curr_week = (iso[0], iso[1])

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
                pnl = (tp - entry) * LOT_UNITS * nzj
                trades.append(pnl)
                equity += pnl
            else:
                rem_l.append((entry, tp))
        open_longs = rem_l

        rem_s = []
        for entry, tp in open_shorts:
            if c <= tp:
                pnl = (entry - tp) * LOT_UNITS * nzj
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

        # ---- New order gating ----
        if day_stop or week_stop:
            continue

        # Volatility filter
        if not (np.isnan(atr_d1[i]) or np.isnan(atr_d1_avg90[i])):
            if atr_d1[i] > atr_d1_avg90[i] * atr_th:
                continue

        # Direction
        dir_val   = direction_h[i]
        if ema_filter:
            can_long  = (dir_val >= 0)   # up or flat
            can_short = (dir_val <= 0)   # down or flat
        else:
            can_long  = True
            can_short = True

        # ---- Grid entry: Long ----
        if can_long:
            if len(open_longs) == 0:
                open_longs.append((c, c + gw))
            elif len(open_longs) < max_levels:
                lowest = min(e for e, _ in open_longs)
                if c <= lowest - gw:
                    open_longs.append((c, c + gw))

        # ---- Grid entry: Short ----
        if can_short:
            if len(open_shorts) == 0:
                open_shorts.append((c, c - gw))
            elif len(open_shorts) < max_levels:
                highest = max(e for e, _ in open_shorts)
                if c >= highest + gw:
                    open_shorts.append((c, c - gw))

    # Close remaining positions at final bar price (reflected in equity)
    c_last   = close_a[-1]
    nzj_last = nzdjpy_arr[-1]
    for entry, tp in open_longs:
        pnl = (c_last - entry) * LOT_UNITS * nzj_last
        trades.append(pnl)
        equity += pnl
    for entry, tp in open_shorts:
        pnl = (entry - c_last) * LOT_UNITS * nzj_last
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

    # Max drawdown (peak-to-trough, % of account)
    eq_arr = np.array(equity_list)
    peak   = np.maximum.accumulate(eq_arr)
    dd_abs = float((eq_arr - peak).min())   # <= 0
    dd_pct = abs(dd_abs) / ACCOUNT_SIZE * 100.0

    # Annualised Sharpe (daily PnL to avoid zero-inflation from hourly)
    eq_s = pd.Series(equity_list)
    # Use roughly 24-bar windows as "daily" proxy
    daily_pnl = eq_s.diff(24).dropna()
    mean_d = daily_pnl.mean()
    std_d  = daily_pnl.std()
    sharpe = (mean_d / std_d * np.sqrt(365)) if std_d > 0 else 0.0

    metrics = {
        'PF':         round(pf, 3),
        'WR':         round(len(wins) / len(arr), 3),
        'n_trades':   int(len(arr)),
        'max_dd_pct': round(dd_pct, 2),
        'sharpe':     round(sharpe, 3),
    }
    return metrics, equity_list


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    print('[INFO] Loading AUDNZD (synthesized from AUDUSD/NZDUSD)...')
    df, nzdjpy_arr = load_data()
    print(f'[INFO] AUDNZD: {len(df)} bars  '
          f'{df.index[0].date()} -> {df.index[-1].date()}')
    print(f'[INFO] AUDNZD close range: '
          f'{df["close"].min():.5f} - {df["close"].max():.5f}')
    print(f'[INFO] NZDJPY avg: {nzdjpy_arr.mean():.2f}')

    print('[INFO] Computing indicators...')
    atr_h1              = calc_atr14_h1(df)
    atr_d1, atr_d1_avg90 = calc_atr14_d1(df)
    direction_h         = calc_weekly_ema20_direction(df)
    print(f'[INFO] Weekly direction dist  up={( direction_h==1).sum()}  '
          f'flat={(direction_h==0).sum()}  down={(direction_h==-1).sum()}')

    combos = list(itertools.product(
        GRID_PARAMS['grid_mult'],
        GRID_PARAMS['max_levels'],
        GRID_PARAMS['atr_th'],
        GRID_PARAMS['ema_filter'],
    ))
    total = len(combos)
    print(f'[INFO] {total} parameter combinations\n')

    all_results  = []
    equity_cache = {}

    for idx, (gm, ml, at, ef) in enumerate(combos):
        if (idx + 1) % 12 == 0 or idx == 0:
            print(f'  progress: {idx + 1}/{total}')

        m, eq_list = run_backtest(
            df, nzdjpy_arr, atr_h1, atr_d1, atr_d1_avg90, direction_h,
            grid_mult=gm, max_levels=int(ml), atr_th=at, ema_filter=ef,
        )
        if m is None:
            continue

        row = {
            'grid_mult':  gm,
            'max_levels': int(ml),
            'atr_th':     at,
            'ema_filter': ef,
            **m,
        }
        all_results.append(row)
        equity_cache[(gm, int(ml), at, ef)] = eq_list

    if not all_results:
        print('[WARN] No results produced. Check data availability.')
        return

    result_df = pd.DataFrame(all_results)
    result_df = result_df.sort_values('PF', ascending=False).reset_index(drop=True)
    result_df.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[INFO] Results saved: {OUTPUT_CSV}  ({len(result_df)} rows)')

    # ---- Print qualifying runs ----
    qual = result_df[
        (result_df['PF']         >= ADOPT_PF) &
        (result_df['max_dd_pct'] <= ADOPT_DD_PCT) &
        (result_df['n_trades']   >= ADOPT_MIN_N)
    ]
    print(f'\n=== Qualifying (PF>={ADOPT_PF}, DD<={ADOPT_DD_PCT}%, n>={ADOPT_MIN_N}) ===')
    if qual.empty:
        print('  (none)')
    else:
        pd.set_option('display.width', 120)
        pd.set_option('display.max_columns', 20)
        print(qual.to_string(index=False))

    # ---- Top-5 summary ----
    print('\n=== Top 5 by PF ===')
    print(result_df.head(5).to_string(index=False))

    # ---- Equity curves (top 5) ----
    top5 = result_df.head(5)
    n_plots = min(5, len(top5))
    fig, axes = plt.subplots(n_plots, 1, figsize=(13, 3.5 * n_plots))
    if n_plots == 1:
        axes = [axes]

    for ax, (_, row) in zip(axes, top5.iterrows()):
        key = (row['grid_mult'], int(row['max_levels']), row['atr_th'], row['ema_filter'])
        eq  = equity_cache.get(key)
        if eq is None:
            continue
        ax.plot(eq, linewidth=0.8)
        ax.set_title(
            f'grid_mult={row["grid_mult"]}  max_levels={int(row["max_levels"])}  '
            f'atr_th={row["atr_th"]}  ema_filter={row["ema_filter"]}  |  '
            f'PF={row["PF"]}  WR={row["WR"]}  n={int(row["n_trades"])}  '
            f'DD={row["max_dd_pct"]}%  Sharpe={row["sharpe"]}',
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
