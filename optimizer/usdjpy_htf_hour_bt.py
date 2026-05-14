"""
usdjpy_htf_hour_bt.py - USDJPY HTFfilter x hour filter grid BT
Fixed TP (no Stage2 trailing).

Stage1: HTF filter variants (no hour filter)
  htf_none  : sigma position filter only (no EMA direction)
  htf4h     : + 4h EMA20 direction (current baseline)
  htf4h_1h  : + 4h EMA20 + 1h EMA20 direction
  htf4h_rsi : + 4h EMA20 + 4h RSI<55(buy)/RSI>45(sell)

Stage2: best HTF from Stage1 x hour sets
  plan_A: [6, 7, 13, 20, 21, 22] UTC
  plan_B: [6, 7, 10, 13, 20, 21, 22] UTC

Output: usdjpy_htf_hour_result.csv
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path

# ===== Path =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')

OUTPUT_CSV = str(Path(__file__).parent / 'usdjpy_htf_hour_result.csv')

# ===== USDJPY params (BB_PAIRS_CFG + USDCAD_CFG defaults) =====
CFG = {
    'bb_period':       20,
    'bb_sigma':        2.0,
    'htf_period':      20,
    'htf_sigma':       1.5,
    'htf_range_sigma': 1.0,
    'rsi_period':      14,
    'rsi_buy_max':     45,
    'rsi_sell_min':    55,
    'sl_atr_mult':     3.0,
    'tp_sl_ratio':     1.5,
    'atr_period':      14,
    'cooldown_bars':   3,
    'spread_pips':     2,
    'pip_unit':        0.01,
}

HOUR_SETS = {
    'plan_A': [6, 7, 13, 20, 21, 22],
    'plan_B': [6, 7, 10, 13, 20, 21, 22],
}


# ===== Data =====
def load_csv(symbol, tf='1h'):
    candidates = [
        os.path.join(DATA_DIR, f'{symbol}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol.lower()}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_{tf.upper()}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_H1.csv'),
        os.path.join(DATA_DIR, f'{symbol}_M5.csv'),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df.index.name = 'datetime'
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]]
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.dropna(subset=['close'])
        df = df.sort_index().reset_index()
        return df
    print(f'[WARN] CSV not found: {symbol} {tf}')
    return None


# ===== Indicators =====
def calc_bb(close, period, sigma):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma + sigma * std, ma - sigma * std, ma, std


def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df, period=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ===== HTF Lookups =====
def build_htf_sigma_lookup(df_1h, htf_period, htf_sigma):
    close = df_1h['close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    ma = close.rolling(htf_period).mean()
    std = close.rolling(htf_period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']


def build_htf4h_ema_lookup(df_1h, ema_period=20):
    """Resample 1h -> 4h, compute EMA20 direction. +1=Buy / -1=Sell."""
    df = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20'] = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_htf1h_ema_lookup(df_1h, ema_period=20):
    """1h EMA20 direction. +1=Buy / -1=Sell."""
    df = df_1h.copy().set_index('datetime')
    ema20 = df['close'].ewm(span=ema_period, adjust=False).mean()
    signal = pd.Series(np.where(df['close'] > ema20, 1, -1), index=df.index)
    return signal


def build_htf4h_rsi_lookup(df_1h, rsi_period=14):
    """Resample 1h -> 4h, compute RSI."""
    df = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], rsi_period)
    return df4h['rsi']


# ===== Core simulation =====
def simulate(df_5m, df_1h, cfg, htf_mode, hour_filter=None):
    """
    Fixed TP BT.
    htf_mode: 'htf_none' | 'htf4h' | 'htf4h_1h' | 'htf4h_rsi'
    hour_filter: list[int] UTC hours, or None
    """
    close = df_5m['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi_5m = calc_rsi(close, cfg['rsi_period'])
    atr = calc_atr(df_5m, cfg['atr_period'])

    htf_sigma_lkp = build_htf_sigma_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    use_4h_ema = htf_mode in ('htf4h', 'htf4h_1h', 'htf4h_rsi')
    htf4h_lkp     = build_htf4h_ema_lookup(df_1h) if use_4h_ema else None
    htf1h_lkp     = build_htf1h_ema_lookup(df_1h) if htf_mode == 'htf4h_1h' else None
    htf4h_rsi_lkp = build_htf4h_rsi_lookup(df_1h) if htf_mode == 'htf4h_rsi' else None

    spread = cfg['spread_pips'] * cfg['pip_unit']
    close_arr = close.values
    n = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    last_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        if hour_filter is not None and dt.hour not in hour_filter:
            continue

        htf_idx = htf_sigma_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_sigma_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        rsi_v = rsi_5m.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        if htf4h_lkp is not None:
            idx4h = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if idx4h < 0:
                continue
            sig4h = htf4h_lkp.iloc[idx4h]
            if direction == 'buy' and sig4h != 1:
                continue
            if direction == 'sell' and sig4h != -1:
                continue

        if htf1h_lkp is not None:
            idx1h = htf1h_lkp.index.searchsorted(dt, side='right') - 1
            if idx1h < 0:
                continue
            sig1h = htf1h_lkp.iloc[idx1h]
            if direction == 'buy' and sig1h != 1:
                continue
            if direction == 'sell' and sig1h != -1:
                continue

        if htf4h_rsi_lkp is not None:
            idx_r = htf4h_rsi_lkp.index.searchsorted(dt, side='right') - 1
            if idx_r < 0:
                continue
            rsi4h = htf4h_rsi_lkp.iloc[idx_r]
            if np.isnan(rsi4h):
                continue
            if direction == 'buy' and rsi4h >= 55:
                continue
            if direction == 'sell' and rsi4h <= 45:
                continue

        entry = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp if direction == 'buy' else entry - tp
        sl_price = entry - sl if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 200, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'; break
                if h >= tp_price:
                    hit = 'tp'; break
            else:
                if h >= sl_price:
                    hit = 'sl'; break
                if l <= tp_price:
                    hit = 'tp'; break

        if hit == 'tp':
            wins += 1
            gross_profit += tp
        elif hit == 'sl':
            losses += 1
            gross_loss += sl
        else:
            continue

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'N':       trades,
        'PF':      round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'WR':      round(wins / trades * 100, 1),
        'avgWin':  round(gross_profit / wins, 5) if wins > 0 else 0.0,
        'avgLoss': round(gross_loss / losses, 5) if losses > 0 else 0.0,
    }


# ===== Stage runners =====
def run_stage1(df_5m, df_1h, cfg):
    plans = ['htf_none', 'htf4h', 'htf4h_1h', 'htf4h_rsi']
    rows = []
    print('=== Stage 1: HTFfilter only (no hour filter) ===')
    print(f'  {"plan":>12} | {"PF":>6} | {"WR":>6} | {"N":>5} | {"avgWin":>9} | {"avgLoss":>9}')
    print(f'  {"-"*62}')
    for plan in plans:
        res = simulate(df_5m, df_1h, cfg, htf_mode=plan, hour_filter=None)
        if res is None:
            print(f'  {plan:>12} | no data')
            continue
        row = {
            'stage':      1,
            'plan':       plan,
            'htf_filter': plan,
            'hours':      'all',
            'PF':         res['PF'],
            'WR':         res['WR'],
            'N':          res['N'],
            'avgWin':     res['avgWin'],
            'avgLoss':    res['avgLoss'],
        }
        rows.append(row)
        print(f'  {plan:>12} | {res["PF"]:>6.3f} | {res["WR"]:>5.1f}% | {res["N"]:>5} | {res["avgWin"]:>9.5f} | {res["avgLoss"]:>9.5f}')
    return rows


def run_stage2(df_5m, df_1h, cfg, best_htf):
    rows = []
    print(f'\n=== Stage 2: {best_htf} x hour filter ===')
    print(f'  {"plan":>8} | {"hours":>32} | {"PF":>6} | {"WR":>6} | {"N":>5} | {"avgWin":>9} | {"avgLoss":>9}')
    print(f'  {"-"*90}')
    for plan_name, hours in HOUR_SETS.items():
        res = simulate(df_5m, df_1h, cfg, htf_mode=best_htf, hour_filter=hours)
        if res is None:
            print(f'  {plan_name:>8} | no data')
            continue
        hours_str = str(hours)
        row = {
            'stage':      2,
            'plan':       plan_name,
            'htf_filter': best_htf,
            'hours':      hours_str,
            'PF':         res['PF'],
            'WR':         res['WR'],
            'N':          res['N'],
            'avgWin':     res['avgWin'],
            'avgLoss':    res['avgLoss'],
        }
        rows.append(row)
        print(f'  {plan_name:>8} | {hours_str:>32} | {res["PF"]:>6.3f} | {res["WR"]:>5.1f}% | {res["N"]:>5} | {res["avgWin"]:>9.5f} | {res["avgLoss"]:>9.5f}')
    return rows


def main():
    symbol = 'USDJPY'
    print(f'Loading data for {symbol} ...')
    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        print('[ERROR] CSV not found. Exiting.')
        return

    print(f'5m rows: {len(df_5m)}  1h rows: {len(df_1h)}\n')

    rows_s1 = run_stage1(df_5m, df_1h, CFG)

    valid_s1 = [r for r in rows_s1 if r['N'] >= 10]
    if not valid_s1:
        print('[ERROR] Stage1: no valid results (N>=10)')
        return
    best_row = max(valid_s1, key=lambda x: x['PF'])
    best_htf = best_row['htf_filter']
    print(f'\n[Stage1 best] htf_filter={best_htf}  PF={best_row["PF"]}  WR={best_row["WR"]}%  N={best_row["N"]}')

    print('\n' + '=' * 70)

    rows_s2 = run_stage2(df_5m, df_1h, CFG, best_htf)

    all_rows = rows_s1 + rows_s2
    if not all_rows:
        print('[ERROR] No results to save.')
        return

    cols = ['stage', 'plan', 'htf_filter', 'hours', 'PF', 'WR', 'N', 'avgWin', 'avgLoss']
    df_out = pd.DataFrame(all_rows, columns=cols)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding='utf-8')

    print(f'\n[Output] {OUTPUT_CSV}')
    print('\n=== CSV ===')
    print(df_out.to_string(index=False))


if __name__ == '__main__':
    main()
