"""
v21_fulldata_bt.py - BB戦略 v21 全データ期間 再バックテスト
- GBPJPY: htf4h_only  + sl_atr_mult=3.0 + fixed_tp_rr=1.5 (Stage2なし)
- USDJPY: htf4h_rsi   + sl_atr_mult=3.0 + fixed_tp_rr=1.5 (Stage2なし)
- tail()なし: df_5m 全件を使用（サンプルバイアス排除）
出力: optimizer/v21_fulldata_bt.csv
"""
import csv
import importlib.util
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

OPT_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')
OUT_CSV = OPT_DIR / 'v21_fulldata_bt.csv'

# v21 確定パラメータ
PAIRS_CFG = {
    'GBPJPY': {
        'is_jpy':      True,
        'pip_unit':    0.01,
        'bb_sigma':    1.5,
        'sl_atr_mult': 3.0,
        'fixed_tp_rr': 1.5,
        'htf_mode':    'htf4h_only',
    },
    'USDJPY': {
        'is_jpy':      True,
        'pip_unit':    0.01,
        'bb_sigma':    2.0,
        'sl_atr_mult': 3.0,
        'fixed_tp_rr': 1.5,
        'htf_mode':    'htf4h_rsi',
    },
}


def load_bt_module():
    bt_path = OPT_DIR / 'backtest.py'
    spec = importlib.util.spec_from_file_location('backtest', bt_path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    return bt


def build_htf4h_rsi_lookup(df_1h, ema_period=20, rsi_period=14):
    """
    1h CSVを4hにリサンプルしてEMA20+RSI14を計算。
    +1: close > EMA20 AND RSI < 55 (buy許可)
    -1: close < EMA20 AND RSI > 45 (sell許可)
     0: 条件不成立（エントリースキップ）
    """
    df = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20'] = df4h['close'].ewm(span=ema_period, adjust=False).mean()

    delta = df4h['close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=rsi_period - 1, min_periods=rsi_period).mean()
    avg_l = loss.ewm(com=rsi_period - 1, min_periods=rsi_period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    df4h['rsi'] = 100 - (100 / (1 + rs))

    cond_buy  = (df4h['close'] > df4h['ema20']) & (df4h['rsi'] < 55)
    cond_sell = (df4h['close'] < df4h['ema20']) & (df4h['rsi'] > 45)
    df4h['signal'] = np.where(cond_buy, 1, np.where(cond_sell, -1, 0))
    return df4h['signal']


def simulate_v21(symbol, pair_cfg, bt):
    cfg = bt.get_base_params()
    cfg.update(pair_cfg)

    df_5m = bt.load_csv(symbol, '5m')
    df_1h = bt.load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        print(f'[ERROR] {symbol}: CSVなし')
        return None

    # 全データ使用（tail()なし）
    df_5m = df_5m.reset_index(drop=True)

    date_from = df_5m['datetime'].iloc[0].strftime('%Y-%m-%d')
    date_to   = df_5m['datetime'].iloc[-1].strftime('%Y-%m-%d')
    print(f'  {symbol}: {len(df_5m)} bars  {date_from} ~ {date_to}')

    close   = df_5m['close']
    bb_u, bb_l, _, _ = bt.calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = bt.calc_rsi(close, cfg['rsi_period'])
    atr     = bt.calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = bt.build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    htf_mode = pair_cfg['htf_mode']
    if htf_mode == 'htf4h_only':
        htf4h_lkp = bt.build_htf4h_ema_lookup(df_1h)
    else:
        htf4h_lkp = build_htf4h_rsi_lookup(df_1h)

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    win_pnl_sum = loss_pnl_sum = 0.0
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    last_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['fixed_tp_rr']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # 1h HTFレンジフィルター（|sigma_pos| < 1.0）
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # 5m BBエントリー + RSIフィルター
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

        # 4h HTFフィルター
        idx4h = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if idx4h < 0:
            continue
        sig4h = htf4h_lkp.iloc[idx4h]
        if direction == 'buy'  and sig4h != 1:
            continue
        if direction == 'sell' and sig4h != -1:
            continue

        # エントリー価格・TP/SL
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl

        hit        = None
        exit_price = None

        for j in range(i + 1, min(i + 300, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]

            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'
                    exit_price = sl_price
                    break
                if h >= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break
            else:
                if h >= sl_price:
                    hit = 'sl'
                    exit_price = sl_price
                    break
                if l <= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break

        if hit is None or exit_price is None:
            continue

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price
        cum_pnl += pnl

        if pnl > 0:
            wins += 1
            gross_profit += pnl
            win_pnl_sum  += pnl
        else:
            losses += 1
            gross_loss   += abs(pnl)
            loss_pnl_sum += abs(pnl)

        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd:
            max_dd = dd

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    pip_unit = cfg['pip_unit']
    pf       = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0
    wr       = round(wins / trades * 100, 1)
    avg_win  = round(win_pnl_sum  / wins   / pip_unit, 1) if wins   > 0 else 0.0
    avg_loss = round(loss_pnl_sum / losses / pip_unit, 1) if losses > 0 else 0.0
    max_dd_p = round(max_dd / pip_unit, 1)

    return {
        'pair':          symbol,
        'htf_mode':      htf_mode,
        'sl_atr_mult':   cfg['sl_atr_mult'],
        'tp_rr':         cfg['fixed_tp_rr'],
        'PF':            pf,
        'WR':            wr,
        'N':             trades,
        'MaxDD':         max_dd_p,
        'avg_win_pips':  avg_win,
        'avg_loss_pips': avg_loss,
        'date_from':     date_from,
        'date_to':       date_to,
    }


def main():
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] === BB戦略 v21 全データ期間 再バックテスト ===')

    bt = load_bt_module()

    results = []
    for pair, pcfg in PAIRS_CFG.items():
        ts = datetime.now().strftime('%H:%M:%S')
        print(f'[{ts}] {pair} ({pcfg["htf_mode"]}) ...')
        res = simulate_v21(pair, pcfg, bt)
        if res is None:
            print(f'  結果なし（トレード0 or CSVなし）')
            continue
        results.append(res)
        print(f'  PF={res["PF"]}  WR={res["WR"]}%  N={res["N"]}  MaxDD={res["MaxDD"]}pips')
        print(f'  avg_win={res["avg_win_pips"]}pips  avg_loss={res["avg_loss_pips"]}pips')

    if not results:
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    fields = ['pair', 'htf_mode', 'sl_atr_mult', 'tp_rr', 'PF', 'WR', 'N',
              'MaxDD', 'avg_win_pips', 'avg_loss_pips', 'date_from', 'date_to']
    with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] 保存完了: {OUT_CSV}')


if __name__ == '__main__':
    main()
