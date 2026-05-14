"""
eurjpy_filter_bt.py - EURJPY htf4h_rsi_bw グリッドBT (1h足版)
データ: EURJPY_1h.csv (2024-04-24〜)
エントリーTF: 1h / HTF sigma: 4h / HTF EMA20: 4h / 4h RSI: 4h

ベースライン: htf4h_only, sl=2.5, rr=1.5, hour=[9,17]
目標: PF>1.2 かつ N>=30

グリッド:
  rsi_buy_max  : [55, 60, 65]
  rsi_sell_min : [35, 40, 45]
  bw_ratio     : [0.8, 1.0, 1.2, 1.5]
  bw_lookback  : [10, 20, 30]
  sl_atr_mult  : [2.0, 2.5, 3.0]
  rr           : [1.5, 2.0]
  hour_filter  : [9, 17] 固定
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
_VPS_OPT_DIR = r'C:\Users\Administrator\fx_bot\optimizer'
OPT_DIR = _VPS_OPT_DIR if os.path.isdir(_VPS_OPT_DIR) else str(Path(__file__).parent)

# ===== EURJPY固定設定 =====
SYMBOL          = 'EURJPY'
PIP_UNIT        = 0.01
BB_PERIOD       = 20
BB_SIGMA        = 1.5
RSI_PERIOD      = 14
RSI_BUY_MAX     = 45   # 1h RSI エントリー条件（固定）
RSI_SELL_MIN    = 55   # 1h RSI エントリー条件（固定）
ATR_PERIOD      = 14
HTF_PERIOD      = 20   # 4h BB sigma 期間
HTF_SIGMA       = 1.5
HTF_RANGE_SIGMA = 1.0
COOLDOWN_BARS   = 3    # 1h×3 = 3時間
SPREAD          = 2 * PIP_UNIT
HOUR_FILTER     = [9, 17]   # UTC 固定

# ===== グリッド =====
RSI_BUY_MAX_GRID  = [55, 60, 65]
RSI_SELL_MIN_GRID = [35, 40, 45]
BW_RATIO_GRID     = [0.8, 1.0, 1.2, 1.5]
BW_LOOKBACK_GRID  = [10, 20, 30]
SL_ATR_MULT_GRID  = [2.0, 2.5, 3.0]
RR_GRID           = [1.5, 2.0]

ADOPT_PF_TH = 1.2
MIN_N       = 30


def log_print(msg):
    print(msg, flush=True)


# ===== データ読み込み =====
def load_csv(symbol, tf='1h'):
    candidates = [
        os.path.join(DATA_DIR, f'{symbol}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol.lower()}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_{tf.upper()}.csv'),
    ]
    if tf == '1h':
        candidates.append(os.path.join(DATA_DIR, f'{symbol}_H1.csv'))
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
        df = df.sort_index()
        df = df.reset_index()
        return df
    log_print(f'[WARN] CSVなし: {symbol} {tf}')
    return None


# ===== インジケーター =====
def calc_bb(close, period=20, sigma=1.5):
    ma  = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma + sigma * std, ma - sigma * std, ma, std


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


# ===== ルックアップ構築（4h足を1hからリサンプル） =====
def build_htf4h_sigma_lookup(df_1h, period=20, sigma=1.5):
    """4h BB sigma position ルックアップ"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    ma   = df4h['close'].rolling(period).mean()
    std  = df4h['close'].rolling(period).std()
    df4h['sigma_pos'] = (df4h['close'] - ma) / std.replace(0, np.nan)
    return df4h['sigma_pos']


def build_htf4h_ema_lookup(df_1h, ema_period=20):
    """4h EMA20 方向フィルター: +1=Buy許可 / -1=Sell許可"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20']  = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_rsi_4h_lookup(df_1h, period=14):
    """4h RSI ルックアップ"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], period)
    return df4h['rsi']


# ===== MaxDD =====
def compute_max_dd_pips(pnl_list):
    if not pnl_list:
        return 0.0
    equity = peak = max_dd = 0.0
    for pnl in pnl_list:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return round(max_dd / PIP_UNIT, 1)


# ===== コアシミュレーション（1h足・固定TP） =====
def simulate(
    df_1h, htf4h_sig_lkp, htf4h_ema_lkp, rsi_4h_lkp,
    sl_atr_mult, rr,
    rsi_buy_max=None, rsi_sell_min=None,
    bw_ratio=None, bw_lookback=None,
):
    close = df_1h['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi_1h = calc_rsi(close, RSI_PERIOD)
    atr    = calc_atr(df_1h, ATR_PERIOD)

    bb_width      = bb_u - bb_l
    bb_width_mean = (bb_width.rolling(bw_lookback).mean()
                     if bw_ratio is not None and bw_lookback is not None else None)

    close_arr = close.values
    n         = len(df_1h)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    pnl_list  = []
    win_pnls  = []
    loss_pnls = []
    last_bar  = -COOLDOWN_BARS - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        c       = close_arr[i]
        sl_dist = atr.iloc[i] * sl_atr_mult
        tp_dist = sl_dist * rr
        if sl_dist == 0 or np.isnan(sl_dist) or np.isnan(c):
            continue

        dt = df_1h['datetime'].iloc[i]

        # 時間帯フィルター（固定）
        if dt.hour not in HOUR_FILTER:
            continue

        # 4h HTF sigma（常時ON）
        htf_idx = htf4h_sig_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf4h_sig_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE_SIGMA:
            continue

        # 1h RSI エントリー方向
        rsi_v = rsi_1h.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < RSI_BUY_MAX:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > RSI_SELL_MIN:
            direction = 'sell'
        if direction is None:
            continue

        # HTF 4h EMA20（常時ON）
        htf4h_idx = htf4h_ema_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_ema_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        # 4h RSIフィルター
        if rsi_buy_max is not None and rsi_sell_min is not None:
            rsi4h_idx = rsi_4h_lkp.index.searchsorted(dt, side='right') - 1
            if rsi4h_idx < 0:
                continue
            rsi4h_val = rsi_4h_lkp.iloc[rsi4h_idx]
            if np.isnan(rsi4h_val):
                continue
            if direction == 'buy'  and rsi4h_val >= rsi_buy_max:
                continue
            if direction == 'sell' and rsi4h_val <= rsi_sell_min:
                continue

        # BBwidthフィルター
        if bw_ratio is not None and bb_width_mean is not None:
            mean_bw = bb_width_mean.iloc[i]
            cur_bw  = bb_u.iloc[i] - bb_l.iloc[i]
            if np.isnan(mean_bw) or cur_bw < mean_bw * bw_ratio:
                continue

        # 固定TP決済（1h足で前進チェック）
        entry    = c + SPREAD if direction == 'buy' else c - SPREAD
        tp_price = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl_price = entry - sl_dist if direction == 'buy' else entry + sl_dist

        hit        = None
        exit_price = None
        for j in range(i + 1, min(i + 120, n)):
            h = df_1h['high'].iloc[j]
            l = df_1h['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'; exit_price = sl_price; break
                if h >= tp_price:
                    hit = 'tp'; exit_price = tp_price; break
            else:
                if h >= sl_price:
                    hit = 'sl'; exit_price = sl_price; break
                if l <= tp_price:
                    hit = 'tp'; exit_price = tp_price; break

        if hit is None or exit_price is None:
            continue

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price
        pnl_list.append(pnl)

        if pnl > 0:
            wins += 1; gross_profit += pnl; win_pnls.append(pnl)
        else:
            losses += 1; gross_loss += abs(pnl); loss_pnls.append(abs(pnl))
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':   trades,
        'win_rate': round(wins / trades * 100, 1),
        'pf':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'max_dd':   compute_max_dd_pips(pnl_list),
        'avg_win':  round(float(np.mean(win_pnls))  / PIP_UNIT, 1) if win_pnls  else 0.0,
        'avg_loss': round(float(np.mean(loss_pnls)) / PIP_UNIT, 1) if loss_pnls else 0.0,
    }


def verdict(pf, n):
    if pf >= ADOPT_PF_TH and n >= MIN_N:
        return 'ADOPT'
    if pf >= 1.0 and n >= MIN_N:
        return 'CONDITIONAL'
    return 'REJECT'


# ===== メイン =====
def main():
    log_print('=== EURJPY htf4h_rsi_bw グリッドBT (1h足版) ===')
    log_print(f'時間帯固定: {HOUR_FILTER} UTC')
    log_print(f'目標: PF>={ADOPT_PF_TH} かつ N>={MIN_N}')

    df_1h = load_csv(SYMBOL, '1h')
    if df_1h is None:
        log_print('[ERROR] EURJPY_1h.csv なし → 終了')
        return

    d_from = df_1h['datetime'].iloc[0].strftime('%Y-%m-%d')
    d_to   = df_1h['datetime'].iloc[-1].strftime('%Y-%m-%d')
    log_print(f'1h: {len(df_1h)} bars ({d_from} ~ {d_to})')

    log_print('ルックアップ構築中...')
    htf4h_sig_lkp = build_htf4h_sigma_lookup(df_1h, HTF_PERIOD, HTF_SIGMA)
    htf4h_ema_lkp = build_htf4h_ema_lookup(df_1h)
    rsi_4h_lkp    = build_rsi_4h_lookup(df_1h)
    log_print('構築完了')

    # ===== ベースライン =====
    log_print('\n----- ベースライン (htf4h_only, sl=2.5, rr=1.5) -----')
    baseline = simulate(df_1h, htf4h_sig_lkp, htf4h_ema_lkp, rsi_4h_lkp,
                        sl_atr_mult=2.5, rr=1.5)
    if baseline:
        log_print(f'  PF={baseline["pf"]}  WR={baseline["win_rate"]}%  '
                  f'N={baseline["trades"]}  MaxDD={baseline["max_dd"]}pips')
    baseline_pf = baseline['pf'] if baseline else 0.0

    # ===== グリッドサーチ =====
    grid  = list(product(
        RSI_BUY_MAX_GRID, RSI_SELL_MIN_GRID,
        BW_RATIO_GRID, BW_LOOKBACK_GRID,
        SL_ATR_MULT_GRID, RR_GRID,
    ))
    total = len(grid)
    log_print(f'\n[Grid] 総実行数: {total} runs')

    rows = []
    for run_i, (rsi_buy_max, rsi_sell_min, bw_ratio, bw_lookback, sl_mult, rr) in enumerate(grid, 1):
        params_str = (f'rsi_buy<{rsi_buy_max},rsi_sell>{rsi_sell_min},'
                      f'bw={bw_ratio}@{bw_lookback},sl={sl_mult},rr={rr}')
        res = simulate(
            df_1h, htf4h_sig_lkp, htf4h_ema_lkp, rsi_4h_lkp,
            sl_atr_mult=sl_mult, rr=rr,
            rsi_buy_max=rsi_buy_max, rsi_sell_min=rsi_sell_min,
            bw_ratio=bw_ratio, bw_lookback=bw_lookback,
        )
        if res is None:
            continue
        dpf = res['pf'] - baseline_pf
        v   = verdict(res['pf'], res['trades'])
        row = {
            'run':          run_i,
            'rsi_buy_max':  rsi_buy_max,
            'rsi_sell_min': rsi_sell_min,
            'bw_ratio':     bw_ratio,
            'bw_lookback':  bw_lookback,
            'sl_atr_mult':  sl_mult,
            'rr':           rr,
            'PF':           res['pf'],
            'WR':           res['win_rate'],
            'N':            res['trades'],
            'MaxDD':        res['max_dd'],
            'avg_win':      res['avg_win'],
            'avg_loss':     res['avg_loss'],
            'delta_pf':     round(dpf, 3),
            'verdict':      v,
        }
        rows.append(row)
        if run_i % 100 == 0 or v == 'ADOPT':
            log_print(f'  [{run_i}/{total}] {params_str}: '
                      f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                      f'N={res["trades"]} [{v}]')

    # ===== 出力 =====
    out_csv = os.path.join(OPT_DIR, 'eurjpy_filter_bt.csv')
    if rows:
        df_out = pd.DataFrame(rows)
        df_out.to_csv(out_csv, index=False, encoding='utf-8')
        log_print(f'\n出力: {out_csv} ({len(rows)}件)')

        adopt = [r for r in rows if r['verdict'] == 'ADOPT']
        log_print(f'\nADOPT件数: {len(adopt)}')
        if adopt:
            top10 = sorted(adopt, key=lambda x: x['PF'], reverse=True)[:10]
            log_print('\n=== ADOPT Top10 (PF降順) ===')
            hdr = (f'  {"rsi_b":>5} {"rsi_s":>5} {"bw_r":>5} {"lb":>4} '
                   f'{"sl":>4} {"rr":>4} | {"PF":>6} {"dPF":>6} {"WR":>5} '
                   f'{"N":>5} {"MaxDD":>8}')
            log_print(hdr)
            log_print('  ' + '-' * 72)
            for r in top10:
                log_print(f'  {r["rsi_buy_max"]:>5} {r["rsi_sell_min"]:>5} '
                          f'{r["bw_ratio"]:>5.1f} {r["bw_lookback"]:>4} '
                          f'{r["sl_atr_mult"]:>4.1f} {r["rr"]:>4.1f} | '
                          f'{r["PF"]:>6.3f} {r["delta_pf"]:>+6.3f} '
                          f'{r["WR"]:>4.1f}% {r["N"]:>5} {r["MaxDD"]:>8.1f}')
        else:
            log_print('[INFO] ADOPT条件(PF>=1.2, N>=30)を満たす組み合わせなし')
            cond = [r for r in rows if r['verdict'] == 'CONDITIONAL']
            if cond:
                top5 = sorted(cond, key=lambda x: x['PF'], reverse=True)[:5]
                log_print('\n=== CONDITIONAL Top5 ===')
                for r in top5:
                    log_print(f'  rsi_b<{r["rsi_buy_max"]} rsi_s>{r["rsi_sell_min"]} '
                              f'bw={r["bw_ratio"]}@{r["bw_lookback"]} '
                              f'sl={r["sl_atr_mult"]} rr={r["rr"]} → '
                              f'PF={r["PF"]} WR={r["WR"]}% N={r["N"]}')

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
