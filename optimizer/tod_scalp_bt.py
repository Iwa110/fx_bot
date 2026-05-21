"""
tod_scalp_bt.py - TOD（Time-of-Day）スキャルピング戦略バックテスト
Magic: 20250003（参考のみ・BTでは不使用）

データ戦略:
  TOD統計: 1h足CSV（最大730日分）← slot=(weekday, hour) × stat_window_days
  BT本体:  5m足CSV（直近60日）   ← SL/TP/ATR精度を保つ

WFO構成:
  pivot = 5m足データの先頭60%終端日時
  IS    = 5m[0 : pivot]  → IS評価
  OOS   = 5m[pivot : end] → OOS評価（メインメトリクス）
  統計  = 1h[pivot - stat_window_days : pivot]  ← frozen stats (ルックアヘッドなし)

TOD統計 (1h足):
  slot  = (weekday, hour)
  ret_i = (close[i-1] - close[i-2]) / close[i-2]  (1バー遅延リターン)
  min 50サンプル以上のスロットのみシグナル有効
  t = mean * sqrt(n) / std  ← t統計量（z-scoreより統計的に適切）
  t >= +entry_sigma → BUY
  t <= -entry_sigma → SELL

BT (5m足):
  ATR: M5 EWM(span=14)
  SL  = ATR × sl_mult  (エントリー時固定)
  TP  = ATR × tp_mult  (エントリー時固定)
  時間切れ: max_hold_min 経過で現在値クローズ
  COOLDOWN: 15分/ペア（エントリー時点から）
  市場クローズ除外: 金曜22:00UTC以降・土日・月曜6:00UTC以前
  スキップ時間帯(UTC): [0, 1, 2]
"""

import os
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
OUTPUT_FILE = str(Path(__file__).parent / 'tod_scalp_bt_result.csv')

# ===== ペア設定 =====
PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY']

SPREAD_PIPS = {
    'EURUSD': 1.5,
    'GBPUSD': 2.0,
    'USDJPY': 1.5,
}

PIP_UNIT = {
    'EURUSD': 0.0001,
    'GBPUSD': 0.0001,
    'USDJPY': 0.01,
}

ATR_MIN = {
    'EURUSD': 0.00002,
    'GBPUSD': 0.00002,
    'USDJPY': 0.005,
}

LOT          = 0.01     # 固定ロット（コスト比較目的）
COOLDOWN_MIN = 15       # エントリー後クールダウン（分）
SKIP_HOURS   = {0, 1, 2}  # スキップ時間帯 UTC
WFO_IS_RATIO = 0.6      # IS比率（5m足ベース）
MIN_SAMPLES  = 50       # スロット最小サンプル数
ATR_EWM_SPAN = 14

# ===== グリッドサーチ =====
GRID = {
    'entry_sigma':      [1.5, 2.0, 2.5],
    'tp_mult':          [0.8, 1.0, 1.2],
    'sl_mult':          [1.0, 1.5, 2.0],
    'max_hold_min':     [15, 30, 60],
    'stat_window_days': [180, 365, 730],   # 1hデータに合わせた値
}

# ===== データ読み込み =====
def load_csv(symbol, tf='5m'):
    """
    tf: '5m' or '1h'
    戻り値: pd.DataFrame (columns: datetime, open, high, low, close, ...)
    """
    if tf == '5m':
        candidates = [
            os.path.join(DATA_DIR, f'{symbol}_M5.csv'),
            os.path.join(DATA_DIR, f'{symbol}_5m.csv'),
            os.path.join(DATA_DIR, f'{symbol.lower()}_5m.csv'),
        ]
    else:
        candidates = [
            os.path.join(DATA_DIR, f'{symbol}_1h.csv'),
            os.path.join(DATA_DIR, f'{symbol}_H1.csv'),
            os.path.join(DATA_DIR, f'{symbol.lower()}_1h.csv'),
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

# ===== 市場クローズ判定 =====
def is_market_closed(dt):
    """金曜22:00UTC以降・土日・月曜6:00UTC以前はクローズ"""
    wd = dt.weekday()   # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    if wd == 5 or wd == 6:          # 土日
        return True
    if wd == 4 and dt.hour >= 22:   # 金曜22:00以降
        return True
    if wd == 0 and dt.hour < 6:     # 月曜6:00以前
        return True
    return False

# ===== ATR（EWM span=14） =====
def calc_atr_ewm(df, span=ATR_EWM_SPAN):
    high  = df['high']
    low   = df['low']
    close = df['close']
    hl    = high - low
    hc    = (high - close.shift()).abs()
    lc    = (low  - close.shift()).abs()
    tr    = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()

# ===== TOD統計計算（1h足） =====
def compute_tod_stats(df_1h):
    """
    1h足データからスロット別の平均リターン・標準偏差を計算。
    slot  = (weekday, hour)
    ret_i = (close[i-1] - close[i-2]) / close[i-2]  (1バー遅延リターン)
    min MIN_SAMPLES サンプル以上のスロットのみ登録。
    戻り値: {(weekday, hour): (mean, std, n)}
    """
    close = df_1h['close'].values
    dts   = df_1h['datetime']
    n     = len(df_1h)

    slot_rets = {}

    for i in range(2, n):
        c_m2 = close[i - 2]
        c_m1 = close[i - 1]
        if c_m2 == 0 or np.isnan(c_m2) or np.isnan(c_m1):
            continue
        ret  = (c_m1 - c_m2) / c_m2
        dt   = dts.iloc[i]
        slot = (dt.weekday(), dt.hour)
        if slot not in slot_rets:
            slot_rets[slot] = []
        slot_rets[slot].append(ret)

    tod_stats = {}
    for slot, rets in slot_rets.items():
        if len(rets) < MIN_SAMPLES:
            continue
        arr  = np.array(rets)
        mean = arr.mean()
        std  = arr.std()
        if std == 0.0:
            continue
        tod_stats[slot] = (mean, std, len(rets))

    return tod_stats

# ===== シミュレーション（5m足） =====
def simulate(df_5m, tod_stats, entry_sigma, tp_mult, sl_mult,
             max_hold_min, spread, atr_min):
    """
    5m足BT。スロット参照は (weekday, hour) のみ（分は使わない）。
    戻り値: list of {'pnl', 'hold_min', 'hit'}
    """
    n             = len(df_5m)
    atr           = calc_atr_ewm(df_5m)
    close         = df_5m['close'].values
    high          = df_5m['high'].values
    low           = df_5m['low'].values
    dts           = df_5m['datetime']
    max_hold_bars = max(1, max_hold_min // 5)

    trades       = []
    cooldown_end = None  # pd.Timestamp

    for i in range(2, n):
        dt = dts.iloc[i]

        # スキップ時間帯
        if dt.hour in SKIP_HOURS:
            continue

        # 市場クローズ
        if is_market_closed(dt):
            continue

        # クールダウン（エントリーから15分）
        if cooldown_end is not None and dt < cooldown_end:
            continue

        # ATR
        atr_v = atr.iloc[i]
        if np.isnan(atr_v) or atr_v < atr_min:
            continue

        # TODスロット（1h統計と同じ粒度: weekday × hour）
        slot = (dt.weekday(), dt.hour)
        if slot not in tod_stats:
            continue

        mean_r, std_r, n_r = tod_stats[slot]
        # t統計量: t = mean * sqrt(n) / std（z-scoreより統計的に適切）
        t = mean_r * np.sqrt(n_r) / std_r

        if t >= entry_sigma:
            direction = 'buy'
        elif t <= -entry_sigma:
            direction = 'sell'
        else:
            continue

        c = close[i]
        if np.isnan(c):
            continue

        sl_dist = atr_v * sl_mult
        tp_dist = atr_v * tp_mult

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl_price = entry - sl_dist if direction == 'buy' else entry + sl_dist

        # クールダウン設定（エントリー時点から）
        cooldown_end = dt + pd.Timedelta(minutes=COOLDOWN_MIN)

        # 決済ループ
        hit        = 'timeout'
        exit_bar   = min(i + max_hold_bars, n - 1)
        exit_price = close[exit_bar]

        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            h  = high[j]
            lo = low[j]
            if direction == 'buy':
                if lo <= sl_price:
                    hit = 'sl'; exit_price = sl_price; exit_bar = j; break
                if h >= tp_price:
                    hit = 'tp'; exit_price = tp_price; exit_bar = j; break
            else:
                if h >= sl_price:
                    hit = 'sl'; exit_price = sl_price; exit_bar = j; break
                if lo <= tp_price:
                    hit = 'tp'; exit_price = tp_price; exit_bar = j; break

        if np.isnan(exit_price):
            continue

        pnl      = (exit_price - entry) if direction == 'buy' else (entry - exit_price)
        hold_min = (exit_bar - i) * 5

        trades.append({'pnl': pnl, 'hold_min': hold_min, 'hit': hit})

    return trades

# ===== メトリクス計算 =====
def calc_metrics(trades):
    if not trades:
        return {'n': 0, 'pf': 0.0, 'wr': 0.0, 'avg_hold_min': 0.0}
    gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss   = sum(abs(t['pnl']) for t in trades if t['pnl'] <= 0)
    wins         = sum(1 for t in trades if t['pnl'] > 0)
    n            = len(trades)
    avg_hold     = float(np.mean([t['hold_min'] for t in trades]))
    pf = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    wr = wins / n * 100
    return {
        'n':            n,
        'pf':           round(pf, 3),
        'wr':           round(wr, 1),
        'avg_hold_min': round(avg_hold, 1),
    }

# ===== メイン =====
def main():
    print('=== TOD Scalp BT ===')
    print(f'DATA_DIR : {DATA_DIR}')
    print(f'OUTPUT   : {OUTPUT_FILE}')
    print(f'TOD統計  : 1h足 / slot=(weekday, hour) / min_samples={MIN_SAMPLES} / signal=t-stat')
    print(f'BT本体   : 5m足 / spread込み / ATR-EWM({ATR_EWM_SPAN})')

    param_keys = list(GRID.keys())
    param_vals = list(GRID.values())
    all_params = list(itertools.product(*param_vals))
    total_runs = len(all_params) * len(PAIRS)
    print(f'\nグリッド: {len(all_params)} combinations × {len(PAIRS)} pairs = {total_runs} runs')
    print(f'  entry_sigma={GRID["entry_sigma"]}')
    print(f'  tp_mult={GRID["tp_mult"]}  sl_mult={GRID["sl_mult"]}')
    print(f'  max_hold_min={GRID["max_hold_min"]}')
    print(f'  stat_window_days={GRID["stat_window_days"]}  (1hデータ対応)')

    all_rows = []

    for pair in PAIRS:
        print(f'\n{"="*60}')
        print(f'  {pair}')
        print(f'{"="*60}')

        df_5m = load_csv(pair, '5m')
        df_1h = load_csv(pair, '1h')
        if df_5m is None:
            print(f'  [SKIP] 5mデータなし')
            continue
        if df_1h is None:
            print(f'  [SKIP] 1hデータなし')
            continue

        spread  = SPREAD_PIPS[pair] * PIP_UNIT[pair]
        atr_min = ATR_MIN[pair]

        # ===== WFO pivot（5m足ベース）=====
        n_5m_total = len(df_5m)
        n_5m_is    = int(n_5m_total * WFO_IS_RATIO)
        df_5m_is   = df_5m.iloc[:n_5m_is].reset_index(drop=True)
        df_5m_oos  = df_5m.iloc[n_5m_is:].reset_index(drop=True)
        pivot_dt   = df_5m_is['datetime'].iloc[-1]  # IS終端 = 統計期間上限

        print(f'  5m総行数 : {n_5m_total}')
        print(f'  pivot    : {pivot_dt}')
        print(f'  5m IS    : {df_5m_is["datetime"].iloc[0]}  〜  {pivot_dt}  ({n_5m_is} rows)')
        print(f'  5m OOS   : {df_5m_oos["datetime"].iloc[0]}  〜  {df_5m_oos["datetime"].iloc[-1]}  ({len(df_5m_oos)} rows)')
        print(f'  1h range : {df_1h["datetime"].iloc[0]}  〜  {df_1h["datetime"].iloc[-1]}  ({len(df_1h)} rows)')

        # ===== stat_window_days 別に TOD統計を事前計算 =====
        # 統計期間 = 1hデータのうち [pivot - swd, pivot] のみ使用（ルックアヘッドなし）
        stat_cache = {}
        for swd in GRID['stat_window_days']:
            cutoff    = pivot_dt - pd.Timedelta(days=swd)
            df_1h_stat = df_1h[(df_1h['datetime'] >= cutoff) &
                                (df_1h['datetime'] <= pivot_dt)]
            n_stat = len(df_1h_stat)
            span_days = (df_1h_stat['datetime'].iloc[-1] - df_1h_stat['datetime'].iloc[0]).days \
                        if n_stat > 1 else 0

            if n_stat < 120:   # 1h足で120行 ≈ 5日分を最低限とする
                print(f'  [WARN] stat_window_days={swd}: 1hデータ不足 ({n_stat} rows) → スキップ')
                stat_cache[swd] = {}
                continue

            tod = compute_tod_stats(df_1h_stat)
            stat_cache[swd] = tod

            n_valid = len(tod)
            max_slots = 5 * 24  # weekday×hour の最大120スロット
            print(f'  stat_window_days={swd}: {n_stat} rows ({span_days}日) '
                  f'→ {n_valid}/{max_slots} valid slots '
                  f'(avg {n_stat // max(n_valid,1):.0f} samples/slot)')

        # ===== グリッドサーチ =====
        if TQDM_AVAILABLE:
            pbar = tqdm(all_params, desc=f'{pair}', leave=True)
        else:
            pbar = all_params

        for params in pbar:
            p = dict(zip(param_keys, params))
            tod_stats = stat_cache[p['stat_window_days']]

            if not tod_stats:
                continue

            trades_is  = simulate(df_5m_is,  tod_stats,
                                   p['entry_sigma'], p['tp_mult'], p['sl_mult'],
                                   p['max_hold_min'], spread, atr_min)
            trades_oos = simulate(df_5m_oos, tod_stats,
                                   p['entry_sigma'], p['tp_mult'], p['sl_mult'],
                                   p['max_hold_min'], spread, atr_min)

            m_is  = calc_metrics(trades_is)
            m_oos = calc_metrics(trades_oos)

            all_rows.append({
                'pair':             pair,
                'entry_sigma':      p['entry_sigma'],
                'tp_mult':          p['tp_mult'],
                'sl_mult':          p['sl_mult'],
                'max_hold_min':     p['max_hold_min'],
                'stat_window_days': p['stat_window_days'],
                'n_is':             m_is['n'],
                'pf_is':            m_is['pf'],
                'wr_is':            m_is['wr'],
                'n_oos':            m_oos['n'],
                'pf_oos':           m_oos['pf'],
                'wr_oos':           m_oos['wr'],
                'avg_hold_min':     m_oos['avg_hold_min'],
            })

    if not all_rows:
        print('\n[ERROR] 結果なし。データを確認してください。')
        return

    df_out = pd.DataFrame(all_rows)
    df_out.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
    print(f'\n出力完了: {OUTPUT_FILE}  ({len(all_rows)} rows)')

    # ===== OOS PF 上位10 ペア別 =====
    print('\n' + '='*60)
    print('  OOS PF 上位10（ペア別・n_oos>=10）')
    print('='*60)
    for pair in PAIRS:
        sub   = df_out[df_out['pair'] == pair]
        valid = sub[sub['n_oos'] >= 10].nlargest(10, 'pf_oos')
        if valid.empty:
            print(f'\n{pair}: n_oos>=10 の結果なし')
            continue
        print(f'\n{pair}:')
        print(f'  {"sigma":>5} | {"tp":>4} | {"sl":>4} | {"hold":>4} | {"swd":>5} | '
              f'{"n_is":>5} | {"pf_is":>6} | {"n_oos":>5} | {"pf_oos":>6} | '
              f'{"wr_oos":>6} | {"avg_hold":>8}')
        print(f'  {"-"*80}')
        for _, r in valid.iterrows():
            print(f'  {r["entry_sigma"]:>5.1f} | '
                  f'{r["tp_mult"]:>4.1f} | '
                  f'{r["sl_mult"]:>4.1f} | '
                  f'{int(r["max_hold_min"]):>4d} | '
                  f'{int(r["stat_window_days"]):>5d} | '
                  f'{int(r["n_is"]):>5d} | '
                  f'{r["pf_is"]:>6.3f} | '
                  f'{int(r["n_oos"]):>5d} | '
                  f'{r["pf_oos"]:>6.3f} | '
                  f'{r["wr_oos"]:>5.1f}% | '
                  f'{r["avg_hold_min"]:>7.1f}m')


if __name__ == '__main__':
    main()
