"""
bb_fixedtp_bt.py - BB戦略 固定TP版バックテスト
Stage2トレーリングSLを廃止し、固定TP(SL x tp_sl_ratio)に戻した場合のPF/WRを検証。
対象: GBPJPY / USDJPY / EURUSD
フィルター: htf4h_only=True (4h EMA20方向フィルター)
出力: bb_fixedtp_bt_result.csv
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')

# ===== 対象ペア設定 (BB_PAIRS_CFG準拠) =====
TARGET_PAIRS = {
    'GBPJPY': {
        'is_jpy':       True,
        'pip_unit':     0.01,
        'bb_period':    20,
        'bb_sigma':     1.5,
        'sl_atr_mult':  3.0,
        'tp_sl_ratio':  1.5,
        'rsi_period':   14,
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'atr_period':   14,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'htf_range_sigma': 1.0,
        'cooldown_bars': 3,
        'bb_width_th':  None,
    },
    'USDJPY': {
        'is_jpy':       True,
        'pip_unit':     0.01,
        'bb_period':    20,
        'bb_sigma':     2.0,
        'sl_atr_mult':  3.0,
        'tp_sl_ratio':  1.5,
        'rsi_period':   14,
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'atr_period':   14,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'htf_range_sigma': 1.0,
        'cooldown_bars': 3,
        'bb_width_th':  None,
    },
    'EURUSD': {
        'is_jpy':       False,
        'pip_unit':     0.0001,
        'bb_period':    20,
        'bb_sigma':     1.5,
        'sl_atr_mult':  1.2,
        'tp_sl_ratio':  1.5,
        'rsi_period':   14,
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'atr_period':   14,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'htf_range_sigma': 1.0,
        'cooldown_bars': 3,
        'bb_width_th':  0.002,
    },
}

# ===== CSV読み込み =====
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
        df = df.dropna(subset=['close'])
        df = df.sort_index()
        df = df.reset_index()
        return df
    print(f'[WARN] CSVなし: {symbol} {tf}')
    return None

# ===== インジケーター =====
def calc_bb(close, period, sigma):
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

# ===== HTFルックアップ =====
def build_htf_lookup(df_1h, htf_period, htf_sigma):
    close     = df_1h['close']
    ma        = close.rolling(htf_period).mean()
    std       = close.rolling(htf_period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']

def build_htf4h_ema_lookup(df_1h, ema_period=20):
    df = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20'] = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']

# ===== コアシミュレーション（固定TP） =====
def simulate_fixed_tp(symbol, cfg, n_bars=None):
    """
    固定TP/SL シミュレーター。Stage2トレーリングなし。
    戻り値: {'pf', 'win_rate', 'trades', 'avg_win', 'avg_loss'} or None
    avg_win/avg_loss はpip単位
    """
    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    if n_bars is not None:
        df_5m = df_5m.tail(n_bars)
    df_5m = df_5m.reset_index(drop=True)

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp = build_htf4h_ema_lookup(df_1h)

    spread    = 2 * cfg['pip_unit']
    pip_unit  = cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    win_pnls = []
    loss_pnls = []
    last_bar  = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c   = close_arr[i]
        sl  = atr.iloc[i] * cfg['sl_atr_mult']
        tp  = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        # BB幅フィルター (EURUSD用)
        bb_width_th = cfg.get('bb_width_th')
        if bb_width_th is not None:
            bw = (bb_std.iloc[i] * 2) / bb_ma.iloc[i] if bb_ma.iloc[i] != 0 else 0
            if bw < bb_width_th:
                continue

        dt = df_5m['datetime'].iloc[i]

        # HTF sigmaフィルター
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # エントリー方向（BBタッチ+RSI）
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        # HTF 4h EMA20フィルター
        htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        # 固定TP/SL決済
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 300, n)):
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

        if hit is None:
            continue

        pnl_pips = (tp / pip_unit) if hit == 'tp' else -(sl / pip_unit)

        if hit == 'tp':
            wins += 1
            gross_profit += tp
            win_pnls.append(pnl_pips)
        else:
            losses += 1
            gross_loss += sl
            loss_pnls.append(-pnl_pips)

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':   trades,
        'win_rate': round(wins / trades * 100, 1),
        'pf':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'avg_win':  round(float(np.mean(win_pnls)), 2)  if win_pnls  else 0.0,
        'avg_loss': round(float(np.mean(loss_pnls)), 2) if loss_pnls else 0.0,
    }


def main():
    print('=== BB戦略 固定TP版BT (Stage2廃止・htf4h_only) ===')
    print(f'対象ペア: {list(TARGET_PAIRS.keys())}')
    print(f'TP = SL x tp_sl_ratio (固定)、Stage2トレーリングなし')
    print(f'フィルター: htf4h EMA20方向フィルター ON\n')
    print(f'  {"pair":>7} | {"PF":>6} | {"WR":>6} | {"N":>5} | {"avgWin(pip)":>11} | {"avgLoss(pip)":>12}')
    print(f'  {"-"*60}')

    rows = []
    for symbol, cfg in TARGET_PAIRS.items():
        res = simulate_fixed_tp(symbol, cfg)
        if res is None:
            print(f'  {symbol:>7} | データなし / 取引0')
            continue
        row = {'pair': symbol, **res}
        rows.append(row)
        print(f'  {symbol:>7} | '
              f'{res["pf"]:>6.3f} | '
              f'{res["win_rate"]:>5.1f}% | '
              f'{res["trades"]:>5} | '
              f'{res["avg_win"]:>+11.2f} | '
              f'{res["avg_loss"]:>+12.2f}')

    if not rows:
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    # CSV出力 (VPS/ローカル両対応)
    out_dir  = Path(__file__).parent
    out_csv  = str(out_dir / 'bb_fixedtp_bt_result.csv')
    df_out   = pd.DataFrame(rows)[['pair', 'pf', 'win_rate', 'trades', 'avg_win', 'avg_loss']]
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    # 判定基準サマリー (PF>1.2 / WR>50%)
    print('\n=== 判定 (PF>1.2 / WR>50%) ===')
    for r in rows:
        pf_ok = r['pf'] > 1.2
        wr_ok = r['win_rate'] > 50.0
        verdict = 'OK' if (pf_ok and wr_ok) else ('PF-NG' if not pf_ok else 'WR-NG')
        print(f'  {r["pair"]:>7}: {verdict}  PF={r["pf"]}  WR={r["win_rate"]}%  N={r["trades"]}')


if __name__ == '__main__':
    main()
