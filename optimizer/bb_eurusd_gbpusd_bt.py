"""
bb_eurusd_gbpusd_bt.py  EURUSD/GBPUSD エントリー条件強化 グリッドサーチBT
v1 (2026-05-12)
足種: 1h (main) / 4h resample (HTF)  ※ VPSでは5mデータ推奨
"""
import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR   = str(Path(__file__).parent.parent / 'data')
OUTPUT_CSV = str(Path(__file__).parent / 'eurusd_gbpusd_bt_result.csv')

TARGET_PAIRS = ['EURUSD', 'GBPUSD']

# bb_monitor v17準拠パラメータ
BB_PERIOD     = 20
BB_SIGMA      = 1.5
HTF_RANGE_SIG = 1.0   # 4h sigma range limit
RSI_PERIOD    = 14
RSI_BUY_MAX   = 40.0  # ベースRSI閾値（buy）
RSI_SELL_MIN  = 60.0  # ベースRSI閾値（sell）
ATR_PERIOD    = 14
COOLDOWN_BARS = 3
SPREAD = {
    'EURUSD': 0.0002,
    'GBPUSD': 0.0002,
}

GRID = {
    'filter':      ['htf4h_only', 'htf4h_and_rsi', 'htf4h_and_volume', 'htf4h_and_bb_width'],
    'rsi_th':      [40, 45, 50],
    'bb_width_th': [0.0010, 0.0015, 0.0020],
    'sl_atr_mult': [1.2, 1.5, 1.8],
    'min_rr':      [1.5, 2.0],
}
MIN_N = 30


def log_print(msg):
    print(msg)


# ==========================================
# データ読み込み
# ==========================================
def load_1h(symbol):
    """1h CSVを読み込む。ローカル/VPS両対応（VPSは5mが優先）"""
    candidates = [
        Path(DATA_DIR) / f'{symbol}_5m.csv',
        Path(DATA_DIR) / f'{symbol}_M5.csv',
        Path(DATA_DIR) / f'{symbol}_1h.csv',
        Path(DATA_DIR) / f'{symbol}_H1.csv',
    ]
    for p in candidates:
        if not p.exists():
            continue
        df = pd.read_csv(str(p), index_col=0)
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_convert(None)
        df.index = idx
        df.index.name = 'datetime'
        df.columns = [c.lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        keep = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        df = df[keep].dropna(subset=['close']).sort_index().reset_index()
        log_print(f'[DEBUG] loaded {p.name}: {len(df)} bars')
        return df
    log_print(f'[WARN] CSV not found: {symbol}')
    return None


# ==========================================
# インジケーター
# ==========================================
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


def build_htf4h_signal(df_1h):
    """1h→4hリサンプル EMA20フィルター。+1=Buy許可/-1=Sell許可"""
    df = df_1h.set_index('datetime')
    df4 = df['close'].resample('4h').last().dropna().to_frame()
    df4['ema20'] = df4['close'].ewm(span=20, adjust=False).mean()
    df4['signal'] = np.where(df4['close'] > df4['ema20'], 1, -1)
    return df4['signal']


def build_htf_sigma(df_1h):
    """1h→4hリサンプル BBシグマポジション（レンジフィルター用）"""
    df = df_1h.set_index('datetime')
    df4 = df['close'].resample('4h').last().dropna().to_frame()
    ma  = df4['close'].rolling(20).mean()
    std = df4['close'].rolling(20).std()
    sig = (df4['close'] - ma) / std.replace(0, np.nan)
    return sig


# ==========================================
# シミュレーター
# ==========================================
def simulate(symbol, df_1h, filter_name, rsi_th, bb_width_th, sl_atr_mult, min_rr):
    """
    1hデータでBBエントリーシミュレーション。
    戻り値: {'pf', 'win_rate', 'trades', 'max_dd'} or None
    """
    spread = SPREAD.get(symbol, 0.0002)

    close   = df_1h['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi     = calc_rsi(close, RSI_PERIOD)
    atr     = calc_atr(df_1h, ATR_PERIOD)
    htf4h   = build_htf4h_signal(df_1h)
    htf_sig = build_htf_sigma(df_1h)

    close_arr = close.values
    n         = len(df_1h)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    cumul  = 0.0
    peak   = 0.0
    max_dd = 0.0
    last_bar = -COOLDOWN_BARS - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * sl_atr_mult
        tp = sl * min_rr
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_1h['datetime'].iloc[i]

        # 4h BBシグマ レンジフィルター
        htf_idx = htf_sig.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_sig.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE_SIG:
            continue

        # BBタッチ + ベースRSI
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        if c <= bb_l.iloc[i] and rsi_v < RSI_BUY_MAX:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > RSI_SELL_MIN:
            direction = 'sell'
        else:
            continue

        # HTF4h EMA20（全フィルターに共通）
        h4_idx = htf4h.index.searchsorted(dt, side='right') - 1
        if h4_idx < 0:
            continue
        h4_sig = htf4h.iloc[h4_idx]
        if direction == 'buy'  and h4_sig != 1:
            continue
        if direction == 'sell' and h4_sig != -1:
            continue

        # 追加フィルター
        if filter_name == 'htf4h_and_rsi':
            if direction == 'buy'  and rsi_v >= rsi_th:
                continue
            if direction == 'sell' and rsi_v <= (100 - rsi_th):
                continue

        elif filter_name == 'htf4h_and_volume':
            if 'volume' in df_1h.columns:
                vol_mean = df_1h['volume'].iloc[max(0, i - 20):i].mean()
                vol_curr = df_1h['volume'].iloc[i]
                if vol_mean > 0 and vol_curr <= vol_mean:
                    continue

        elif filter_name == 'htf4h_and_bb_width':
            width = float(bb_u.iloc[i]) - float(bb_l.iloc[i])
            if width < bb_width_th:
                continue

        # 決済シミュレーション（固定SL/TP）
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        hit      = None

        for j in range(i + 1, min(i + 200, n)):
            h = df_1h['high'].iloc[j]
            l = df_1h['low'].iloc[j]
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
            wins         += 1
            gross_profit += tp
            cumul        += tp
        elif hit == 'sl':
            losses     += 1
            gross_loss += sl
            cumul      -= sl
        else:
            continue

        if cumul > peak:
            peak = cumul
        dd = peak - cumul
        if dd > max_dd:
            max_dd = dd
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':   trades,
        'win_rate': round(wins / trades * 100, 1),
        'pf':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'max_dd':   round(max_dd, 6),
    }


# ==========================================
# グリッド組み合わせ生成
# ==========================================
def build_combinations():
    combos = []
    for filter_name in GRID['filter']:
        for sl_atr_mult in GRID['sl_atr_mult']:
            for min_rr in GRID['min_rr']:
                if filter_name in ('htf4h_only', 'htf4h_and_volume'):
                    combos.append({
                        'filter':      filter_name,
                        'rsi_th':      None,
                        'bb_width_th': None,
                        'sl_atr_mult': sl_atr_mult,
                        'min_rr':      min_rr,
                    })
                elif filter_name == 'htf4h_and_rsi':
                    for rsi_th in GRID['rsi_th']:
                        combos.append({
                            'filter':      filter_name,
                            'rsi_th':      rsi_th,
                            'bb_width_th': None,
                            'sl_atr_mult': sl_atr_mult,
                            'min_rr':      min_rr,
                        })
                elif filter_name == 'htf4h_and_bb_width':
                    for bb_width_th in GRID['bb_width_th']:
                        combos.append({
                            'filter':      filter_name,
                            'rsi_th':      None,
                            'bb_width_th': bb_width_th,
                            'sl_atr_mult': sl_atr_mult,
                            'min_rr':      min_rr,
                        })
    return combos


# ==========================================
# メイン
# ==========================================
def main():
    log_print('=== EURUSD/GBPUSD エントリー条件強化 グリッドサーチBT ===')
    combos = build_combinations()
    log_print(f'[DEBUG] {len(combos)} combos x {len(TARGET_PAIRS)} pairs = {len(combos)*len(TARGET_PAIRS)} runs')

    rows = []
    for symbol in TARGET_PAIRS:
        df_1h = load_1h(symbol)
        if df_1h is None:
            log_print(f'[ERROR] {symbol}: データなし → スキップ')
            continue

        log_print(f'\n--- {symbol} ({len(df_1h)} bars) ---')
        for combo in combos:
            res = simulate(
                symbol, df_1h,
                filter_name  = combo['filter'],
                rsi_th       = combo['rsi_th']      if combo['rsi_th']      is not None else 40,
                bb_width_th  = combo['bb_width_th'] if combo['bb_width_th'] is not None else 0.0,
                sl_atr_mult  = combo['sl_atr_mult'],
                min_rr       = combo['min_rr'],
            )
            if res is None:
                continue
            row = {
                'symbol':      symbol,
                'filter':      combo['filter'],
                'rsi_th':      combo['rsi_th'],
                'bb_width_th': combo['bb_width_th'],
                'sl_atr_mult': combo['sl_atr_mult'],
                'min_rr':      combo['min_rr'],
                **res,
            }
            rows.append(row)
            log_print(
                f'[DEBUG] {symbol} {combo["filter"]:22} '
                f'rsi_th={str(combo["rsi_th"]):4} bw={str(combo["bb_width_th"]):7} '
                f'sl={combo["sl_atr_mult"]:.1f} rr={combo["min_rr"]:.1f} '
                f'-> PF={res["pf"]:5.3f} WR={res["win_rate"]:4.1f}% N={res["trades"]}'
            )

    if not rows:
        log_print('[ERROR] 結果なし')
        return

    df_out = pd.DataFrame(rows)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding='utf-8')
    log_print(f'\n出力: {OUTPUT_CSV} ({len(rows)}件)')

    # PF上位5（N>=30足切り）
    valid = df_out[df_out['trades'] >= MIN_N]
    if valid.empty:
        log_print(f'[WARN] N>={MIN_N}の結果なし → N足切りなしで上位5表示')
        top5 = df_out.nlargest(5, 'pf')
    else:
        top5 = valid.nlargest(5, 'pf')

    print('\n=== PF上位5 (N>=' + str(MIN_N) + ') ===')
    print(f'{"pair":>7} {"filter":>22} {"rsi_th":>6} {"bw_th":>7} {"sl":>4} {"rr":>4} | {"PF":>6} {"WR":>6} {"N":>5} {"MaxDD":>10}')
    print('-' * 90)
    for _, r in top5.iterrows():
        print(
            f'{r["symbol"]:>7} {r["filter"]:>22} '
            f'{str(r["rsi_th"]):>6} {str(r["bb_width_th"]):>7} '
            f'{r["sl_atr_mult"]:>4.1f} {r["min_rr"]:>4.1f} | '
            f'{r["pf"]:>6.3f} {r["win_rate"]:>5.1f}% {int(r["trades"]):>5} {r["max_dd"]:>10.6f}'
        )


if __name__ == '__main__':
    main()
