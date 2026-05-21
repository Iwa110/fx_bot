"""
grid_bt_v2.py  --  NZDUSD Grid Strategy v2  (Magic: 20260030)

出口戦略 A/B/C + レンジフィルター 3種 の比較バックテスト。

出口戦略:
  A: ADX(D1) > 25 で全ポジ即時決済
  B: max_levels 到達から N 時間経過で全ポジ決済 (N=24/48/72)
  C: 含み損 > 証拠金合計 × X% で全ポジ決済 (X=10/20/30)

レンジフィルター (エントリー条件):
  ci     : Choppiness Index(D1,14) > 61.8
  adx    : ADX(D1,14) < 25 AND ADX の3日傾き <= 0
  relaltr: ATR(H1,14) / ATR(H1,14).rolling(20).mean() < 1.1

Output: optimizer/grid_bt_v2_result.csv
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
OUTPUT_CSV = str(OUTPUT_DIR / 'grid_bt_v2_result.csv')
CHART_PATH = str(OUTPUT_DIR / 'grid_bt_v2_equity_top5.png')

# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------
PAIR       = 'NZDUSD'
LOT_UNITS  = 1000       # 0.01 lot = 1,000 base units (NZD)
LEVERAGE   = 25
ACCOUNT    = 100_000.0  # for DD% denominator (JPY)
MIN_TRADES = 20

DD_DAILY_JPY  = 5_000.0
DD_WEEKLY_JPY = 15_000.0

# ---------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------
GRID_MULT_LIST  = [0.5, 1.0, 1.5, 2.0]
MAX_LEVELS_LIST = [3, 5, 7]

# Exit strategies: ('A', None) / ('B', n_hours) / ('C', x_pct)
EXIT_CONFIGS = [
    ('A', None),
    ('B', 24), ('B', 48), ('B', 72),
    ('C', 10),  ('C', 20),  ('C', 30),
]

# Range filter combinations (use_ci, use_adx, use_relaltr)
RF_CONFIGS = {
    'none':    (False, False, False),
    'ci':      (True,  False, False),
    'adx':     (False, True,  False),
    'all':     (True,  True,  True),
}

ADOPT_PF      = 1.3
ADOPT_DD_PCT  = 15.0
ADOPT_MIN_N   = 20


# ---------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------

def load_data():
    """Load NZDUSD 1h + NZDJPY conversion. Returns (df, conv_arr)."""
    def _load(fname):
        path = DATA_DIR / fname
        df   = pd.read_csv(path, index_col=0)
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
        return df[['open', 'high', 'low', 'close']].dropna().sort_index()

    df  = _load('NZDUSD_1h.csv')
    jpy = _load('NZDJPY_1h.csv')

    conv = jpy['close'].reindex(df.index, method='ffill').bfill().fillna(89.0)
    return df, conv.values


# ---------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------

def calc_atr14_h1(df):
    """ATR14 on H1. Returns np.ndarray."""
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean().values


def calc_rel_atr_h1(df):
    """ATR14(H1) / rolling(20).mean()  -- relative ATR."""
    atr_s = pd.Series(calc_atr14_h1(df))
    avg20 = atr_s.rolling(20, min_periods=20).mean()
    rel   = (atr_s / avg20.replace(0, np.nan)).values
    return rel


def _daily_ohlc(df):
    return df.resample('D').agg(
        open  =('open',  'first'),
        high  =('high',  'max'),
        low   =('low',   'min'),
        close =('close', 'last'),
    ).dropna()


def calc_adx14_d1(df):
    """
    ADX14 on D1 + 3-day slope, mapped to H1 index.
    Returns (adx_h1_arr, slope_h1_arr).
    """
    daily    = _daily_ohlc(df)
    high_s   = daily['high']
    low_s    = daily['low']
    close_s  = daily['close']

    plus_dm  = high_s.diff()
    minus_dm = low_s.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    hl = high_s - low_s
    hc = (high_s - close_s.shift()).abs()
    lc = (low_s  - close_s.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)

    atr_s    = tr.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm( alpha=1.0/14, min_periods=14, adjust=False).mean() \
               / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean() \
               / atr_s.replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx      = dx.ewm(alpha=1.0/14, min_periods=14, adjust=False).mean()

    # 3-day slope (diff over 3 periods / 3)
    slope    = adx.diff(3) / 3.0

    adx_h    = adx.reindex(df.index,   method='ffill')
    slope_h  = slope.reindex(df.index, method='ffill')
    return adx_h.values, slope_h.values


def calc_choppiness_d1(df, period=14):
    """
    Choppiness Index(D1, period) mapped to H1 index.
    CI = 100 * log10(sum_TR(n) / (High_n - Low_n)) / log10(n)
    CI > 61.8  --> range (use grid)
    CI < 38.2  --> trend (avoid)
    """
    daily   = _daily_ohlc(df)
    hl      = daily['high'] - daily['low']
    hc      = (daily['high'] - daily['close'].shift()).abs()
    lc      = (daily['low']  - daily['close'].shift()).abs()
    tr      = pd.concat([hl, hc, lc], axis=1).max(axis=1)

    sum_tr  = tr.rolling(period, min_periods=period).sum()
    high_n  = daily['high'].rolling(period, min_periods=period).max()
    low_n   = daily['low'].rolling(period, min_periods=period).min()
    rng     = (high_n - low_n).replace(0, np.nan)

    ci      = 100.0 * np.log10(sum_tr / rng) / np.log10(period)
    ci_h    = ci.reindex(df.index, method='ffill')
    return ci_h.values


def build_indicators(df):
    """Compute and cache all indicators for one pair."""
    print('  computing indicators...', end=' ', flush=True)
    adx_h1, adx_slope_h1 = calc_adx14_d1(df)
    ind = {
        'atr_h1':      calc_atr14_h1(df),
        'rel_atr_h1':  calc_rel_atr_h1(df),
        'adx_d1':      adx_h1,
        'adx_slope_d1': adx_slope_h1,
        'ci_d1':       calc_choppiness_d1(df),
    }
    print('done')
    return ind


# ---------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------

def _unrealized_jpy(open_longs, open_shorts, c, cnv):
    """Total unrealized PnL across all open positions in JPY."""
    total = 0.0
    for entry, tp, _ in open_longs:
        total += (c - entry) * LOT_UNITS * cnv
    for entry, tp, _ in open_shorts:
        total += (entry - c) * LOT_UNITS * cnv
    return total


def _total_margin_jpy(open_longs, open_shorts, cnv):
    """Total margin required for all open positions in JPY."""
    total = 0.0
    for entry, tp, _ in open_longs:
        total += LOT_UNITS * entry * cnv / LEVERAGE
    for entry, tp, _ in open_shorts:
        total += LOT_UNITS * entry * cnv / LEVERAGE
    return total


def run_backtest(df, conv_arr, ind,
                 grid_mult, max_levels,
                 exit_type, exit_param,
                 use_ci, use_adx, use_relaltr,
                 ci_threshold=61.8):
    """
    Single backtest run.

    exit_type : 'A' | 'B' | 'C'
    exit_param: None (A) | n_hours (B) | x_pct (C)

    Returns metrics dict or None if n < MIN_TRADES.
    """
    n        = len(df)
    close_a  = df['close'].values
    warmup   = 100

    # Positions: (entry_price, tp_price, entry_bar_idx)
    open_longs  = []
    open_shorts = []

    equity     = 0.0
    eq_list    = [0.0]
    trades     = []     # (pnl_jpy, hold_hours)

    # Exit B: timer tracking
    max_lv_bar_long  = None   # bar index when longs reached max_levels
    max_lv_bar_short = None

    day_eq_start = week_eq_start = 0.0
    day_stop = week_stop = False
    prev_day = prev_week = None

    for i in range(warmup, n):
        c   = close_a[i]
        cnv = conv_arr[i]
        gw  = ind['atr_h1'][i] * grid_mult

        if np.isnan(c) or np.isnan(gw) or gw <= 0:
            continue

        ts        = df.index[i]
        curr_day  = ts.date()
        iso       = ts.isocalendar()
        curr_week = (iso[0], iso[1])

        # Day / week boundary
        if curr_day != prev_day:
            day_eq_start = equity; day_stop  = False; prev_day  = curr_day
        if curr_week != prev_week:
            week_eq_start = equity; week_stop = False; prev_week = curr_week

        # ---- Exit strategy A/B/C ----
        force_long = force_short = False

        if exit_type == 'A':
            adx_v = ind['adx_d1'][i]
            if not np.isnan(adx_v) and adx_v > 25.0:
                force_long = force_short = True

        elif exit_type == 'B':
            n_h = int(exit_param)
            if max_lv_bar_long  is not None and len(open_longs)  >= max_levels \
                    and (i - max_lv_bar_long)  >= n_h:
                force_long = True
            if max_lv_bar_short is not None and len(open_shorts) >= max_levels \
                    and (i - max_lv_bar_short) >= n_h:
                force_short = True

        elif exit_type == 'C':
            x_pct = float(exit_param)
            if open_longs or open_shorts:
                unr = _unrealized_jpy(open_longs, open_shorts, c, cnv)
                mrg = _total_margin_jpy(open_longs, open_shorts, cnv)
                if mrg > 0 and unr < -(mrg * x_pct / 100.0):
                    force_long = force_short = True

        # Process forced exits
        if force_long and open_longs:
            for entry, tp, eb in open_longs:
                pnl = (c - entry) * LOT_UNITS * cnv
                trades.append((pnl, i - eb))
                equity += pnl
            open_longs = []
            max_lv_bar_long = None

        if force_short and open_shorts:
            for entry, tp, eb in open_shorts:
                pnl = (entry - c) * LOT_UNITS * cnv
                trades.append((pnl, i - eb))
                equity += pnl
            open_shorts = []
            max_lv_bar_short = None

        # ---- TP check ----
        rem_l = []
        for entry, tp, eb in open_longs:
            if c >= tp:
                pnl = (tp - entry) * LOT_UNITS * cnv
                trades.append((pnl, i - eb))
                equity += pnl
            else:
                rem_l.append((entry, tp, eb))
        open_longs = rem_l

        rem_s = []
        for entry, tp, eb in open_shorts:
            if c <= tp:
                pnl = (entry - tp) * LOT_UNITS * cnv
                trades.append((pnl, i - eb))
                equity += pnl
            else:
                rem_s.append((entry, tp, eb))
        open_shorts = rem_s

        # Reset Exit B timer if count dropped below max_levels
        if len(open_longs)  < max_levels:
            max_lv_bar_long  = None
        if len(open_shorts) < max_levels:
            max_lv_bar_short = None

        eq_list.append(equity)

        # ---- Daily / weekly DD stop ----
        if equity - day_eq_start  < -DD_DAILY_JPY:  day_stop  = True
        if equity - week_eq_start < -DD_WEEKLY_JPY: week_stop = True

        if day_stop or week_stop:
            continue

        # ---- Range filters ----
        ok = True
        if ok and use_ci:
            ci_v = ind['ci_d1'][i]
            if not np.isnan(ci_v) and ci_v <= ci_threshold:
                ok = False
        if ok and use_adx:
            adx_v  = ind['adx_d1'][i]
            slp_v  = ind['adx_slope_d1'][i]
            if not (np.isnan(adx_v) or np.isnan(slp_v)):
                if adx_v >= 25.0 or slp_v > 0:
                    ok = False
        if ok and use_relaltr:
            ra = ind['rel_atr_h1'][i]
            if not np.isnan(ra) and ra >= 1.1:
                ok = False

        if not ok:
            continue

        # ---- Grid entries ----
        # Long (buy-the-dip)
        if len(open_longs) == 0:
            open_longs.append((c, c + gw, i))
        elif len(open_longs) < max_levels:
            lowest = min(e for e, _, _ in open_longs)
            if c <= lowest - gw:
                open_longs.append((c, c + gw, i))
                if len(open_longs) >= max_levels and max_lv_bar_long is None:
                    max_lv_bar_long = i

        # Short (sell-the-rally)
        if len(open_shorts) == 0:
            open_shorts.append((c, c - gw, i))
        elif len(open_shorts) < max_levels:
            highest = max(e for e, _, _ in open_shorts)
            if c >= highest + gw:
                open_shorts.append((c, c - gw, i))
                if len(open_shorts) >= max_levels and max_lv_bar_short is None:
                    max_lv_bar_short = i

    # Force-close remaining at final bar
    c_last  = close_a[-1]
    cnv_last = conv_arr[-1]
    for entry, tp, eb in open_longs:
        pnl = (c_last - entry) * LOT_UNITS * cnv_last
        trades.append((pnl, n - 1 - eb))
        equity += pnl
    for entry, tp, eb in open_shorts:
        pnl = (entry - c_last) * LOT_UNITS * cnv_last
        trades.append((pnl, n - 1 - eb))
        equity += pnl
    eq_list.append(equity)

    if len(trades) < MIN_TRADES:
        return None, None

    pnls   = np.array([t[0] for t in trades])
    htimes = np.array([t[1] for t in trades], dtype=float)

    wins = pnls[pnls > 0]
    loss = pnls[pnls <= 0]
    gp   = float(wins.sum())  if len(wins) > 0 else 0.0
    gl   = float(abs(loss.sum())) if len(loss) > 0 else 0.0
    pf   = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)

    eq_arr = np.array(eq_list)
    peak   = np.maximum.accumulate(eq_arr)
    dd_pct = abs(float((eq_arr - peak).min())) / ACCOUNT * 100.0

    metrics = {
        'PF':           round(pf, 3),
        'WR':           round(len(wins) / len(pnls), 3),
        'n_trades':     int(len(pnls)),
        'total_pnl_jpy': int(round(float(pnls.sum()), 0)),
        'max_dd_pct':   round(dd_pct, 2),
        'avg_hold_h':   round(float(htimes.mean()), 1),
    }
    return metrics, eq_list


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    print(f'[INFO] Loading {PAIR}...')
    df, conv_arr = load_data()
    print(f'[INFO] {len(df)} bars  {df.index[0].date()} -> {df.index[-1].date()}')

    ind = build_indicators(df)

    # Print indicator stats for sanity check
    ci_valid = ind['ci_d1'][~np.isnan(ind['ci_d1'])]
    adx_valid = ind['adx_d1'][~np.isnan(ind['adx_d1'])]
    print(f'[INFO] CI(D1,14):  mean={ci_valid.mean():.1f}  '
          f'>61.8={( ci_valid > 61.8).mean()*100:.0f}%  '
          f'<38.2={(ci_valid < 38.2).mean()*100:.0f}%')
    print(f'[INFO] ADX(D1,14): mean={adx_valid.mean():.1f}  '
          f'<25 ={(adx_valid < 25 ).mean()*100:.0f}%')
    rel_valid = ind['rel_atr_h1'][~np.isnan(ind['rel_atr_h1'])]
    print(f'[INFO] RelATR(H1): mean={rel_valid.mean():.3f}  '
          f'<1.1={(rel_valid < 1.1).mean()*100:.0f}%')

    # Build all combos
    combos = list(itertools.product(
        GRID_MULT_LIST,
        MAX_LEVELS_LIST,
        EXIT_CONFIGS,
        list(RF_CONFIGS.items()),
    ))
    total = len(combos)
    print(f'[INFO] {total} parameter combinations\n')

    all_results  = []
    equity_cache = {}
    done         = 0

    for gm, ml, (ex_type, ex_param), (rf_name, (use_ci, use_adx, use_relaltr)) in combos:
        done += 1
        if done % 50 == 0:
            print(f'  ... {done}/{total}')

        m, eq_list = run_backtest(
            df, conv_arr, ind,
            grid_mult    = gm,
            max_levels   = int(ml),
            exit_type    = ex_type,
            exit_param   = ex_param,
            use_ci       = use_ci,
            use_adx      = use_adx,
            use_relaltr  = use_relaltr,
        )
        if m is None:
            continue

        ex_label = f'{ex_type}{ex_param}' if ex_param is not None else 'A'
        row = {
            'exit':       ex_label,
            'rf':         rf_name,
            'grid_mult':  gm,
            'max_levels': int(ml),
            **m,
        }
        all_results.append(row)
        equity_cache[(ex_label, rf_name, gm, int(ml))] = eq_list

    if not all_results:
        print('[WARN] No results.')
        return

    result_df = pd.DataFrame(all_results)
    result_df = result_df.sort_values('PF', ascending=False).reset_index(drop=True)
    result_df.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[INFO] Results saved: {OUTPUT_CSV}  ({len(result_df)} rows)')

    # ---- Summary by exit strategy ----
    pd.set_option('display.width', 160)
    pd.set_option('display.max_columns', 20)
    print('\n=== Best PF per exit strategy ===')
    best_exit = result_df.groupby('exit').first().reset_index() \
                         .sort_values('PF', ascending=False)
    print(best_exit[['exit', 'rf', 'grid_mult', 'max_levels',
                     'PF', 'WR', 'n_trades', 'total_pnl_jpy',
                     'max_dd_pct', 'avg_hold_h']].to_string(index=False))

    # ---- Summary by range filter ----
    print('\n=== Best PF per range filter ===')
    best_rf = result_df.groupby('rf').first().reset_index() \
                       .sort_values('PF', ascending=False)
    print(best_rf[['rf', 'exit', 'grid_mult', 'max_levels',
                   'PF', 'WR', 'n_trades', 'total_pnl_jpy',
                   'max_dd_pct', 'avg_hold_h']].to_string(index=False))

    # ---- Qualifying ----
    qual = result_df[
        (result_df['PF']         >= ADOPT_PF) &
        (result_df['max_dd_pct'] <= ADOPT_DD_PCT) &
        (result_df['n_trades']   >= ADOPT_MIN_N)
    ]
    print(f'\n=== Qualifying (PF>={ADOPT_PF}, DD<={ADOPT_DD_PCT}%, '
          f'n>={ADOPT_MIN_N}): {len(qual)} rows ===')
    if not qual.empty:
        print(qual.head(20).to_string(index=False))
    else:
        print('  (none)')

    # ---- Top 5 overall ----
    print('\n=== Top 5 overall ===')
    print(result_df.head(5).to_string(index=False))

    # ---- Equity curves (top 5) ----
    top5    = result_df.head(5)
    n_plots = min(5, len(top5))
    fig, axes = plt.subplots(n_plots, 1, figsize=(13, 3.5 * n_plots))
    if n_plots == 1:
        axes = [axes]

    for ax, (_, row) in zip(axes, top5.iterrows()):
        key = (row['exit'], row['rf'], row['grid_mult'], int(row['max_levels']))
        eq  = equity_cache.get(key)
        if eq is None:
            continue
        ax.plot(eq, linewidth=0.8)
        ax.set_title(
            f"exit={row['exit']}  rf={row['rf']}  "
            f"gm={row['grid_mult']}  lv={int(row['max_levels'])}  |  "
            f"PF={row['PF']}  WR={row['WR']}  n={int(row['n_trades'])}  "
            f"DD={row['max_dd_pct']}%  hold={row['avg_hold_h']}h  "
            f"PnL={int(row['total_pnl_jpy']):+,}JPY",
            fontsize=8,
        )
        ax.set_ylabel('Equity (JPY)')
        ax.axhline(0, color='gray', linewidth=0.5, linestyle='--')
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHART_PATH, dpi=100)
    print(f'\n[INFO] Chart saved: {CHART_PATH}')
    plt.show()


if __name__ == '__main__':
    main()
