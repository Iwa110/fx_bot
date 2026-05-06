"""
tod_backtest.py - TOD戦略バックテスト & グリッドサーチ
================================================================
データ:
  yfinanceで1h足をダウンロード（最大730日）。
  ローカルに {symbol}_1h.csv がある場合はそちらを優先。

分割:
  先頭500日(12000本)を訓練期間（時間帯統計計算）
  残りをテスト期間（シグナルシミュレーション）

グリッドサーチ:
  entry_sigma / tp_atr_mult / sl_atr_mult
  選択基準: PF最大（最低トレード数 MIN_TRADES 以上）

出力: optimizer/tod_bt_result.json
"""
import os
import json
import itertools
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

DATA_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tod_bt_result.json')

PAIRS = ['EURUSD', 'GBPUSD']
PAIR_PIP = {'EURUSD': 0.0001, 'GBPUSD': 0.0001}

TRAIN_BARS    = 2000       # 訓練期間バー数（~4ヶ月分の市場時間）
ATR_PERIOD    = 14
MAX_HOLD_BARS = 24
MIN_SAMPLES   = 4          # 時間帯ごとの最低サンプル数
MIN_TRADES    = 20         # 選択基準に必要な最低トレード数

GRID = {
    'entry_sigma': [1.0, 1.5, 2.0, 2.5, 3.0],
    'tp_atr_mult': [0.5, 1.0, 1.5, 2.0],
    'sl_atr_mult': [0.5, 1.0, 1.5, 2.0],
}


# ══════════════════════════════════════════
# データ取得
# ══════════════════════════════════════════
def download_1h(symbol):
    """yfinanceで1h足データをダウンロード（最大730日）"""
    try:
        import yfinance as yf
    except ImportError:
        print('[ERROR] yfinanceが未インストール: pip install yfinance')
        return None

    ticker = symbol + '=X'
    end    = datetime.now()
    start  = end - timedelta(days=729)
    print('  ダウンロード中: ' + ticker + ' 1h (' +
          str(start.date()) + ' ~ ' + str(end.date()) + ')')
    try:
        df = yf.download(
            ticker,
            start=start.strftime('%Y-%m-%d'),
            end=end.strftime('%Y-%m-%d'),
            interval='1h',
            progress=False,
        )
        if df is None or len(df) == 0:
            print('  [WARN] データ取得0件: ' + ticker)
            return None
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[['Open', 'High', 'Low', 'Close']].dropna()
        df.columns = ['open', 'high', 'low', 'close']
        print('  取得完了: ' + str(len(df)) + '本')
        return df
    except Exception as e:
        print('  [ERROR] ダウンロード失敗 ' + ticker + ': ' + str(e))
        return None


def load_1h_csv(symbol):
    """ローカル1hCSVを読み込む（VPS上のデータ優先）"""
    path = os.path.join(DATA_DIR, symbol + '_1h.csv')
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    required = ['open', 'high', 'low', 'close']
    if not all(c in df.columns for c in required):
        return None
    return df[required]


def load_data(symbol):
    """ローカルCSVがあればそれを使い、なければyfinanceでダウンロード"""
    df = load_1h_csv(symbol)
    if df is not None:
        print('  ローカルCSV読み込み: ' + symbol + ' (' + str(len(df)) + '本)')
        return df
    return download_1h(symbol)


# ══════════════════════════════════════════
# ATR計算（EMA）
# ══════════════════════════════════════════
def calc_atr(df, period=14):
    """ATR(EMA)を計算してカラム追加したDataFrameを返す"""
    highs  = df['high'].values
    lows   = df['low'].values
    closes = df['close'].values

    trs = [float(highs[0] - lows[0])]
    for i in range(1, len(df)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1]),
        )
        trs.append(float(tr))

    k   = 2.0 / (period + 1)
    atr = [trs[0]]
    for tr in trs[1:]:
        atr.append(tr * k + atr[-1] * (1 - k))

    df = df.copy()
    df['atr'] = atr
    return df


# ══════════════════════════════════════════
# 時間帯統計
# ══════════════════════════════════════════
def build_stats(df):
    """
    訓練データから時間帯別統計を計算。
    close-to-closeリターン / 平日のみ / JST時間帯。
    Fix2と同一の計算方式。
    """
    df = df.copy()
    df['ret']      = df['close'].pct_change()
    df['hour_jst'] = (df.index.hour + 9) % 24
    df['weekday']  = df.index.dayofweek   # 0=月 4=金

    wd    = df[df['weekday'] <= 4].dropna(subset=['ret'])
    stats = {}
    for h, grp in wd.groupby('hour_jst'):
        if len(grp) < MIN_SAMPLES:
            continue
        stats[int(h)] = {
            'mean': float(grp['ret'].mean()),
            'std':  float(grp['ret'].std()),
            'n':    int(len(grp)),
        }
    return stats


# ══════════════════════════════════════════
# 市場クローズ判定
# ══════════════════════════════════════════
def is_market_closed(dt):
    """Fix3と同一のロジック（UTC基準）"""
    wd = dt.weekday()  # 0=月 4=金
    h  = dt.hour
    if wd == 4 and h >= 22:
        return True   # 金曜夜
    if wd == 5 or wd == 6:
        return True   # 土日
    if wd == 0 and h < 6:
        return True   # 月曜早朝
    return False


# ══════════════════════════════════════════
# シミュレーション
# ══════════════════════════════════════════
def simulate(df, stats, entry_sigma, tp_mult, sl_mult, pip):
    """
    テスト期間でシグナルシミュレーション。
    close-to-closeリターンでz-score計算（Fix2に準拠）。
    max_pos=1相当（exit_barまで次のエントリーをスキップ）。
    """
    closes = df['close'].values
    highs  = df['high'].values
    lows   = df['low'].values
    atrs   = df['atr'].values
    idx    = df.index

    trades   = []
    exit_bar = -1   # この行インデックスまでトレード中

    for i in range(3, len(df)):
        if is_market_closed(idx[i]):
            continue
        if i <= exit_bar:
            continue

        hour_jst = (idx[i].hour + 9) % 24
        stat = stats.get(hour_jst)
        if stat is None or stat['std'] == 0:
            continue

        # Fix2準拠: bars[-3]→bars[-2] のclose-to-closeリターン
        # （バックテストでは i-1 が bars[-2]、i-2 が bars[-3] 相当）
        prev_c0 = closes[i - 2]   # bars[-2] close
        prev_c1 = closes[i - 3]   # bars[-3] close
        if prev_c1 == 0:
            continue
        ret = (prev_c0 - prev_c1) / prev_c1

        z = (ret - stat['mean']) / stat['std']
        if abs(z) <= entry_sigma:
            continue

        direction = 'buy' if z < 0 else 'sell'
        entry = closes[i - 1]   # 直前バー終値でエントリー（= バーi開値相当）
        atr   = atrs[i - 1]

        if direction == 'buy':
            tp = entry + atr * tp_mult
            sl = entry - atr * sl_mult
        else:
            tp = entry - atr * tp_mult
            sl = entry + atr * sl_mult

        result_pips = None
        for j in range(i, min(i + MAX_HOLD_BARS, len(df))):
            hi = highs[j]
            lo = lows[j]

            if direction == 'buy':
                sl_hit = lo <= sl
                tp_hit = hi >= tp
            else:
                sl_hit = hi >= sl
                tp_hit = lo <= tp

            if sl_hit:
                result_pips = (sl - entry) / pip if direction == 'buy' else (entry - sl) / pip
                exit_bar = j
                break
            if tp_hit:
                result_pips = (tp - entry) / pip if direction == 'buy' else (entry - tp) / pip
                exit_bar = j
                break

        if result_pips is None:
            j = min(i + MAX_HOLD_BARS - 1, len(df) - 1)
            cp = closes[j]
            result_pips = (cp - entry) / pip if direction == 'buy' else (entry - cp) / pip
            exit_bar = j

        trades.append(result_pips)

    if not trades:
        return None

    wins       = [t for t in trades if t > 0]
    losses     = [t for t in trades if t <= 0]
    gross_win  = sum(wins)   if wins   else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    pf       = gross_win / gross_loss if gross_loss > 0 else float('inf')
    win_rate = len(wins) / len(trades)

    return {
        'pf':       round(pf, 3),
        'win_rate': round(win_rate, 3),
        'n':        len(trades),
        'avg_pips': round(sum(trades) / len(trades), 1),
    }


# ══════════════════════════════════════════
# ペア別最適化
# ══════════════════════════════════════════
def run_pair(symbol):
    print('\n===== ' + symbol + ' =====')
    df = load_data(symbol)
    if df is None or len(df) < TRAIN_BARS + MAX_HOLD_BARS + 50:
        print('  [ERROR] データ不足 (' +
              str(len(df) if df is not None else 0) + '本)')
        return None

    df = calc_atr(df, ATR_PERIOD)

    train = df.iloc[:TRAIN_BARS]
    test  = df.iloc[TRAIN_BARS:]
    print('  訓練: ' + str(train.index[0].date()) +
          ' ~ ' + str(train.index[-1].date()) +
          ' (' + str(len(train)) + '本)')
    print('  テスト: ' + str(test.index[0].date()) +
          ' ~ ' + str(test.index[-1].date()) +
          ' (' + str(len(test)) + '本)')

    stats = build_stats(train)
    print('  時間帯統計: ' + str(len(stats)) + '時間帯')
    if len(stats) < 12:
        print('  [WARN] 時間帯数不足 → スキップ')
        return None

    pip    = PAIR_PIP[symbol]
    combos = list(itertools.product(
        GRID['entry_sigma'],
        GRID['tp_atr_mult'],
        GRID['sl_atr_mult'],
    ))
    print('  グリッドサーチ: ' + str(len(combos)) + '組合せ')

    best_pf     = -1.0
    best_params = None

    for entry_sigma, tp_mult, sl_mult in combos:
        result = simulate(test, stats, entry_sigma, tp_mult, sl_mult, pip)
        if result is None or result['n'] < MIN_TRADES:
            continue
        if result['pf'] > best_pf:
            best_pf     = result['pf']
            best_params = {
                'entry_sigma': entry_sigma,
                'tp_atr_mult': tp_mult,
                'sl_atr_mult': sl_mult,
                'pf':          result['pf'],
                'win_rate':    result['win_rate'],
                'n':           result['n'],
                'avg_pips':    result['avg_pips'],
            }

    if best_params is None:
        print('  [WARN] 有効な組合せなし（n>=' + str(MIN_TRADES) + ' を満たすものがない）')
        return None

    print('  最適: sigma=' + str(best_params['entry_sigma']) +
          ' TP*' + str(best_params['tp_atr_mult']) +
          ' SL*' + str(best_params['sl_atr_mult']) +
          ' PF=' + str(best_params['pf']) +
          ' WR=' + str(round(best_params['win_rate'] * 100, 1)) + '%' +
          ' n=' + str(best_params['n']) +
          ' avg=' + str(best_params['avg_pips']) + 'pips')
    return best_params


# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    print('TOD バックテスト開始: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    results = {}
    for symbol in PAIRS:
        params = run_pair(symbol)
        if params:
            results[symbol] = params

    if not results:
        print('\n[ERROR] 全ペアで最適化失敗')
        return

    with open(RESULT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print('\n結果保存: ' + RESULT_PATH)

    print('\n===== 最終結果サマリー =====')
    for symbol, p in results.items():
        print(symbol + ': sigma=' + str(p['entry_sigma']) +
              ' TP*' + str(p['tp_atr_mult']) +
              ' SL*' + str(p['sl_atr_mult']) +
              ' PF=' + str(p['pf']) +
              ' WR=' + str(round(p['win_rate'] * 100, 1)) + '%' +
              ' n=' + str(p['n']))


if __name__ == '__main__':
    main()
