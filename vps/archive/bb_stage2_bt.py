# bb_stage2_bt.py
# GBPJPY TP / Stage2_activate 整合性検証
# 使用方法: python bb_stage2_bt.py
# 出力: 各案のPF/勝率/RR比較

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product

# ==============================
# パラメータ
# ==============================
SYMBOL = 'GBPJPY=X'
PERIOD = '2y'
INTERVAL = '1h'

BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14

# フィルター設定（現状維持）
F1_PARAM = 3       # F1: BB幅フィルター（ATR倍率）
F2_PIPS = 10       # F2: 価格フィルター（pips）
PIP_SIZE = 0.01    # GBPJPY pip

# SL設定
SL_ATR_MULT = 1.5

# 検証する案の組み合わせ
TP_RATIOS = [1.2, 1.25, 1.3, 1.5]          # TP = SL * ratio
STAGE2_ACTIVATES = [0.5, 0.7, 0.8, 0.9]   # stage2発動 = SL * activate

# stage2後のトレール距離（trail_monitor.pyのstage2_distance相当）
STAGE2_DISTANCE = 0.7   # SL * 0.7 をトレールキープ


# ==============================
# データ取得
# ==============================
def fetch_data():
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL, auto_adjust=True)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close']].dropna()
    return df


# ==============================
# インジケーター計算
# ==============================
def calc_indicators(df):
    # BB
    df['bb_mid'] = df['Close'].rolling(BB_PERIOD).mean()
    df['bb_std'] = df['Close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']

    # ATR
    df['hl'] = df['High'] - df['Low']
    df['hc'] = (df['High'] - df['Close'].shift(1)).abs()
    df['lc'] = (df['Low'] - df['Close'].shift(1)).abs()
    df['tr'] = df[['hl', 'hc', 'lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

    # BB幅
    df['bb_width'] = df['bb_upper'] - df['bb_lower']

    return df.dropna()


# ==============================
# エントリーシグナル（F2andF1）
# ==============================
def get_signals(df):
    signals = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        atr = row['atr']

        # F2: BB幅 >= F2_PIPS
        bb_width_pips = row['bb_width'] / PIP_SIZE
        if bb_width_pips < F2_PIPS:
            continue

        # F1: BB幅 >= ATR * F1_PARAM
        if row['bb_width'] < atr * F1_PARAM:
            continue

        # ロングシグナル: 前足がLBB以下でクローズ → 今足がLBB上
        if prev['Close'] <= prev['bb_lower'] and row['Close'] > row['bb_lower']:
            signals.append((i, 'long', row['Close'], atr))

        # ショートシグナル: 前足がUBB以上でクローズ → 今足がUBB下
        elif prev['Close'] >= prev['bb_upper'] and row['Close'] < row['bb_upper']:
            signals.append((i, 'short', row['Close'], atr))

    return signals


# ==============================
# 1トレードのシミュレーション
# ==============================
def simulate_trade(df, entry_idx, direction, entry_price, atr, tp_ratio, stage2_activate):
    sl_dist = atr * SL_ATR_MULT
    tp_dist = sl_dist * tp_ratio
    stage2_dist = sl_dist * stage2_activate
    trail_dist = sl_dist * STAGE2_DISTANCE

    if direction == 'long':
        sl = entry_price - sl_dist
        tp = entry_price + tp_dist
        stage2_trigger = entry_price + stage2_dist
    else:
        sl = entry_price + sl_dist
        tp = entry_price - tp_dist
        stage2_trigger = entry_price - stage2_dist

    stage2_active = False
    trail_sl = sl

    for j in range(entry_idx + 1, min(entry_idx + 200, len(df))):
        high = df.iloc[j]['High']
        low = df.iloc[j]['Low']

        if direction == 'long':
            # stage2チェック
            if not stage2_active and high >= stage2_trigger:
                stage2_active = True
                trail_sl = entry_price  # BE移動

            # トレールSL更新
            if stage2_active:
                new_trail = high - trail_dist
                trail_sl = max(trail_sl, new_trail)

            # TP
            if high >= tp:
                pnl = tp_dist
                return pnl, 'tp', stage2_active

            # SL/トレールSL
            sl_check = trail_sl if stage2_active else sl
            if low <= sl_check:
                pnl = -(entry_price - sl_check) if not stage2_active else (trail_sl - entry_price)
                return pnl, 'sl', stage2_active

        else:  # short
            if not stage2_active and low <= stage2_trigger:
                stage2_active = True
                trail_sl = entry_price

            if stage2_active:
                new_trail = low + trail_dist
                trail_sl = min(trail_sl, new_trail)

            if low <= tp:
                pnl = tp_dist
                return pnl, 'tp', stage2_active

            sl_check = trail_sl if stage2_active else sl
            if high >= sl_check:
                pnl = -(sl_check - entry_price) if not stage2_active else (entry_price - trail_sl)
                return pnl, 'sl', stage2_active

    # タイムアウト（200本）
    last_close = df.iloc[min(entry_idx + 199, len(df) - 1)]['Close']
    if direction == 'long':
        pnl = last_close - entry_price
    else:
        pnl = entry_price - last_close
    return pnl, 'timeout', stage2_active


# ==============================
# メイン
# ==============================
def run():
    print('データ取得中...')
    df = fetch_data()
    df = calc_indicators(df)
    signals = get_signals(df)
    print(f'シグナル数: {len(signals)}件\n')

    results = []

    for tp_ratio, stage2_act in product(TP_RATIOS, STAGE2_ACTIVATES):
        trades = []
        for entry_idx, direction, entry_price, atr in signals:
            pnl, exit_type, s2_fired = simulate_trade(
                df, entry_idx, direction, entry_price, atr,
                tp_ratio, stage2_act
            )
            trades.append({
                'pnl': pnl,
                'exit_type': exit_type,
                'stage2_fired': s2_fired
            })

        if not trades:
            continue

        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = len(wins) / len(pnls) * 100
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        pf = gross_profit / gross_loss if gross_loss > 0 else 999
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1e-9
        rr = avg_win / avg_loss if avg_loss > 0 else 0

        tp_count = sum(1 for t in trades if t['exit_type'] == 'tp')
        sl_count = sum(1 for t in trades if t['exit_type'] == 'sl')
        s2_fired_count = sum(1 for t in trades if t['stage2_fired'])
        tp_rate = tp_count / len(trades) * 100

        # 現状かどうかのフラグ
        is_current = (tp_ratio == 1.5 and stage2_act == 0.5)
        label = '[現状]' if is_current else '      '

        results.append({
            'label': label,
            'tp_ratio': tp_ratio,
            'stage2_act': stage2_act,
            'trades': len(trades),
            'win_rate': win_rate,
            'pf': pf,
            'rr': rr,
            'tp_rate': tp_rate,
            'sl_count': sl_count,
            's2_fired': s2_fired_count,
            'total_pnl': sum(pnls)
        })

    # ソート: PF降順
    results.sort(key=lambda x: x['pf'], reverse=True)

    # ===== 出力 =====
    print('=' * 90)
    print(f'{"":6} {"TP倍率":>6} {"S2発動":>6} | {"件数":>5} {"勝率%":>7} {"PF":>6} {"実RR":>6} {"TP率%":>7} {"累計pips":>9}')
    print('-' * 90)
    for r in results:
        print(
            f'{r["label"]:6} {r["tp_ratio"]:>6.2f} {r["stage2_act"]:>6.2f} | '
            f'{r["trades"]:>5} {r["win_rate"]:>7.1f} {r["pf"]:>6.2f} {r["rr"]:>6.2f} '
            f'{r["tp_rate"]:>7.1f} {r["total_pnl"]:>9.1f}'
        )
    print('=' * 90)

    # トップ3をハイライト
    print('\n--- トップ3案（PF基準）---')
    for r in results[:3]:
        print(
            f'  TP={r["tp_ratio"]:.2f} / Stage2={r["stage2_act"]:.2f}  '
            f'=> PF={r["pf"]:.2f} 勝率={r["win_rate"]:.1f}% RR={r["rr"]:.2f} TP到達率={r["tp_rate"]:.1f}%'
        )

    # 推奨案（案3相当: TP=1.25, stage2=0.7）
    rec = next((r for r in results if r['tp_ratio'] == 1.25 and r['stage2_act'] == 0.7), None)
    if rec:
        cur = next((r for r in results if r['label'] == '[現状]'), None)
        print('\n--- 案3（推奨: TP=1.25 / Stage2=0.7）vs 現状 ---')
        if cur:
            print(f'  現状  : PF={cur["pf"]:.2f} 勝率={cur["win_rate"]:.1f}% RR={cur["rr"]:.2f} TP率={cur["tp_rate"]:.1f}%')
        print(f'  推奨案: PF={rec["pf"]:.2f} 勝率={rec["win_rate"]:.1f}% RR={rec["rr"]:.2f} TP率={rec["tp_rate"]:.1f}%')


if __name__ == '__main__':
    run()