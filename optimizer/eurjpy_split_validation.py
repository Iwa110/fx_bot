"""
eurjpy_split_validation.py - EURJPY htf4h_rsi_bw フィルター 期間分割検証 (1h足版)
採用パラメータ: rsi_buy<65, rsi_sell>35, bw=0.8@10, sl=3.0, rr=2.0
  (eurjpy_filter_bt.py ADOPT Top1: PF=2.386 WR=50.0% N=26)

判定:
# STABLE:   全期間PF>1.0
# UNSTABLE: 1期間PF<1.0 → 追加検証推奨
# REJECT:   2期間以上PF<1.0 → 過学習リスク高
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path

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
HTF_PERIOD      = 20
HTF_SIGMA       = 1.5
HTF_RANGE_SIGMA = 1.0
COOLDOWN_BARS   = 3
SPREAD          = 2 * PIP_UNIT
HOUR_FILTER     = [9, 17]

# ===== 採用パラメータ（eurjpy_filter_bt.py 結果より） =====
ADOPTED_RSI_BUY_MAX  = 65
ADOPTED_RSI_SELL_MIN = 35
ADOPTED_BW_RATIO     = 0.8
ADOPTED_BW_LOOKBACK  = 10
ADOPTED_SL_ATR_MULT  = 3.0
ADOPTED_RR           = 2.0


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


# ===== ルックアップ構築 =====
def build_htf4h_sigma_lookup(df_1h, period=20, sigma=1.5):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    ma   = df4h['close'].rolling(period).mean()
    std  = df4h['close'].rolling(period).std()
    df4h['sigma_pos'] = (df4h['close'] - ma) / std.replace(0, np.nan)
    return df4h['sigma_pos']


def build_htf4h_ema_lookup(df_1h, ema_period=20):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20']  = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_rsi_4h_lookup(df_1h, period=14):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], period)
    return df4h['rsi']


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


# ===== コアシミュレーション =====
def simulate(df_1h_slice, htf4h_sig_lkp, htf4h_ema_lkp, rsi_4h_lkp, use_filter=True):
    """
    use_filter=False: ベースライン (htf4h_only, sl=2.5, rr=1.5)
    use_filter=True:  採用フィルター (ADOPTED_* パラメータ)
    ルックアップは全期間から構築済み → searchsortedでlookaheadなし
    """
    sl_atr_mult = ADOPTED_SL_ATR_MULT if use_filter else 2.5
    rr          = ADOPTED_RR          if use_filter else 1.5

    close = df_1h_slice['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi_1h = calc_rsi(close, RSI_PERIOD)
    atr    = calc_atr(df_1h_slice, ATR_PERIOD)

    bb_width      = bb_u - bb_l
    bb_width_mean = bb_width.rolling(ADOPTED_BW_LOOKBACK).mean() if use_filter else None

    close_arr = close.values
    n         = len(df_1h_slice)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    pnl_list = []
    win_pnls = []
    loss_pnls = []
    last_bar = -COOLDOWN_BARS - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        c       = close_arr[i]
        sl_dist = atr.iloc[i] * sl_atr_mult
        tp_dist = sl_dist * rr
        if sl_dist == 0 or np.isnan(sl_dist) or np.isnan(c):
            continue

        dt = df_1h_slice['datetime'].iloc[i]

        if dt.hour not in HOUR_FILTER:
            continue

        htf_idx = htf4h_sig_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf4h_sig_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE_SIGMA:
            continue

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

        htf4h_idx = htf4h_ema_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_ema_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        if use_filter:
            rsi4h_idx = rsi_4h_lkp.index.searchsorted(dt, side='right') - 1
            if rsi4h_idx < 0:
                continue
            rsi4h_val = rsi_4h_lkp.iloc[rsi4h_idx]
            if np.isnan(rsi4h_val):
                continue
            if direction == 'buy'  and rsi4h_val >= ADOPTED_RSI_BUY_MAX:
                continue
            if direction == 'sell' and rsi4h_val <= ADOPTED_RSI_SELL_MIN:
                continue

            mean_bw = bb_width_mean.iloc[i]
            cur_bw  = bb_u.iloc[i] - bb_l.iloc[i]
            if np.isnan(mean_bw) or cur_bw < mean_bw * ADOPTED_BW_RATIO:
                continue

        entry    = c + SPREAD if direction == 'buy' else c - SPREAD
        tp_price = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl_price = entry - sl_dist if direction == 'buy' else entry + sl_dist

        hit = exit_price = None
        for j in range(i + 1, min(i + 120, n)):
            h = df_1h_slice['high'].iloc[j]
            l = df_1h_slice['low'].iloc[j]
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
    }


# ===== メイン =====
def main():
    log_print('=== EURJPY 期間分割検証 (1h足版) ===')
    log_print(f'採用パラメータ: rsi_buy<{ADOPTED_RSI_BUY_MAX} rsi_sell>{ADOPTED_RSI_SELL_MIN} '
              f'bw={ADOPTED_BW_RATIO}@{ADOPTED_BW_LOOKBACK} '
              f'sl={ADOPTED_SL_ATR_MULT} rr={ADOPTED_RR}')

    df_1h = load_csv(SYMBOL, '1h')
    if df_1h is None:
        log_print('[ERROR] EURJPY_1h.csv なし → 終了')
        return

    total = len(df_1h)
    log_print(f'1h bars: {total}')

    log_print('ルックアップ構築中...')
    htf4h_sig_lkp = build_htf4h_sigma_lookup(df_1h, HTF_PERIOD, HTF_SIGMA)
    htf4h_ema_lkp = build_htf4h_ema_lookup(df_1h)
    rsi_4h_lkp    = build_rsi_4h_lookup(df_1h)
    log_print('構築完了')

    third  = total // 3
    slices = {
        'Period_A': df_1h.iloc[:third].reset_index(drop=True),
        'Period_B': df_1h.iloc[third:2 * third].reset_index(drop=True),
        'Period_C': df_1h.iloc[2 * third:].reset_index(drop=True),
    }

    log_print('\n期間情報:')
    for name, sl in slices.items():
        d_from = sl['datetime'].iloc[0].strftime('%Y-%m-%d')
        d_to   = sl['datetime'].iloc[-1].strftime('%Y-%m-%d')
        log_print(f'  {name}: {d_from} ~ {d_to}  ({len(sl)} bars)')

    rows    = []
    results = {}

    for period_name, sl in slices.items():
        d_from = sl['datetime'].iloc[0].strftime('%Y-%m-%d')
        d_to   = sl['datetime'].iloc[-1].strftime('%Y-%m-%d')
        results[period_name] = {}

        for filter_label, use_f in [('baseline', False), ('RSI+BW', True)]:
            res = simulate(sl, htf4h_sig_lkp, htf4h_ema_lkp, rsi_4h_lkp, use_filter=use_f)
            if res is None:
                log_print(f'[WARN] {period_name} {filter_label}: N=0')
                res = {'trades': 0, 'win_rate': 0.0, 'pf': 0.0, 'max_dd': 0.0}
            results[period_name][filter_label] = res
            rows.append({
                'period':      period_name,
                'date_from':   d_from,
                'date_to':     d_to,
                'filter_type': filter_label,
                'PF':          res['pf'],
                'WR':          res['win_rate'],
                'N':           res['trades'],
                'MaxDD':       res['max_dd'],
            })

    out_csv = os.path.join(OPT_DIR, 'eurjpy_split_validation.csv')
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding='utf-8')
    log_print(f'\n出力: {out_csv}')

    log_print('\n' + '=' * 65)
    log_print('  期間別 PF比較表')
    log_print('=' * 65)
    log_print(f'  {"period":>10} | {"date_from":>12} ~ {"date_to":>12} | '
              f'{"baseline_PF":>11} | {"RSI+BW_PF":>9} | {"dPF":>6} | {"N(RSI+BW)":>9}')
    log_print('  ' + '-' * 62)

    filter_pfs = []
    base_pfs   = []
    for period_name in ['Period_A', 'Period_B', 'Period_C']:
        sl     = slices[period_name]
        d_from = sl['datetime'].iloc[0].strftime('%Y-%m-%d')
        d_to   = sl['datetime'].iloc[-1].strftime('%Y-%m-%d')
        base_r = results[period_name]['baseline']
        filt_r = results[period_name]['RSI+BW']
        dpf    = filt_r['pf'] - base_r['pf']
        base_pfs.append(base_r['pf'])
        filter_pfs.append(filt_r['pf'])
        log_print(f'  {period_name:>10} | {d_from:>12} ~ {d_to:>12} | '
                  f'{base_r["pf"]:>11.3f} | {filt_r["pf"]:>9.3f} | '
                  f'{dpf:>+6.3f} | {filt_r["trades"]:>9}')

    log_print('\n' + '=' * 65)
    log_print('  安定性指標（PF標準偏差）')
    log_print('=' * 65)
    log_print(f'  baseline: {base_pfs[0]:.3f} / {base_pfs[1]:.3f} / {base_pfs[2]:.3f}  '
              f'std={np.std(base_pfs):.3f}')
    log_print(f'  RSI+BW:   {filter_pfs[0]:.3f} / {filter_pfs[1]:.3f} / {filter_pfs[2]:.3f}  '
              f'std={np.std(filter_pfs):.3f}')

    below_1 = sum(1 for pf in filter_pfs if pf < 1.0)
    if below_1 == 0:
        judgment = 'STABLE';   comment = '全期間PF>1.0 → 過学習リスク低'
    elif below_1 == 1:
        judgment = 'UNSTABLE'; comment = '1期間PF<1.0 → 追加検証推奨'
    else:
        judgment = 'REJECT';   comment = f'{below_1}期間PF<1.0 → 過学習リスク高'

    log_print('\n' + '=' * 65)
    log_print(f'  判定: {judgment}  ({comment})')
    log_print(f'  RSI+BW PF<1.0の期間数: {below_1}/3')
    log_print('=' * 65)

    log_print('\n詳細（RSI+BW）:')
    log_print(f'  {"period":>10} | {"PF":>6} | {"WR":>5} | {"N":>5} | {"MaxDD":>8}')
    log_print('  ' + '-' * 42)
    for period_name in ['Period_A', 'Period_B', 'Period_C']:
        r = results[period_name]['RSI+BW']
        log_print(f'  {period_name:>10} | {r["pf"]:>6.3f} | {r["win_rate"]:>4.1f}% | '
                  f'{r["trades"]:>5} | {r["max_dd"]:>8.1f}')

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
