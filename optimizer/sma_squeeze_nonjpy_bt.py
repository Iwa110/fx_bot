"""
sma_squeeze_nonjpy_bt.py
Walk-forward BT for SMA Squeeze Play on non-JPY pairs: AUDUSD, NZDUSD, USDCAD.

- Data: yfinance 1h (max 730 days)
- IS/OOS split: chronological IS 60% / OOS 40%
- IS: grid search -> pick best PF params (n>=MIN_IS_TRADES)
- OOS: evaluate best IS params + daily filter (daily_sma=20, slope_period=3)
- Cost: spread * 3 deducted at entry
- Pass criteria (OOS): PF>=1.4 / Sharpe>=1.0 / DD<15pct / n>=30

Compare output against existing 5 pairs (from CLAUDE.md BT results).
"""

import itertools
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    raise SystemExit('yfinance not installed. Run: pip install yfinance')


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

PAIRS = {
    'AUDUSD': {'ticker': 'AUDUSD=X', 'spread_pips': 1.5, 'pip': 0.0001},
    'NZDUSD': {'ticker': 'NZDUSD=X', 'spread_pips': 2.0, 'pip': 0.0001},
    'USDCAD': {'ticker': 'USDCAD=X', 'spread_pips': 2.5, 'pip': 0.0001},
}

# Grid search parameters (applied on IS data)
GRID = {
    'timeframe':   ['1h', '4h'],
    'sma_short':   [15, 25],
    'sma_long':    [150, 200, 250],
    'squeeze_th':  [0.5, 1.0, 1.5, 2.0],
    'slope_period': [5, 10, 20],
    'rr':          [2.0, 2.5],
    'sl_atr_mult': [1.0, 1.5],
}

# Daily filter (fixed, same best combo from existing BT)
DAILY_SMA         = 20
DAILY_SLOPE_PERIOD = 3

IS_RATIO      = 0.60
MIN_IS_TRADES = 20
MIN_OOS_N     = 30

OOS_PF_PASS     = 1.4
OOS_SHARPE_PASS = 1.0
OOS_DD_PASS     = 0.15   # max DD as fraction of peak equity; threshold: <15%

# Existing 5 pairs reference (from CLAUDE.md, daily filter applied, OOS not applicable)
EXISTING_PAIRS_REF = {
    'USDJPY': {'tf': '4h', 'PF': 1.928, 'n': 27},
    'GBPJPY': {'tf': '1h', 'PF': 1.522, 'n': 47},
    'EURUSD': {'tf': '4h', 'PF': 2.831, 'n': 29},
    'GBPUSD': {'tf': '1h', 'PF': 1.372, 'n': 208, 'note': 'stopped'},
    'EURJPY': {'tf': '4h', 'PF': 3.748, 'n': 29},
}


# ─────────────────────────────────────────────
# Data download
# ─────────────────────────────────────────────

def download_1h(ticker):
    """Download max 1h data from yfinance. Returns DataFrame with dt/open/high/low/close."""
    end   = datetime.utcnow()
    start = end - timedelta(days=728)
    df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                     end=end.strftime('%Y-%m-%d'), interval='1h',
                     auto_adjust=True, progress=False)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = df.rename_axis('dt').reset_index()
    df['dt'] = pd.to_datetime(df['dt'])
    try:
        df['dt'] = df['dt'].dt.tz_convert(None)
    except Exception:
        try:
            df['dt'] = df['dt'].dt.tz_localize(None)
        except Exception:
            pass
    df = df[['dt', 'open', 'high', 'low', 'close']].dropna().sort_values('dt').reset_index(drop=True)
    return df


def resample_4h(df_1h):
    tmp  = df_1h.set_index('dt')
    agg  = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    df4h = tmp.resample('4h').agg(agg).dropna(subset=['close'])
    df4h.index.name = 'dt'
    return df4h.reset_index()


def resample_daily(df_1h):
    tmp = df_1h.set_index('dt')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
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
    hl   = high - low
    hc   = (high - close.shift()).abs()
    lc   = (low  - close.shift()).abs()
    tr   = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s    = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()


def calc_max_dd_pct(equity):
    """Max drawdown as fraction (not percentage)."""
    eq   = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide='ignore', invalid='ignore'):
        dd = np.where(peak != 0, (eq - peak) / np.abs(peak), 0.0)
    return float(-dd.min()) if dd.min() < 0 else 0.0


def calc_sharpe(trades, oos_days):
    """Annualized Sharpe based on trade returns."""
    arr = np.array(trades, dtype=float)
    if len(arr) < 2:
        return 0.0
    mu   = arr.mean()
    std  = arr.std(ddof=1)
    if std == 0.0:
        return 0.0
    # annualize by trades per year
    trades_per_year = len(arr) / max(oos_days / 365.0, 0.01)
    return float(mu / std * np.sqrt(trades_per_year))


# ─────────────────────────────────────────────
# Daily slope map
# ─────────────────────────────────────────────

def build_daily_slope_map(df_daily, daily_sma, slope_period):
    d = df_daily.copy().reset_index(drop=True)
    d['sma_d'] = d['close'].rolling(daily_sma).mean()
    result = {}
    for i in range(len(d)):
        dt_key = d.loc[i, 'dt'].normalize()
        if i < slope_period:
            result[dt_key] = None
            continue
        seg = d['sma_d'].values[i - slope_period + 1: i + 1]
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

def run_backtest(df, cfg, cost_per_trade=0.0, daily_slope_map=None, min_trades=1):
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
    dt_a    = df['dt'].values
    n       = len(df)

    warmup   = max(sma_long, slope_period, 28) + 2
    trades   = []
    equity   = [0.0]
    in_trade = False
    t_dir = t_entry = t_sl = t_tp = 0.0

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
                trades.append(pnl - cost_per_trade)
                equity.append(equity[-1] + pnl - cost_per_trade)
                in_trade = False
            elif t_dir == 'long':
                if c <= t_entry - t_sl:
                    trades.append(-t_sl - cost_per_trade)
                    equity.append(equity[-1] - t_sl - cost_per_trade)
                    in_trade = False
                elif c >= t_entry + t_tp:
                    trades.append(t_tp - cost_per_trade)
                    equity.append(equity[-1] + t_tp - cost_per_trade)
                    in_trade = False
            else:
                if c >= t_entry + t_sl:
                    trades.append(-t_sl - cost_per_trade)
                    equity.append(equity[-1] - t_sl - cost_per_trade)
                    in_trade = False
                elif c <= t_entry - t_tp:
                    trades.append(t_tp - cost_per_trade)
                    equity.append(equity[-1] + t_tp - cost_per_trade)
                    in_trade = False

        if in_trade:
            continue

        if adx_v <= 20.0 or sl_v == 0.0:
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

        direction = None
        if rising and c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            direction = 'long'
        elif falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            direction = 'short'

        if direction is None:
            continue

        if daily_slope_map is not None:
            bar_dt  = pd.Timestamp(dt_a[i]).normalize()
            d_slope = daily_slope_map.get(bar_dt, None)
            if d_slope is not None:
                if direction == 'long'  and d_slope is False:
                    continue
                if direction == 'short' and d_slope is True:
                    continue

        t_dir    = direction
        t_entry  = c
        t_sl     = sl_dist
        t_tp     = tp_dist
        in_trade = True

    if in_trade:
        c   = close_a[-1]
        pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
        trades.append(pnl - cost_per_trade)
        equity.append(equity[-1] + pnl - cost_per_trade)

    if len(trades) < min_trades:
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
        'max_dd':   round(calc_max_dd_pct(equity), 4),
        'trades':   list(arr),
    }


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    results = {}

    for pair, pcfg in PAIRS.items():
        ticker = pcfg['ticker']
        cost   = pcfg['spread_pips'] * pcfg['pip'] * 3  # spread x3

        print(f'\n{"="*60}')
        print(f'Pair: {pair}  (ticker={ticker}, cost={cost:.5f})')
        print(f'{"="*60}')

        df_1h = download_1h(ticker)
        if df_1h is None or len(df_1h) < 500:
            print(f'  [ERROR] insufficient data for {pair}')
            results[pair] = None
            continue
        print(f'  1h rows: {len(df_1h)}  '
              f'[{df_1h["dt"].iloc[0].date()} to {df_1h["dt"].iloc[-1].date()}]')

        df_daily = resample_daily(df_1h)
        df_4h    = resample_4h(df_1h)

        # IS/OOS split by index
        n_1h = len(df_1h)
        is_cutoff_idx = int(n_1h * IS_RATIO)
        split_dt = df_1h['dt'].iloc[is_cutoff_idx]
        print(f'  IS cutoff: {split_dt.date()}  (IS={is_cutoff_idx} bars, OOS={n_1h - is_cutoff_idx} bars)')

        df_1h_is  = df_1h[df_1h['dt'] <  split_dt].reset_index(drop=True)
        df_1h_oos = df_1h[df_1h['dt'] >= split_dt].reset_index(drop=True)
        df_4h_is  = df_4h[df_4h['dt'] <  split_dt].reset_index(drop=True)
        df_4h_oos = df_4h[df_4h['dt'] >= split_dt].reset_index(drop=True)
        df_daily_is  = df_daily[df_daily['dt'] <  split_dt].reset_index(drop=True)
        df_daily_oos = df_daily[df_daily['dt'] >= split_dt].reset_index(drop=True)

        oos_days = (df_1h_oos['dt'].iloc[-1] - df_1h_oos['dt'].iloc[0]).days

        # ── IS grid search ──
        print(f'  Running IS grid search...')
        keys   = list(GRID.keys())
        combos = list(itertools.product(*GRID.values()))
        print(f'  Total combos: {len(combos)}')

        best_is_pf = -1.0
        best_cfg   = None

        for combo in combos:
            cfg = dict(zip(keys, combo))
            tf  = cfg['timeframe']
            df_main = df_1h_is if tf == '1h' else df_4h_is

            m = run_backtest(df_main, cfg, cost_per_trade=cost,
                             daily_slope_map=None,
                             min_trades=MIN_IS_TRADES)
            if m is None:
                continue
            if m['PF'] > best_is_pf:
                best_is_pf = m['PF']
                best_cfg   = {**cfg, '_is_n': m['n_trades'], '_is_pf': m['PF']}

        if best_cfg is None:
            print(f'  [SKIP] No IS combo meets min_trades={MIN_IS_TRADES}')
            results[pair] = None
            continue

        print(f'  Best IS params: tf={best_cfg["timeframe"]} '
              f'sma_s={best_cfg["sma_short"]} sma_l={best_cfg["sma_long"]} '
              f'sq_th={best_cfg["squeeze_th"]} slp={best_cfg["slope_period"]} '
              f'rr={best_cfg["rr"]} sl_m={best_cfg["sl_atr_mult"]}')
        print(f'  IS result: PF={best_is_pf:.3f}  n={best_cfg["_is_n"]}')

        # ── OOS evaluation ──
        tf_oos = best_cfg['timeframe']
        df_main_oos = df_1h_oos if tf_oos == '1h' else df_4h_oos

        # Build daily slope map for OOS
        slope_map_oos = build_daily_slope_map(
            pd.concat([df_daily_is, df_daily_oos]).reset_index(drop=True),
            DAILY_SMA, DAILY_SLOPE_PERIOD
        )
        # Filter map to OOS dates only
        oos_start = df_main_oos['dt'].iloc[0].normalize()
        slope_map_oos_filtered = {k: v for k, v in slope_map_oos.items() if k >= oos_start}

        m_oos = run_backtest(df_main_oos, best_cfg, cost_per_trade=cost,
                             daily_slope_map=slope_map_oos_filtered,
                             min_trades=1)

        if m_oos is None or len(m_oos['trades']) == 0:
            print(f'  OOS: no trades')
            results[pair] = {'is': best_cfg, 'oos': None}
            continue

        sharpe = calc_sharpe(m_oos['trades'], oos_days)
        n_oos  = m_oos['n_trades']
        pf_oos = m_oos['PF']
        dd_oos = m_oos['max_dd']
        wr_oos = m_oos['win_rate']

        pass_pf     = pf_oos >= OOS_PF_PASS
        pass_sharpe = sharpe >= OOS_SHARPE_PASS
        pass_dd     = dd_oos < OOS_DD_PASS
        pass_n      = n_oos >= MIN_OOS_N
        overall     = pass_pf and pass_sharpe and pass_dd and pass_n

        print(f'\n  OOS result:')
        print(f'    n={n_oos}  PF={pf_oos:.3f}  WR={wr_oos:.1%}  Sharpe={sharpe:.2f}  DD={dd_oos:.1%}')
        print(f'    n>={MIN_OOS_N}:  {"OK" if pass_n else "NG"}  '
              f'PF>={OOS_PF_PASS}: {"OK" if pass_pf else "NG"}  '
              f'Sharpe>={OOS_SHARPE_PASS}: {"OK" if pass_sharpe else "NG"}  '
              f'DD<{OOS_DD_PASS:.0%}: {"OK" if pass_dd else "NG"}')
        print(f'    Overall: {"PASS ✅" if overall else "FAIL ❌"}')

        results[pair] = {
            'is_pf':    round(best_is_pf, 3),
            'is_n':     best_cfg['_is_n'],
            'tf':       tf_oos,
            'cfg':      best_cfg,
            'oos_n':    n_oos,
            'oos_pf':   pf_oos,
            'oos_wr':   wr_oos,
            'oos_dd':   dd_oos,
            'sharpe':   round(sharpe, 2),
            'pass_pf':  pass_pf,
            'pass_sh':  pass_sharpe,
            'pass_dd':  pass_dd,
            'pass_n':   pass_n,
            'overall':  overall,
        }

    # ─────────────────────────────────────────────
    # Summary table
    # ─────────────────────────────────────────────
    print('\n\n' + '='*70)
    print('SUMMARY: SMA Squeeze — non-JPY pairs vs existing 5 pairs')
    print('='*70)
    print('  * Existing 5 pairs: full-data BT with daily filter (from CLAUDE.md)')
    print('  * New pairs: IS 60% / OOS 40% WF, cost=spread×3')
    print()
    print(f'{"Pair":<10} {"TF":<4} {"IS_PF":<8} {"OOS_PF":<8} {"OOS_n":<7} '
          f'{"Sharpe":<8} {"DD":<7} {"vs existing":<12} {"Result"}')
    print('-'*80)

    # Existing 5 pairs reference line
    for p, ref in EXISTING_PAIRS_REF.items():
        note = f' ({ref.get("note","")})'  if ref.get('note') else ''
        print(f'{p:<10} {ref["tf"]:<4} {"—":<8} {ref["PF"]:<8.3f} {ref["n"]:<7} '
              f'{"—":<8} {"—":<7} {"(reference)":<12} {note}')

    print()
    pass_pairs = []
    for pair, r in results.items():
        if r is None:
            print(f'{pair:<10} {"—":<4} {"—":<8} {"—":<8} {"—":<7} '
                  f'{"—":<8} {"—":<7} {"—":<12} DATA ERROR')
            continue

        tf    = r.get('tf', '—')
        is_pf = r.get('is_pf', 0.0)
        oos   = r

        if oos.get('oos_pf') is None:
            print(f'{pair:<10} {tf:<4} {is_pf:<8.3f} {"—":<8} {"0":<7} '
                  f'{"—":<8} {"—":<7} {"—":<12} NO OOS TRADES')
            continue

        # vs existing: compare OOS PF against existing 5 pairs average
        existing_pf_list = [v['PF'] for v in EXISTING_PAIRS_REF.values()
                            if not v.get('note')]  # exclude stopped GBPUSD
        avg_existing = np.mean(existing_pf_list)
        oos_pf = oos['oos_pf']
        if oos_pf >= avg_existing * 0.9:
            vs = '同等+'
        elif oos_pf >= avg_existing * 0.7:
            vs = '劣る'
        else:
            vs = '大幅劣る'

        result_str = 'PASS ✅' if oos['overall'] else 'FAIL ❌'
        pass_info  = (f'n{"OK" if oos["pass_n"] else "NG"} '
                      f'PF{"OK" if oos["pass_pf"] else "NG"} '
                      f'Sh{"OK" if oos["pass_sh"] else "NG"} '
                      f'DD{"OK" if oos["pass_dd"] else "NG"}')

        print(f'{pair:<10} {tf:<4} {is_pf:<8.3f} {oos_pf:<8.3f} {oos["oos_n"]:<7} '
              f'{oos["sharpe"]:<8.2f} {oos["oos_dd"]:.1%}{"  ":<4} {vs:<12} '
              f'{result_str} ({pass_info})')

        if oos['overall']:
            pass_pairs.append(pair)

    print()
    print(f'Pass criteria: OOS PF>={OOS_PF_PASS} / Sharpe>={OOS_SHARPE_PASS} '
          f'/ DD<{OOS_DD_PASS:.0%} / n>={MIN_OOS_N}')
    print()

    if pass_pairs:
        print('✅ sma_sq_monitor.py への追加候補:')
        for p in pass_pairs:
            r = results[p]
            cfg = r['cfg']
            print(f"  {p}: tf={cfg['timeframe']} sma_s={cfg['sma_short']} "
                  f"sma_l={cfg['sma_long']} sq_th={cfg['squeeze_th']} "
                  f"slp={cfg['slope_period']} rr={cfg['rr']} sl_m={cfg['sl_atr_mult']}")
            print(f"    OOS: PF={r['oos_pf']:.3f}  Sharpe={r['sharpe']:.2f}  "
                  f"n={r['oos_n']}  DD={r['oos_dd']:.1%}")
    else:
        print('❌ 合格ペアなし — 今回の3ペアは sma_sq_monitor.py への追加候補外')

    print('\n[Done]')


if __name__ == '__main__':
    main()
