"""
sma_squeeze_daily_filter_bt.py - Daily SMA slope filter evaluation for SMA Squeeze Play.

For each pair, fixed params = current PAIRS_CFG best params.
Grid: daily_sma x daily_slope_period (plus no-filter baseline).

Daily slope logic (same as 1h slope but on daily candles resampled from 1h):
  True  = strictly rising  -> long allowed
  False = strictly falling -> short allowed
  None  = indeterminate    -> allow (pass through)

Filter rule:
  rising  (1h UP)  AND daily_slope=False -> skip
  falling (1h DN)  AND daily_slope=True  -> skip

Output: optimizer/sma_squeeze_daily_filter_bt_result.csv
"""

import itertools
import os
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

DATA_DIR   = Path(__file__).parent.parent / 'data'
OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'sma_squeeze_daily_filter_bt_result.csv')

# Fixed params per pair (current PAIRS_CFG best, from CLAUDE.md)
PAIRS_CFG = {
    'USDJPY': {'sma_short': 25, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 5,  'rr': 2.5, 'sl_atr_mult': 1.5, 'timeframe': '4h'},
    'GBPJPY': {'sma_short': 25, 'sma_long': 250, 'squeeze_th': 0.5,
               'slope_period': 10, 'rr': 2.0, 'sl_atr_mult': 1.5, 'timeframe': '1h'},
    'EURUSD': {'sma_short': 25, 'sma_long': 200, 'squeeze_th': 2.0,
               'slope_period': 10, 'rr': 2.5, 'sl_atr_mult': 1.0, 'timeframe': '4h'},
    'GBPUSD': {'sma_short': 15, 'sma_long': 250, 'squeeze_th': 1.5,
               'slope_period': 20, 'rr': 2.0, 'sl_atr_mult': 1.0, 'timeframe': '1h'},
    'EURJPY': {'sma_short': 15, 'sma_long': 150, 'squeeze_th': 2.0,
               'slope_period': 20, 'rr': 2.5, 'sl_atr_mult': 1.5, 'timeframe': '4h'},
}

# Grid for daily filter (None = no filter baseline)
DAILY_SMA_GRID          = [None, 20, 50]
DAILY_SLOPE_PERIOD_GRID = [3, 5, 10]

MIN_TRADES = 20

# Reference dates for 9-loss verification (2026-05-13 to 2026-05-14)
VERIFY_START = pd.Timestamp('2026-05-13')
VERIFY_END   = pd.Timestamp('2026-05-15')


# ─────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────

def load_1h_with_dt(pair):
    """Load 1h CSV. Returns df with integer index and 'dt' column (UTC naive)."""
    candidates = [f'{pair}_1h.csv', f'{pair}_H1.csv', f'{pair}_1H.csv', f'{pair.lower()}_1h.csv']
    for fname in candidates:
        path = DATA_DIR / fname
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df.columns = [c.lower().strip() for c in df.columns]
        # drop duplicate columns (Open/High/Low/Close extra copies)
        df = df.loc[:, ~df.columns.duplicated()]

        dt_col = 'datetime' if 'datetime' in df.columns else df.columns[0]
        df['dt'] = pd.to_datetime(df[dt_col])
        try:
            df['dt'] = df['dt'].dt.tz_convert(None)
        except Exception:
            try:
                df['dt'] = df['dt'].dt.tz_localize(None)
            except Exception:
                pass

        keep = ['dt'] + [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        if 'close' not in keep:
            continue
        df = df[keep].dropna(subset=['close']).sort_values('dt').reset_index(drop=True)
        return df
    return None


def resample_4h(df_1h):
    """Resample 1h df (with 'dt' column) to 4h OHLCV. Returns df with 'dt' column."""
    tmp = df_1h.set_index('dt')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in tmp.columns:
        agg['volume'] = 'sum'
    df4h = tmp.resample('4h').agg(agg).dropna(subset=['close'])
    df4h.index.name = 'dt'
    return df4h.reset_index()


def resample_daily(df_1h):
    """Resample 1h df to daily OHLCV. Returns df with 'dt' column (date-level)."""
    tmp = df_1h.set_index('dt')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in tmp.columns:
        agg['volume'] = 'sum'
    dfd = tmp.resample('1D').agg(agg).dropna(subset=['close'])
    dfd.index.name = 'dt'
    return dfd.reset_index()


# ─────────────────────────────────────────────
# Indicators
# ─────────────────────────────────────────────

def calc_atr14(df):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def calc_adx14(df):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl       = high - low
    hc       = (high - close.shift()).abs()
    lc       = (low  - close.shift()).abs()
    tr       = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s    = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()


def calc_max_dd(equity):
    eq   = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd   = float((eq - peak).min())
    return -dd if dd < 0 else 0.0


# ─────────────────────────────────────────────
# Daily slope lookup table builder
# ─────────────────────────────────────────────

def build_daily_slope_map(df_daily, daily_sma, daily_slope_period):
    """
    Returns a dict: date -> True/False/None (daily SMA slope).
    date is normalized to midnight (Timestamp).
    """
    d = df_daily.copy().reset_index(drop=True)
    d['sma_d'] = d['close'].rolling(daily_sma).mean()
    result = {}
    sp = daily_slope_period
    for i in range(len(d)):
        dt_key = d.loc[i, 'dt'].normalize()  # floor to day
        sma_vals = d['sma_d'].values
        if i < sp:
            result[dt_key] = None
            continue
        seg   = sma_vals[i - sp + 1: i + 1]
        if np.any(np.isnan(seg)):
            result[dt_key] = None
            continue
        diffs = np.diff(seg)
        if np.all(diffs > 0):
            result[dt_key] = True
        elif np.all(diffs < 0):
            result[dt_key] = False
        else:
            result[dt_key] = None
    return result


# ─────────────────────────────────────────────
# Core backtest
# ─────────────────────────────────────────────

def run_backtest(df, cfg, daily_slope_map=None, collect_dates=False):
    """
    Run backtest with fixed params from cfg.
    daily_slope_map: dict date->True/False/None or None (no filter).
    collect_dates: if True, return list of (entry_date, direction) instead of metrics.
    """
    sma_short    = cfg['sma_short']
    sma_long     = cfg['sma_long']
    squeeze_th   = cfg['squeeze_th']
    slope_period = cfg['slope_period']
    rr           = cfg['rr']
    sl_atr_mult  = cfg['sl_atr_mult']

    close_s = df['close']
    sma_s   = close_s.rolling(sma_short).mean().values
    sma_l   = close_s.rolling(sma_long).mean().values
    atr14   = calc_atr14(df).values
    adx14   = calc_adx14(df).values
    close_a = close_s.values
    open_a  = df['open'].values
    dt_a    = df['dt'].values  # numpy datetime64
    n       = len(df)

    warmup   = max(sma_long, slope_period, 28) + 2
    trades   = []
    equity   = [0.0]
    in_trade = False
    t_dir = t_entry = t_sl = t_tp = 0.0
    entry_dates = []

    for i in range(warmup, n):
        c     = close_a[i]
        o     = open_a[i]
        sl_v  = sma_l[i]
        ss_v  = sma_s[i]
        atr_v = atr14[i]
        adx_v = adx14[i]

        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            continue

        if in_trade:
            force = (t_dir == 'long' and c < sl_v) or (t_dir == 'short' and c > sl_v)
            if force:
                pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
                trades.append(pnl)
                equity.append(equity[-1] + pnl)
                in_trade = False
            elif t_dir == 'long':
                if c <= t_entry - t_sl:
                    trades.append(-t_sl)
                    equity.append(equity[-1] - t_sl)
                    in_trade = False
                elif c >= t_entry + t_tp:
                    trades.append(t_tp)
                    equity.append(equity[-1] + t_tp)
                    in_trade = False
            else:
                if c >= t_entry + t_sl:
                    trades.append(-t_sl)
                    equity.append(equity[-1] - t_sl)
                    in_trade = False
                elif c <= t_entry - t_tp:
                    trades.append(t_tp)
                    equity.append(equity[-1] + t_tp)
                    in_trade = False

        if in_trade:
            continue

        if adx_v <= 20.0:
            continue
        if sl_v == 0.0:
            continue

        div_rate = abs(ss_v - sl_v) / sl_v * 100.0
        if div_rate > squeeze_th:
            continue

        slp_start = i - slope_period + 1
        if slp_start < 0:
            continue
        slp = sma_l[slp_start: i + 1]
        if np.any(np.isnan(slp)):
            continue

        diffs   = np.diff(slp)
        rising  = bool(np.all(diffs > 0))
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

        # Determine direction before daily filter
        direction = None
        if rising and c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            direction = 'long'
        elif falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            direction = 'short'

        if direction is None:
            continue

        # Daily filter
        if daily_slope_map is not None:
            bar_dt   = pd.Timestamp(dt_a[i]).normalize()
            d_slope  = daily_slope_map.get(bar_dt, None)
            if d_slope is not None:
                if direction == 'long'  and d_slope is False:
                    continue  # 1h UP but daily DN -> skip
                if direction == 'short' and d_slope is True:
                    continue  # 1h DN but daily UP -> skip

        if collect_dates:
            entry_dates.append((pd.Timestamp(dt_a[i]), direction))

        t_dir = direction
        t_entry = c
        t_sl    = sl_dist
        t_tp    = tp_dist
        in_trade = True

    if in_trade:
        c   = close_a[-1]
        pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
        trades.append(pnl)
        equity.append(equity[-1] + pnl)

    if collect_dates:
        return entry_dates

    if len(trades) < MIN_TRADES:
        return None

    arr  = np.array(trades)
    wins = arr[arr > 0]
    loss = arr[arr <= 0]
    gp   = float(wins.sum()) if len(wins) > 0 else 0.0
    gl   = float(abs(loss.sum())) if len(loss) > 0 else 0.0
    pf   = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)

    return {
        'PF':       round(pf, 3),
        'win_rate': round(len(wins) / len(arr), 3),
        'n_trades': len(arr),
        'max_dd':   round(calc_max_dd(equity), 6),
    }


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    all_results = []

    for pair, cfg in PAIRS_CFG.items():
        print(f'\n=== {pair} (tf={cfg["timeframe"]}) ===')

        df_1h = load_1h_with_dt(pair)
        if df_1h is None:
            print(f'  [WARN] {pair} 1h CSV not found')
            continue
        print(f'  1h rows: {len(df_1h)}')

        df_daily = resample_daily(df_1h)
        print(f'  daily rows: {len(df_daily)}')

        if cfg['timeframe'] == '4h':
            df_main = resample_4h(df_1h)
            print(f'  4h rows: {len(df_main)}')
        else:
            df_main = df_1h.copy()

        # ── Baseline: no filter ──
        m_base = run_backtest(df_main, cfg, daily_slope_map=None)
        if m_base is None:
            print(f'  baseline: n_trades < {MIN_TRADES}, skipping pair')
            continue

        print(f'  baseline (no filter): PF={m_base["PF"]:.3f}  WR={m_base["win_rate"]:.1%}  n={m_base["n_trades"]}')
        all_results.append({
            'pair':               pair,
            'timeframe':          cfg['timeframe'],
            'sma_short':          cfg['sma_short'],
            'sma_long':           cfg['sma_long'],
            'squeeze_th':         cfg['squeeze_th'],
            'slope_period':       cfg['slope_period'],
            'rr':                 cfg['rr'],
            'sl_atr_mult':        cfg['sl_atr_mult'],
            'daily_sma':          None,
            'daily_slope_period': None,
            'PF':                 m_base['PF'],
            'win_rate':           m_base['win_rate'],
            'n_trades':           m_base['n_trades'],
            'max_dd':             m_base['max_dd'],
        })

        # ── Collect baseline trades in verify window ──
        base_dates = run_backtest(df_main, cfg, daily_slope_map=None, collect_dates=True)
        verify_base = [(dt, d) for dt, d in base_dates
                       if VERIFY_START <= dt < VERIFY_END]
        if verify_base:
            print(f'  Verify window (no filter): {len(verify_base)} entries')
            for dt, d in verify_base:
                print(f'    {dt.strftime("%Y-%m-%d %H:%M")}  {d}')

        # ── Grid: daily_sma x daily_slope_period ──
        for daily_sma in [x for x in DAILY_SMA_GRID if x is not None]:
            for dsp in DAILY_SLOPE_PERIOD_GRID:
                slope_map = build_daily_slope_map(df_daily, daily_sma, dsp)
                m = run_backtest(df_main, cfg, daily_slope_map=slope_map)
                n = m['n_trades'] if m else 0

                # also get verify-window trades
                v_dates = run_backtest(df_main, cfg, daily_slope_map=slope_map, collect_dates=True)
                verify_flt = [(dt, d) for dt, d in v_dates if VERIFY_START <= dt < VERIFY_END]
                filtered_out = len(verify_base) - len(verify_flt)

                if m is None:
                    print(f'  d_sma={daily_sma} d_sp={dsp}: n<{MIN_TRADES} (filtered too much)'
                          f'  verify_blocked={filtered_out}/{len(verify_base)}')
                    all_results.append({
                        'pair':               pair,
                        'timeframe':          cfg['timeframe'],
                        'sma_short':          cfg['sma_short'],
                        'sma_long':           cfg['sma_long'],
                        'squeeze_th':         cfg['squeeze_th'],
                        'slope_period':       cfg['slope_period'],
                        'rr':                 cfg['rr'],
                        'sl_atr_mult':        cfg['sl_atr_mult'],
                        'daily_sma':          daily_sma,
                        'daily_slope_period': dsp,
                        'PF':                 0.0,
                        'win_rate':           0.0,
                        'n_trades':           0,
                        'max_dd':             0.0,
                    })
                    continue

                print(f'  d_sma={daily_sma} d_sp={dsp}: '
                      f'PF={m["PF"]:.3f}  WR={m["win_rate"]:.1%}  n={m["n_trades"]}'
                      f'  verify_blocked={filtered_out}/{len(verify_base)}')
                all_results.append({
                    'pair':               pair,
                    'timeframe':          cfg['timeframe'],
                    'sma_short':          cfg['sma_short'],
                    'sma_long':           cfg['sma_long'],
                    'squeeze_th':         cfg['squeeze_th'],
                    'slope_period':       cfg['slope_period'],
                    'rr':                 cfg['rr'],
                    'sl_atr_mult':        cfg['sl_atr_mult'],
                    'daily_sma':          daily_sma,
                    'daily_slope_period': dsp,
                    'PF':                 m['PF'],
                    'win_rate':           m['win_rate'],
                    'n_trades':           m['n_trades'],
                    'max_dd':             m['max_dd'],
                })

    if all_results:
        df_out = pd.DataFrame(all_results)
        df_out.to_csv(OUTPUT_CSV, index=False)
        print(f'\n[INFO] {len(df_out)} rows saved -> {OUTPUT_CSV}')
    else:
        print('[WARN] no results')
        return

    # ─────────────────────────────────────────────
    # Analysis: filter vs baseline comparison
    # ─────────────────────────────────────────────
    print('\n' + '='*70)
    print('ANALYSIS: Daily filter vs Baseline (current PAIRS_CFG params)')
    print('='*70)
    print(f'Adoption criteria: PF improved AND n_trades >= {MIN_TRADES}')
    print()

    adoptions = {}

    for pair in PAIRS_CFG.keys():
        pair_rows = df_out[df_out['pair'] == pair]
        if pair_rows.empty:
            continue

        base_row = pair_rows[pair_rows['daily_sma'].isna()].iloc[0]
        flt_rows = pair_rows[~pair_rows['daily_sma'].isna() & (pair_rows['n_trades'] >= MIN_TRADES)]

        print(f'--- {pair} ---')
        print(f'  Baseline: PF={base_row["PF"]:.3f}  WR={base_row["win_rate"]:.1%}  n={int(base_row["n_trades"])}')

        if flt_rows.empty:
            print('  Filter: all combos insufficient n_trades -> NOT ADOPTED')
            adoptions[pair] = None
            continue

        best = flt_rows.loc[flt_rows['PF'].idxmax()]
        pf_delta = best['PF'] - base_row['PF']
        adopted = (best['PF'] > base_row['PF']) and (best['n_trades'] >= MIN_TRADES)

        print(f'  Best filter: daily_sma={int(best["daily_sma"])}  daily_slope_period={int(best["daily_slope_period"])}')
        print(f'    PF={best["PF"]:.3f} ({"+"+f"{pf_delta:.3f}" if pf_delta>=0 else f"{pf_delta:.3f}"})'
              f'  WR={best["win_rate"]:.1%}  n={int(best["n_trades"])}')
        print(f'  Decision: {"ADOPT ✅" if adopted else "NOT ADOPT ❌"} '
              f'({"PF improved" if pf_delta>0 else "PF degraded"}, n={int(best["n_trades"])})')
        adoptions[pair] = best if adopted else None

    print()
    print('='*70)
    print('ADOPTION SUMMARY')
    print('='*70)
    for pair, best in adoptions.items():
        if best is None:
            print(f'  {pair}: NOT ADOPTED')
        else:
            print(f'  {pair}: daily_sma={int(best["daily_sma"])}  daily_slope_period={int(best["daily_slope_period"])}'
                  f'  PF={best["PF"]:.3f}  n={int(best["n_trades"])}')


if __name__ == '__main__':
    main()
