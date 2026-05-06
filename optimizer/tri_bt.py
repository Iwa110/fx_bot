"""
tri_bt.py - TRI戦略（EUR/GBP三角裁定）バックテスト & グリッドサーチ
出力: best_params (PF/勝率/n/平均RR) + 上位10件表示
"""

import yfinance as yf
import pandas as pd
import itertools

# ===== データ取得 =====
def load_data():
    print('[INFO] データ取得中 (yfinance period=2y interval=1h)...')
    tickers = ['EURUSD=X', 'GBPUSD=X', 'EURGBP=X']
    dfs = {}
    for t in tickers:
        df = yf.download(t, period='2y', interval='1h', auto_adjust=True, progress=False)
        df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
        dfs[t] = df
        print(f'  {t}: {len(df)} bars')
    eu = dfs['EURUSD=X'][['open', 'high', 'low', 'close']].copy()
    gu = dfs['GBPUSD=X'][['open', 'high', 'low', 'close']].copy()
    eg = dfs['EURGBP=X'][['open', 'high', 'low', 'close']].copy()
    # 共通インデックスに揃える
    idx = eu.index.intersection(gu.index).intersection(eg.index)
    eu = eu.loc[idx]
    gu = gu.loc[idx]
    eg = eg.loc[idx]
    print(f'[INFO] 共通バー数: {len(idx)}')
    return eu, gu, eg

# ===== バックテスト =====
SPREAD_COST = 0.00012  # EURGBP 1.2pips 往復

def simulate_tri(eu, gu, eg, params):
    """
    params:
      entry_th: 乖離閾値
      tp_ratio: TP = abs(diff) * tp_ratio
      sl_th:    SL固定距離
    戻り値: (pf, win_rate, n_trades, avg_rr)
    """
    entry_th = params['entry_th']
    tp_ratio = params['tp_ratio']
    sl_th    = params['sl_th']

    eu_mid = (eu['open'] + eu['close']) / 2
    gu_mid = (gu['open'] + gu['close']) / 2
    eg_mid = (eg['open'] + eg['close']) / 2

    theory = eu_mid / gu_mid
    diff   = eg_mid - theory

    n = len(diff)
    in_pos    = False
    direction = None
    entry_price = 0.0
    tp_price  = 0.0
    sl_price  = 0.0
    tp_dist   = 0.0
    sl_dist   = 0.0

    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss   = 0.0
    rr_list = []

    for i in range(n - 1):
        if in_pos:
            # 次足のhigh/lowでTP/SL判定
            hi = eg['high'].iloc[i + 1]
            lo = eg['low'].iloc[i + 1]
            if direction == 'sell':
                if lo <= tp_price:
                    profit = tp_dist - SPREAD_COST
                    gross_profit += profit
                    rr_list.append(tp_dist / sl_dist if sl_dist > 0 else 0)
                    wins += 1
                    in_pos = False
                elif hi >= sl_price:
                    loss = sl_dist + SPREAD_COST
                    gross_loss += loss
                    losses += 1
                    in_pos = False
            else:  # buy
                if hi >= tp_price:
                    profit = tp_dist - SPREAD_COST
                    gross_profit += profit
                    rr_list.append(tp_dist / sl_dist if sl_dist > 0 else 0)
                    wins += 1
                    in_pos = False
                elif lo <= sl_price:
                    loss = sl_dist + SPREAD_COST
                    gross_loss += loss
                    losses += 1
                    in_pos = False
            continue

        d = diff.iloc[i]
        if d >= entry_th:
            direction = 'sell'
        elif d <= -entry_th:
            direction = 'buy'
        else:
            continue

        in_pos      = True
        entry_price = eg_mid.iloc[i]
        tp_dist     = abs(d) * tp_ratio
        sl_dist     = sl_th
        if direction == 'sell':
            tp_price = entry_price - tp_dist
            sl_price = entry_price + sl_dist
        else:
            tp_price = entry_price + tp_dist
            sl_price = entry_price - sl_dist

    n_trades = wins + losses
    if n_trades == 0:
        return 0.0, 0.0, 0, 0.0

    win_rate = wins / n_trades
    pf       = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    avg_rr   = sum(rr_list) / len(rr_list) if rr_list else 0.0
    return pf, win_rate, n_trades, avg_rr

# ===== グリッドサーチ =====
def grid_search(eu, gu, eg):
    entry_range   = [0.0003, 0.0005, 0.0007, 0.0009, 0.0012, 0.0015]
    tp_ratio_range = [0.4, 0.5, 0.6, 0.7, 0.8]
    sl_range      = [0.0008, 0.0012, 0.0015, 0.0020, 0.0025]

    total = len(entry_range) * len(tp_ratio_range) * len(sl_range)
    print(f'[INFO] グリッドサーチ開始: {total}組み合わせ')

    results = []
    for entry_th, tp_ratio, sl_th in itertools.product(entry_range, tp_ratio_range, sl_range):
        params = {'entry_th': entry_th, 'tp_ratio': tp_ratio, 'sl_th': sl_th}
        pf, wr, n, rr = simulate_tri(eu, gu, eg, params)
        if n < 20:
            continue
        results.append({
            'entry_th': entry_th,
            'tp_ratio': tp_ratio,
            'sl_th':    sl_th,
            'pf':       pf,
            'win_rate': wr,
            'n':        n,
            'avg_rr':   rr,
        })

    results.sort(key=lambda x: x['pf'], reverse=True)
    return results

# ===== メイン =====
def main():
    eu, gu, eg = load_data()
    results = grid_search(eu, gu, eg)

    if not results:
        print('[WARN] n>=20 の組み合わせが存在しません。best_params 未確定。')
        return None

    print(f'\n[INFO] n>=20 の有効組み合わせ: {len(results)}件')
    print('\n=== 上位10件 (PF降順) ===')
    print(f'{"entry_th":>10} {"tp_ratio":>9} {"sl_th":>8} {"PF":>7} {"WR":>7} {"n":>5} {"avgRR":>7}')
    for r in results[:10]:
        print(
            f'{r["entry_th"]:>10.4f} {r["tp_ratio"]:>9.1f} {r["sl_th"]:>8.4f} '
            f'{r["pf"]:>7.3f} {r["win_rate"]:>7.1%} {r["n"]:>5} {r["avg_rr"]:>7.3f}'
        )

    best = results[0]
    print(f'\n=== best_params ===')
    print(f'  entry_th = {best["entry_th"]}')
    print(f'  tp_ratio = {best["tp_ratio"]}')
    print(f'  sl_th    = {best["sl_th"]}')
    print(f'  PF={best["pf"]:.3f}  WR={best["win_rate"]:.1%}  n={best["n"]}  avgRR={best["avg_rr"]:.3f}')
    return best

if __name__ == '__main__':
    main()
