# bb_usdcad_rr_grid_bt.py
# USDCAD RR改善グリッドサーチ
# フィルター固定: ADX>=25 + HTF幅<0.008
# 使用方法: python bb_usdcad_rr_grid_bt.py

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product

# ==============================
# パラメータ
# ==============================
SYMBOL = 'USDCAD=X'
PERIOD = '2y'
BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14
RSI_BUY_MAX = 45
RSI_SELL_MIN = 55

# 固定フィルター（BT済み最良案）
ADX_TH = 25
HTF_WIDTH_TH = 0.008

# Stage2 (trail_monitor v5準拠)
STAGE2_ACTIVATE = 0.7
STAGE2_DISTANCE = 0.7

# グリッド
TP_RATIOS  = [1.5, 2.0, 2.5, 3.0]
SL_MULTS   = [1.0, 1.2, 1.5]


# ==============================
# データ取得
# ==============================
def fetch_data():
    print('1hデータ取得中...')
    df_1h = yf.download(SYMBOL, period=PERIOD, interval='1h', auto_adjust=True)
    df_1h.columns = [c[0] if isinstance(c, tuple) else c for c in df_1h.columns]
    df_1h = df_1h[['Open', 'High', 'Low', 'Close']].dropna()

    print('4hデータ取得中...')
    df_4h = yf.download(SYMBOL, period=PERIOD, interval='4h', auto_adjust=True)
    df_4h.columns = [c[0] if isinstance(c, tuple) else c for c in df_4h.columns]
    df_4h = df_4h[['Open', 'High', 'Low', 'Close']].dropna()

    return df_1h, df_4h


# ==============================
# インジケーター計算
# ==============================
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_adx(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close']
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return dx.ewm(span=period, adjust=False).mean()


def calc_indicators_1h(df):
    df = df.copy()
    df['bb_mid']   = df['Close'].rolling(BB_PERIOD).mean()
    df['bb_std']   = df['Close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low']  - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    df['rsi'] = calc_rsi(df['Close'])
    df['adx'] = calc_adx(df)
    return df.dropna()


def calc_indicators_4h(df):
    df = df.copy()
    df['bb_mid']   = df['Close'].rolling(BB_PERIOD).mean()
    df['bb_std']   = df['Close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']
    df['bb_width_norm'] = (df['bb_upper'] - df['bb_lower']) / (df['bb_mid'] + 1e-9)
    return df.dropna()


# ==============================
# エントリーシグナル（RSIフィルター込み）
# ==============================
def get_signals(df):
    signals = []
    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        if prev['Close'] <= prev['bb_lower'] and row['Close'] > row['bb_lower']:
            if row['rsi'] <= RSI_BUY_MAX:
                signals.append((i, df.index[i], 'long', row['Close'], row['atr']))
        elif prev['Close'] >= prev['bb_upper'] and row['Close'] < row['bb_upper']:
            if row['rsi'] >= RSI_SELL_MIN:
                signals.append((i, df.index[i], 'short', row['Close'], row['atr']))
    return signals


# ==============================
# 固定フィルター適用（ADX>=25 + HTF幅<0.008）
# ==============================
def apply_filters(df_1h, df_4h, signals):
    filtered = []
    for entry_idx, entry_ts, direction, entry_price, atr in signals:
        # ADXフィルター
        if df_1h.iloc[entry_idx]['adx'] < ADX_TH:
            continue
        # HTF BB幅フィルター
        past_4h = df_4h[df_4h.index <= entry_ts]
        if len(past_4h) > 0:
            w = past_4h.iloc[-1]['bb_width_norm']
            if w >= HTF_WIDTH_TH:
                continue
        filtered.append((entry_idx, entry_ts, direction, entry_price, atr))
    return filtered


# ==============================
# 1トレードシミュレーション
# ==============================
def simulate_trade(df, entry_idx, direction, entry_price, atr, sl_mult, tp_ratio):
    sl_dist = atr * sl_mult
    tp_dist = sl_dist * tp_ratio
    s2_trigger_dist = sl_dist * STAGE2_ACTIVATE
    trail_dist = sl_dist * STAGE2_DISTANCE

    if direction == 'long':
        sl         = entry_price - sl_dist
        tp         = entry_price + tp_dist
        s2_trigger = entry_price + s2_trigger_dist
    else:
        sl         = entry_price + sl_dist
        tp         = entry_price - tp_dist
        s2_trigger = entry_price - s2_trigger_dist

    stage2_active = False
    trail_sl = sl

    for j in range(entry_idx + 1, min(entry_idx + 200, len(df))):
        high = df.iloc[j]['High']
        low  = df.iloc[j]['Low']

        if direction == 'long':
            if not stage2_active and high >= s2_trigger:
                stage2_active = True
                trail_sl = entry_price
            if stage2_active:
                trail_sl = max(trail_sl, high - trail_dist)
            if high >= tp:
                return tp_dist, 'tp'
            sl_check = trail_sl if stage2_active else sl
            if low <= sl_check:
                pnl = (trail_sl - entry_price) if stage2_active else -sl_dist
                return pnl, 'sl'
        else:
            if not stage2_active and low <= s2_trigger:
                stage2_active = True
                trail_sl = entry_price
            if stage2_active:
                trail_sl = min(trail_sl, low + trail_dist)
            if low <= tp:
                return tp_dist, 'tp'
            sl_check = trail_sl if stage2_active else sl
            if high >= sl_check:
                pnl = (entry_price - trail_sl) if stage2_active else -sl_dist
                return pnl, 'sl'

    last_close = df.iloc[min(entry_idx + 199, len(df) - 1)]['Close']
    pnl = (last_close - entry_price) if direction == 'long' else (entry_price - last_close)
    return pnl, 'timeout'


# ==============================
# メイン
# ==============================
def run():
    df_1h_raw, df_4h_raw = fetch_data()
    df_1h = calc_indicators_1h(df_1h_raw)
    df_4h = calc_indicators_4h(df_4h_raw)

    signals  = get_signals(df_1h)
    filtered = apply_filters(df_1h, df_4h, signals)
    print(f'ベースシグナル: {len(signals)}件 -> フィルター後: {len(filtered)}件\n')

    results = []
    total = len(TP_RATIOS) * len(SL_MULTS)
    done = 0

    for sl_mult, tp_ratio in product(SL_MULTS, TP_RATIOS):
        trades = []
        for entry_idx, entry_ts, direction, entry_price, atr in filtered:
            pnl, exit_type = simulate_trade(
                df_1h, entry_idx, direction, entry_price, atr, sl_mult, tp_ratio
            )
            trades.append({'pnl': pnl, 'exit_type': exit_type})

        done += 1
        n = len(trades)
        if n < 5:
            continue

        pnls   = [t['pnl'] for t in trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / n * 100
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1e-9
        pf = gp / gl
        avg_w = np.mean(wins) if wins else 0
        avg_l = abs(np.mean(losses)) if losses else 1e-9
        rr    = avg_w / avg_l
        tp_rate = sum(1 for t in trades if t['exit_type'] == 'tp') / n * 100

        is_baseline = (sl_mult == 1.5 and tp_ratio == 1.5)

        results.append({
            'baseline': is_baseline,
            'sl_mult':  sl_mult,
            'tp_ratio': tp_ratio,
            'n':        n,
            'win_rate': win_rate,
            'pf':       pf,
            'rr':       rr,
            'tp_rate':  tp_rate,
            'total_pnl': sum(pnls),
        })

    # PF降順ソート
    results.sort(key=lambda x: x['pf'], reverse=True)

    # ===== 全結果表示 =====
    print('=' * 80)
    print(f'{"":7} {"SL倍率":>6} {"TP倍率":>6} | '
          f'{"件数":>5} {"勝率%":>7} {"PF":>6} {"RR":>6} {"TP率%":>7} {"累計pips":>9}')
    print('-' * 80)
    for r in results:
        label = '[現状]' if r['baseline'] else '      '
        print(
            f'{label:7} ATR*{r["sl_mult"]:.1f} SL*{r["tp_ratio"]:.1f} | '
            f'{r["n"]:>5} {r["win_rate"]:>7.1f} {r["pf"]:>6.2f} {r["rr"]:>6.2f} '
            f'{r["tp_rate"]:>7.1f} {r["total_pnl"]:>9.1f}'
        )
    print('=' * 80)

    # ===== PF>=1.0 かつ 件数>=30 のみ抽出 =====
    print('\n--- PF>=1.0 かつ 件数>=30 の案 ---')
    passing = [r for r in results if r['pf'] >= 1.0 and r['n'] >= 30]
    if passing:
        print(f'{"SL倍率":>8} {"TP倍率":>6} | {"件数":>5} {"勝率%":>7} {"PF":>6} {"RR":>6} {"TP率%":>7}')
        print('-' * 60)
        for r in passing:
            print(
                f'  ATR*{r["sl_mult"]:.1f}  SL*{r["tp_ratio"]:.1f} | '
                f'{r["n"]:>5} {r["win_rate"]:>7.1f} {r["pf"]:>6.2f} '
                f'{r["rr"]:>6.2f} {r["tp_rate"]:>7.1f}'
            )
    else:
        print('  該当なし（PF>=1.0 かつ 件数>=30 を満たす組み合わせなし）')
        # 条件緩和: PF>=0.9 かつ 件数>=20
        fallback = [r for r in results if r['pf'] >= 0.9 and r['n'] >= 20]
        if fallback:
            print('\n  --- 緩和条件（PF>=0.9 かつ 件数>=20）---')
            for r in fallback[:5]:
                print(
                    f'  ATR*{r["sl_mult"]:.1f} SL*{r["tp_ratio"]:.1f} => '
                    f'PF={r["pf"]:.2f} 勝率={r["win_rate"]:.1f}% '
                    f'RR={r["rr"]:.2f} TP率={r["tp_rate"]:.1f}% 件数={r["n"]}'
                )

    # ===== 現状との比較 =====
    baseline = next((r for r in results if r['baseline']), None)
    if baseline:
        print(f'\n--- 現状（ATR*1.5 / SL*1.5） ---')
        print(f'  PF={baseline["pf"]:.2f} 勝率={baseline["win_rate"]:.1f}% '
              f'RR={baseline["rr"]:.2f} TP率={baseline["tp_rate"]:.1f}% 件数={baseline["n"]}')


if __name__ == '__main__':
    run()