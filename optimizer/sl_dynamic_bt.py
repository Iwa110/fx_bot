"""
sl_dynamic_bt.py - BB戦略 動的SL改善バックテスト
対象: GBPJPY / USDJPY
ベース設定: GBPJPY=htf4h_only / USDJPY=htf4h_rsi (固定TP)
手法: baseline / P1=BreakEven / P2=SwingSL / P3=BE+Swing
出力: sl_bt_result_GBPJPY.csv / sl_bt_result_USDJPY.csv
"""

import os
import itertools
import numpy as np
import pandas as pd
from pathlib import Path

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
OUT_DIR = Path(_VPS_DATA_DIR).parent / 'optimizer' if os.path.isdir(_VPS_DATA_DIR) else Path(__file__).parent

# ===== 共通パラメータ（BB / HTF / エントリー） =====
BASE_CFG = {
    'bb_period':       20,
    'htf_period':      20,
    'htf_sigma':       1.5,
    'htf_range_sigma': 1.0,
    'rsi_period':      14,
    'rsi_buy_max':     45,
    'rsi_sell_min':    55,
    'atr_period':      14,
    'cooldown_bars':   3,
    'spread_pips':     2,
}

# ===== ペア別設定 =====
PAIR_CFG = {
    'GBPJPY': {
        'bb_sigma':    1.5,
        'sl_atr_mult': 3.0,
        'tp_sl_ratio': 1.5,
        'pip_unit':    0.01,
        'htf_mode':    'htf4h',      # 4h EMA20 direction filter
    },
    'USDJPY': {
        'bb_sigma':    2.0,
        'sl_atr_mult': 3.0,
        'tp_sl_ratio': 1.5,
        'pip_unit':    0.01,
        'htf_mode':    'htf4h_rsi',  # 4h EMA20 + 4h RSI filter
    },
}

# ===== グリッドパラメータ =====
BE_TRIGGER_RATIOS  = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
BE_BUFFER_PIPS     = [0.0, 0.5, 1.0]
SWING_LOOKBACKS    = [5, 8, 10, 13, 20]
SWING_INTERVALS    = [1, 3, 5]

MIN_N = 30


# ===== ログ =====
def log_print(msg):
    print(msg)


# ===== データ読み込み =====
def load_csv(symbol, tf='5m'):
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
        df = df.dropna(subset=['close']).sort_index().reset_index()
        return df
    log_print(f'[WARN] CSVなし: {symbol} {tf}')
    return None


# ===== インジケーター =====
def calc_bb(close, period, sigma):
    ma  = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma + sigma * std, ma - sigma * std

def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ===== HTFルックアップ =====
def build_htf_sigma_lookup(df_1h, period, sigma):
    close     = df_1h['close']
    ma        = close.rolling(period).mean()
    std       = close.rolling(period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['v'] = sigma_pos.values
    return result.set_index('datetime')['v']

def build_htf4h_ema_lookup(df_1h, ema_period=20):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20'] = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['sig']   = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['sig']

def build_htf4h_rsi_lookup(df_1h, rsi_period=14):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], rsi_period)
    return df4h['rsi']


# ===== 最大ドローダウン計算 =====
def calc_max_dd(pnl_list):
    """PnLリスト（価格単位）から最大DD（価格単位）を返す。"""
    if not pnl_list:
        return 0.0
    equity = np.cumsum(pnl_list)
    peak   = np.maximum.accumulate(equity)
    dd     = peak - equity
    return float(np.max(dd))


# ===== コアシミュレーション =====
def simulate(symbol, method, be_trigger_ratio=0.3, be_buffer_pips=0.0,
             swing_lookback=10, swing_update_interval=3):
    """
    method: 'baseline' | 'be' | 'swing' | 'be_swing'
    戻り値: dict or None
    """
    pcfg = PAIR_CFG[symbol]
    cfg  = {**BASE_CFG, **pcfg}
    htf_mode = pcfg['htf_mode']

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    close = df_5m['close']
    bb_u, bb_l = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi_5m     = calc_rsi(close, cfg['rsi_period'])
    atr        = calc_atr(df_5m, cfg['atr_period'])

    htf_sigma_lkp = build_htf_sigma_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp     = build_htf4h_ema_lookup(df_1h)
    htf4h_rsi_lkp = build_htf4h_rsi_lookup(df_1h) if htf_mode == 'htf4h_rsi' else None

    spread    = cfg['spread_pips'] * cfg['pip_unit']
    pip       = cfg['pip_unit']
    close_arr = close.values
    high_arr  = df_5m['high'].values
    low_arr   = df_5m['low'].values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    pnl_list  = []
    last_bar  = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c   = close_arr[i]
        sl  = atr.iloc[i] * cfg['sl_atr_mult']
        tp  = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # HTF sigma フィルター
        htf_idx = htf_sigma_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_sigma_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # エントリー方向（BBタッチ + RSI）
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

        # 4h EMA20 フィルター（全モード共通）
        idx4h = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if idx4h < 0:
            continue
        sig4h = htf4h_lkp.iloc[idx4h]
        if direction == 'buy'  and sig4h != 1:
            continue
        if direction == 'sell' and sig4h != -1:
            continue

        # 4h RSI フィルター（htf4h_rsiのみ）
        if htf4h_rsi_lkp is not None:
            idx_r = htf4h_rsi_lkp.index.searchsorted(dt, side='right') - 1
            if idx_r < 0:
                continue
            rsi4h = htf4h_rsi_lkp.iloc[idx_r]
            if np.isnan(rsi4h):
                continue
            if direction == 'buy'  and rsi4h >= 55:
                continue
            if direction == 'sell' and rsi4h <= 45:
                continue

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = tp  # abs(tp_price - entry)

        be_buffer = be_buffer_pips * pip
        be_triggered = False
        current_sl   = sl_price
        hit          = None
        exit_price   = None

        for j in range(i + 1, min(i + 300, n)):
            h = high_arr[j]
            l = low_arr[j]

            if direction == 'buy':
                progress = (((h + l) / 2.0) - entry) / tp_dist if tp_dist > 0 else 0.0

                # BE トリガー
                if method in ('be', 'be_swing') and not be_triggered:
                    if progress >= be_trigger_ratio:
                        be_level = entry + be_buffer
                        if be_level > current_sl:
                            current_sl   = be_level
                            be_triggered = True

                # Swing SL 更新
                if method in ('swing', 'be_swing'):
                    bars_since_entry = j - i
                    if bars_since_entry > 0 and bars_since_entry % swing_update_interval == 0:
                        lookback_start = max(i, j - swing_lookback)
                        swing_low = float(np.min(low_arr[lookback_start:j]))
                        if swing_low > current_sl:
                            current_sl = swing_low

                # SL/TP 判定
                if l <= current_sl:
                    hit        = 'sl'
                    exit_price = current_sl
                    break
                if h >= tp_price:
                    hit        = 'tp'
                    exit_price = tp_price
                    break

            else:  # sell
                progress = (entry - ((h + l) / 2.0)) / tp_dist if tp_dist > 0 else 0.0

                # BE トリガー
                if method in ('be', 'be_swing') and not be_triggered:
                    if progress >= be_trigger_ratio:
                        be_level = entry - be_buffer
                        if be_level < current_sl:
                            current_sl   = be_level
                            be_triggered = True

                # Swing SL 更新
                if method in ('swing', 'be_swing'):
                    bars_since_entry = j - i
                    if bars_since_entry > 0 and bars_since_entry % swing_update_interval == 0:
                        lookback_start = max(i, j - swing_lookback)
                        swing_high = float(np.max(high_arr[lookback_start:j]))
                        if swing_high < current_sl:
                            current_sl = swing_high

                # SL/TP 判定
                if h >= current_sl:
                    hit        = 'sl'
                    exit_price = current_sl
                    break
                if l <= tp_price:
                    hit        = 'tp'
                    exit_price = tp_price
                    break

        if hit is None or exit_price is None:
            continue

        pnl = (exit_price - entry) if direction == 'buy' else (entry - exit_price)

        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)

        pnl_list.append(pnl)
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    avg_win  = gross_profit / wins   if wins   > 0 else 0.0
    avg_loss = gross_loss  / losses  if losses > 0 else 0.0
    max_dd   = calc_max_dd(pnl_list)

    return {
        'N':        trades,
        'PF':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'WR':       round(wins / trades * 100, 1),
        'MaxDD':    round(max_dd / pip, 1),       # pips換算
        'avg_win':  round(avg_win  / pip, 2),
        'avg_loss': round(avg_loss / pip, 2),
    }


# ===== BT実行 =====
def run_bt(symbol):
    log_print(f'\n{"="*70}')
    log_print(f'  {symbol}  htf_mode={PAIR_CFG[symbol]["htf_mode"]}')
    log_print(f'{"="*70}')

    rows = []

    # --- baseline ---
    log_print('\n[baseline]')
    res = simulate(symbol, 'baseline')
    if res:
        row = {'method': 'baseline', **res}
        rows.append(row)
        log_print(f'  PF={res["PF"]}  WR={res["WR"]}%  N={res["N"]}  '
                  f'MaxDD={res["MaxDD"]}pip  avg_win={res["avg_win"]}  avg_loss={res["avg_loss"]}')
    base_pf = res['PF'] if res else 0.0

    # --- P1: BreakEven ---
    log_print(f'\n[P1 BreakEven] {len(BE_TRIGGER_RATIOS) * len(BE_BUFFER_PIPS)} runs')
    hdr = (f'  {"be_trig":>7} | {"be_buf":>6} | '
           f'{"PF":>6} | {"WR":>5} | {"N":>5} | {"MaxDD":>7} | {"avgWin":>7} | {"avgLoss":>8}')
    log_print(hdr)
    log_print('  ' + '-' * 65)
    for trig, buf in itertools.product(BE_TRIGGER_RATIOS, BE_BUFFER_PIPS):
        res = simulate(symbol, 'be', be_trigger_ratio=trig, be_buffer_pips=buf)
        if res is None:
            continue
        mark = ' *' if res['N'] < MIN_N else ''
        row  = {
            'method':          'be',
            'be_trigger_ratio': trig,
            'be_buffer_pips':  buf,
            **res,
        }
        rows.append(row)
        dpf = res['PF'] - base_pf
        log_print(f'  {trig:>7.2f} | {buf:>6.1f} | '
                  f'{res["PF"]:>6.3f}({dpf:+.3f}) | '
                  f'{res["WR"]:>4.1f}% | '
                  f'{res["N"]:>5}{mark} | '
                  f'{res["MaxDD"]:>7.1f} | '
                  f'{res["avg_win"]:>7.2f} | '
                  f'{res["avg_loss"]:>8.2f}')

    # --- P2: SwingSL ---
    log_print(f'\n[P2 SwingSL] {len(SWING_LOOKBACKS) * len(SWING_INTERVALS)} runs')
    hdr = (f'  {"lookback":>8} | {"interval":>8} | '
           f'{"PF":>6} | {"WR":>5} | {"N":>5} | {"MaxDD":>7} | {"avgWin":>7} | {"avgLoss":>8}')
    log_print(hdr)
    log_print('  ' + '-' * 70)
    for lb, iv in itertools.product(SWING_LOOKBACKS, SWING_INTERVALS):
        res = simulate(symbol, 'swing', swing_lookback=lb, swing_update_interval=iv)
        if res is None:
            continue
        mark = ' *' if res['N'] < MIN_N else ''
        row  = {
            'method':                'swing',
            'swing_lookback':        lb,
            'swing_update_interval': iv,
            **res,
        }
        rows.append(row)
        dpf = res['PF'] - base_pf
        log_print(f'  {lb:>8} | {iv:>8} | '
                  f'{res["PF"]:>6.3f}({dpf:+.3f}) | '
                  f'{res["WR"]:>4.1f}% | '
                  f'{res["N"]:>5}{mark} | '
                  f'{res["MaxDD"]:>7.1f} | '
                  f'{res["avg_win"]:>7.2f} | '
                  f'{res["avg_loss"]:>8.2f}')

    # --- P3: BE + Swing ---
    p3_runs = len(BE_TRIGGER_RATIOS) * len(BE_BUFFER_PIPS) * len(SWING_LOOKBACKS) * len(SWING_INTERVALS)
    log_print(f'\n[P3 BE+Swing] {p3_runs} runs')
    hdr = (f'  {"trig":>5} | {"buf":>4} | {"lb":>4} | {"iv":>3} | '
           f'{"PF":>6} | {"WR":>5} | {"N":>5} | {"MaxDD":>7}')
    log_print(hdr)
    log_print('  ' + '-' * 60)
    best_p3 = None
    for trig, buf, lb, iv in itertools.product(BE_TRIGGER_RATIOS, BE_BUFFER_PIPS,
                                                SWING_LOOKBACKS, SWING_INTERVALS):
        res = simulate(symbol, 'be_swing',
                       be_trigger_ratio=trig, be_buffer_pips=buf,
                       swing_lookback=lb, swing_update_interval=iv)
        if res is None:
            continue
        mark = ' *' if res['N'] < MIN_N else ''
        row  = {
            'method':                'be_swing',
            'be_trigger_ratio':      trig,
            'be_buffer_pips':        buf,
            'swing_lookback':        lb,
            'swing_update_interval': iv,
            **res,
        }
        rows.append(row)
        dpf = res['PF'] - base_pf
        log_print(f'  {trig:>5.2f} | {buf:>4.1f} | {lb:>4} | {iv:>3} | '
                  f'{res["PF"]:>6.3f}({dpf:+.3f}) | '
                  f'{res["WR"]:>4.1f}% | '
                  f'{res["N"]:>5}{mark} | '
                  f'{res["MaxDD"]:>7.1f}')
        if res['N'] >= MIN_N:
            if best_p3 is None or res['PF'] > best_p3['PF']:
                best_p3 = {**row}

    # --- サマリー ---
    log_print(f'\n{"="*70}')
    log_print(f'  {symbol} サマリー（N>={MIN_N}）')
    log_print(f'{"="*70}')
    valid = [r for r in rows if r.get('N', 0) >= MIN_N]
    if valid:
        top5 = sorted(valid, key=lambda x: x['PF'], reverse=True)[:5]
        for r in top5:
            params = ''
            if r['method'] == 'be':
                params = f'trig={r["be_trigger_ratio"]} buf={r["be_buffer_pips"]}'
            elif r['method'] == 'swing':
                params = f'lb={r["swing_lookback"]} iv={r["swing_update_interval"]}'
            elif r['method'] == 'be_swing':
                params = (f'trig={r["be_trigger_ratio"]} buf={r["be_buffer_pips"]} '
                          f'lb={r["swing_lookback"]} iv={r["swing_update_interval"]}')
            log_print(f'  [{r["method"]:>8}] {params:<40} '
                      f'PF={r["PF"]}  WR={r["WR"]}%  N={r["N"]}  MaxDD={r["MaxDD"]}pip')

    # --- CSV出力 ---
    out_csv = str(OUT_DIR / f'sl_bt_result_{symbol}.csv')
    if rows:
        df_out = pd.DataFrame(rows)
        # NaN列を0埋め（パラメータ列はメソッドによってNaNになる）
        for col in ['be_trigger_ratio', 'be_buffer_pips', 'swing_lookback', 'swing_update_interval']:
            if col not in df_out.columns:
                df_out[col] = np.nan
        col_order = ['method', 'be_trigger_ratio', 'be_buffer_pips',
                     'swing_lookback', 'swing_update_interval',
                     'PF', 'WR', 'N', 'MaxDD', 'avg_win', 'avg_loss']
        df_out = df_out[col_order]
        df_out.to_csv(out_csv, index=False, encoding='utf-8')
        log_print(f'\n出力: {out_csv} ({len(rows)}件)')
    else:
        log_print('[ERROR] 結果なし')

    return rows


# ===== メイン =====
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='all', choices=['all', 'GBPJPY', 'USDJPY'])
    args = parser.parse_args()

    targets = list(PAIR_CFG.keys()) if args.symbol == 'all' else [args.symbol]
    total_runs = sum(
        1 + len(BE_TRIGGER_RATIOS) * len(BE_BUFFER_PIPS)
          + len(SWING_LOOKBACKS) * len(SWING_INTERVALS)
          + len(BE_TRIGGER_RATIOS) * len(BE_BUFFER_PIPS) * len(SWING_LOOKBACKS) * len(SWING_INTERVALS)
        for _ in targets
    )
    log_print(f'=== sl_dynamic_bt.py 開始 ===')
    log_print(f'対象: {targets}')
    log_print(f'総runs: {total_runs}')

    for symbol in targets:
        run_bt(symbol)

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
