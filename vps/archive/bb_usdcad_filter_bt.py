# bb_usdcad_filter_bt.py
# USDCAD フィルター強化検証
# 使用方法: python bb_usdcad_filter_bt.py
# 出力: 各フィルター組み合わせのPF/勝率/RR比較

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product

# ==============================
# パラメータ（bb_monitor.py v13準拠）
# ==============================
SYMBOL_1H = 'USDCAD=X'
SYMBOL_4H = 'USDCAD=X'
PERIOD = '2y'

BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_RATIO = 1.5        # v13現状
STAGE2_ACTIVATE = 0.7 # trail_monitor v5準拠
STAGE2_DISTANCE = 0.7
PIP_SIZE = 0.0001     # USDCAD pip

# RSIフィルター（v13現状）
RSI_BUY_MAX = 45
RSI_SELL_MIN = 55

# ==============================
# 検証するフィルター候補
# ==============================
# 候補A: ADX閾値
ADX_THRESHOLDS = [0, 20, 25, 30]    # 0=なし

# 候補B: HTF(4h) BB幅閾値（正規化幅 = BB幅/mid）
HTF_WIDTH_THRESHOLDS = [0.0, 0.006, 0.008, 0.010]  # 0.0=なし

# 候補C: セッションフィルター
SESSION_OPTIONS = [False, True]     # True=NYのみ(13-21UTC)
SESSION_START = 13
SESSION_END = 21


# ==============================
# データ取得
# ==============================
def fetch_data():
    print('1hデータ取得中...')
    df_1h = yf.download(SYMBOL_1H, period=PERIOD, interval='1h', auto_adjust=True)
    df_1h.columns = [c[0] if isinstance(c, tuple) else c for c in df_1h.columns]
    df_1h = df_1h[['Open', 'High', 'Low', 'Close']].dropna()

    print('4hデータ取得中...')
    df_4h = yf.download(SYMBOL_4H, period=PERIOD, interval='4h', auto_adjust=True)
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
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return dx.ewm(span=period, adjust=False).mean()


def calc_indicators_1h(df):
    df = df.copy()
    df['bb_mid'] = df['Close'].rolling(BB_PERIOD).mean()
    df['bb_std'] = df['Close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']
    df['bb_width'] = df['bb_upper'] - df['bb_lower']
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    df['rsi'] = calc_rsi(df['Close'])
    df['adx'] = calc_adx(df)
    df['hour'] = df.index.hour
    return df.dropna()


def calc_indicators_4h(df):
    df = df.copy()
    df['bb_mid'] = df['Close'].rolling(BB_PERIOD).mean()
    df['bb_std'] = df['Close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']
    # 正規化BB幅
    df['bb_width_norm'] = (df['bb_upper'] - df['bb_lower']) / (df['bb_mid'] + 1e-9)
    return df.dropna()


# ==============================
# エントリーシグナル（filter_type=None + RSIフィルター）
# ==============================
def get_signals(df):
    signals = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # ロング: 前足LBB以下クローズ → 今足LBB上
        if prev['Close'] <= prev['bb_lower'] and row['Close'] > row['bb_lower']:
            if row['rsi'] <= RSI_BUY_MAX:
                signals.append((i, df.index[i], 'long', row['Close'], row['atr']))

        # ショート: 前足UBB以上クローズ → 今足UBB下
        elif prev['Close'] >= prev['bb_upper'] and row['Close'] < row['bb_upper']:
            if row['rsi'] >= RSI_SELL_MIN:
                signals.append((i, df.index[i], 'short', row['Close'], row['atr']))

    return signals


# ==============================
# フィルター判定
# ==============================
def check_filters(df_1h, df_4h_indexed, entry_idx, entry_ts, direction, cfg):
    # 候補A: ADX
    if cfg['adx_th'] > 0:
        adx_val = df_1h.iloc[entry_idx]['adx']
        if adx_val < cfg['adx_th']:
            return False, 'ADX'

    # 候補B: HTF BB幅
    if cfg['htf_width'] > 0.0:
        past_4h = df_4h_indexed[df_4h_indexed.index <= entry_ts]
        if len(past_4h) > 0:
            w = past_4h.iloc[-1]['bb_width_norm']
            # 幅が閾値以上 = トレンド相場 → 逆張り禁止
            if w >= cfg['htf_width']:
                return False, 'HTF_width'

    # 候補C: セッション
    if cfg['session']:
        h = df_1h.iloc[entry_idx]['hour']
        if not (SESSION_START <= h < SESSION_END):
            return False, 'Session'

    return True, 'pass'


# ==============================
# 1トレードシミュレーション（stage2込み）
# ==============================
def simulate_trade(df, entry_idx, direction, entry_price, atr):
    sl_dist = atr * SL_ATR_MULT
    tp_dist = sl_dist * TP_RATIO
    stage2_dist = sl_dist * STAGE2_ACTIVATE
    trail_dist = sl_dist * STAGE2_DISTANCE

    if direction == 'long':
        sl = entry_price - sl_dist
        tp = entry_price + tp_dist
        s2_trigger = entry_price + stage2_dist
    else:
        sl = entry_price + sl_dist
        tp = entry_price - tp_dist
        s2_trigger = entry_price - stage2_dist

    stage2_active = False
    trail_sl = sl

    for j in range(entry_idx + 1, min(entry_idx + 200, len(df))):
        high = df.iloc[j]['High']
        low = df.iloc[j]['Low']

        if direction == 'long':
            if not stage2_active and high >= s2_trigger:
                stage2_active = True
                trail_sl = entry_price
            if stage2_active:
                trail_sl = max(trail_sl, high - trail_dist)
            if high >= tp:
                return tp_dist, 'tp', stage2_active
            sl_check = trail_sl if stage2_active else sl
            if low <= sl_check:
                pnl = (trail_sl - entry_price) if stage2_active else -sl_dist
                return pnl, 'sl', stage2_active
        else:
            if not stage2_active and low <= s2_trigger:
                stage2_active = True
                trail_sl = entry_price
            if stage2_active:
                trail_sl = min(trail_sl, low + trail_dist)
            if low <= tp:
                return tp_dist, 'tp', stage2_active
            sl_check = trail_sl if stage2_active else sl
            if high >= sl_check:
                pnl = (entry_price - trail_sl) if stage2_active else -sl_dist
                return pnl, 'sl', stage2_active

    last_close = df.iloc[min(entry_idx + 199, len(df) - 1)]['Close']
    pnl = (last_close - entry_price) if direction == 'long' else (entry_price - last_close)
    return pnl, 'timeout', stage2_active


# ==============================
# メイン
# ==============================
def run():
    df_1h_raw, df_4h_raw = fetch_data()
    df_1h = calc_indicators_1h(df_1h_raw)
    df_4h = calc_indicators_4h(df_4h_raw)
    signals = get_signals(df_1h)
    print(f'ベースシグナル数: {len(signals)}件\n')

    results = []

    configs = list(product(ADX_THRESHOLDS, HTF_WIDTH_THRESHOLDS, SESSION_OPTIONS))
    total = len(configs)

    for i, (adx_th, htf_w, sess) in enumerate(configs):
        cfg = {'adx_th': adx_th, 'htf_width': htf_w, 'session': sess}
        trades = []
        skip = {'ADX': 0, 'HTF_width': 0, 'Session': 0}

        for entry_idx, entry_ts, direction, entry_price, atr in signals:
            ok, reason = check_filters(df_1h, df_4h, entry_idx, entry_ts, direction, cfg)
            if not ok:
                skip[reason] = skip.get(reason, 0) + 1
                continue
            pnl, exit_type, s2 = simulate_trade(df_1h, entry_idx, direction, entry_price, atr)
            trades.append({'pnl': pnl, 'exit_type': exit_type, 's2': s2})

        if len(trades) < 5:
            continue

        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) * 100
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1e-9
        pf = gp / gl
        avg_w = np.mean(wins) if wins else 0
        avg_l = abs(np.mean(losses)) if losses else 1e-9
        rr = avg_w / avg_l
        tp_rate = sum(1 for t in trades if t['exit_type'] == 'tp') / len(trades) * 100

        is_baseline = (adx_th == 0 and htf_w == 0.0 and not sess)
        results.append({
            'baseline': is_baseline,
            'adx_th': adx_th,
            'htf_w': htf_w,
            'sess': sess,
            'n': len(trades),
            'win_rate': win_rate,
            'pf': pf,
            'rr': rr,
            'tp_rate': tp_rate,
            'total_pnl': sum(pnls),
            'skip_adx': skip.get('ADX', 0),
            'skip_htf': skip.get('HTF_width', 0),
            'skip_sess': skip.get('Session', 0),
        })

        if (i + 1) % 8 == 0:
            print(f'  進捗: {i + 1}/{total}')

    results.sort(key=lambda x: x['pf'], reverse=True)

    # ===== 出力 =====
    print('\n' + '=' * 100)
    print(f'{"":7} {"ADX>=":>6} {"HTF幅<":>7} {"NY時間":>6} | '
          f'{"件数":>5} {"勝率%":>7} {"PF":>6} {"RR":>6} {"TP率%":>7} {"累計pips":>9}')
    print('-' * 100)

    for r in results:
        label = '[現状]' if r['baseline'] else '      '
        sess_str = 'Yes' if r['sess'] else 'No '
        htf_str = f'{r["htf_w"]:.3f}' if r['htf_w'] > 0 else '  -  '
        adx_str = f'{r["adx_th"]:>4}' if r['adx_th'] > 0 else '   -'
        print(
            f'{label:7} {adx_str:>6} {htf_str:>7} {sess_str:>6} | '
            f'{r["n"]:>5} {r["win_rate"]:>7.1f} {r["pf"]:>6.2f} {r["rr"]:>6.2f} '
            f'{r["tp_rate"]:>7.1f} {r["total_pnl"]:>9.1f}'
        )

    print('=' * 100)

    # トップ5
    print('\n--- トップ5案（PF基準・件数>=20）---')
    top5 = [r for r in results if r['n'] >= 20][:5]
    for r in top5:
        filters = []
        if r['adx_th'] > 0: filters.append(f'ADX>={r["adx_th"]}')
        if r['htf_w'] > 0: filters.append(f'HTF幅<{r["htf_w"]:.3f}')
        if r['sess']: filters.append('NY時間')
        f_str = ' + '.join(filters) if filters else 'フィルターなし（現状）'
        print(f'  {f_str}')
        print(f'    => PF={r["pf"]:.2f} 勝率={r["win_rate"]:.1f}% RR={r["rr"]:.2f} '
              f'TP率={r["tp_rate"]:.1f}% 件数={r["n"]}')

    # 現状との比較
    baseline = next((r for r in results if r['baseline']), None)
    if baseline:
        print(f'\n--- 現状（フィルターなし） ---')
        print(f'  PF={baseline["pf"]:.2f} 勝率={baseline["win_rate"]:.1f}% '
              f'RR={baseline["rr"]:.2f} TP率={baseline["tp_rate"]:.1f}% 件数={baseline["n"]}')

        print('\n--- 現状比PF改善ランキング（件数>=20） ---')
        improved = [r for r in results if r['n'] >= 20 and r['pf'] > baseline['pf']]
        for r in improved[:5]:
            filters = []
            if r['adx_th'] > 0: filters.append(f'ADX>={r["adx_th"]}')
            if r['htf_w'] > 0: filters.append(f'HTF幅<{r["htf_w"]:.3f}')
            if r['sess']: filters.append('NY時間')
            f_str = ' + '.join(filters)
            delta_pf = r['pf'] - baseline['pf']
            delta_wr = r['win_rate'] - baseline['win_rate']
            print(f'  [{f_str}] PF+{delta_pf:.2f} 勝率+{delta_wr:.1f}pt 件数={r["n"]}')


if __name__ == '__main__':
    run()