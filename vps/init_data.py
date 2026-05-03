"""
init_data.py - FX価格データ初回一括取得
1時間足: 2年分 / 5分足: 60日分
実行: python init_data.py
"""

import os
import sys
import time
import yfinance as yf
import pandas as pd
from datetime import datetime

# ── 設定 ────────────────────────────────────────────
PAIRS = [
    # 現行戦略（7ペア）
    'USDCAD', 'GBPJPY', 'EURJPY', 'USDJPY', 'AUDJPY', 'EURUSD', 'GBPUSD',
    # 拡張用追加（7ペア）
    'AUDUSD', 'NZDUSD', 'USDCHF', 'NZDJPY', 'CHFJPY', 'EURGBP', 'AUDCAD',
]

DATA_DIR = r'C:\Users\Administrator\fx_bot\data'

TIMEFRAMES = {
    '1h': {'interval': '1h',  'period': '2y'},
    '5m': {'interval': '5m',  'period': '60d'},
}

COLUMNS = ['datetime', 'open', 'high', 'low', 'close', 'volume']

# yfinance のシンボル形式に変換（例: USDJPY -> USDJPY=X）
def to_yf_symbol(pair: str) -> str:
    return f'{pair}=X'

def fetch_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """yfinanceからOHLCVを取得してDataFrameで返す"""
    ticker = yf.Ticker(to_yf_symbol(symbol))
    df = ticker.history(
        period=period,
        interval=interval,
        auto_adjust=True,
    )

    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = df.reset_index()

    # カラム名を統一
    df.rename(columns={
        'Datetime': 'datetime',
        'Date':     'datetime',
        'Open':     'open',
        'High':     'high',
        'Low':      'low',
        'Close':    'close',
        'Volume':   'volume',
    }, inplace=True)

    # datetime をタイムゾーンなしのUTCに統一
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True).dt.tz_localize(None)

    df = df[COLUMNS].copy()
    df.sort_values('datetime', inplace=True)
    df.drop_duplicates(subset='datetime', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def save_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False, date_format='%Y-%m-%d %H:%M:%S')

def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    total = len(PAIRS) * len(TIMEFRAMES)
    done  = 0
    errors = []

    print(f'=== FX初回データ取得開始: {datetime.now():%Y-%m-%d %H:%M:%S} ===')
    print(f'対象ペア: {len(PAIRS)}  時間足: {len(TIMEFRAMES)}  合計: {total}リクエスト\n')

    for pair in PAIRS:
        for tf_name, tf_cfg in TIMEFRAMES.items():
            done += 1
            csv_path = os.path.join(DATA_DIR, f'{pair}_{tf_name}.csv')
            print(f'[{done:>2}/{total}] {pair} {tf_name} ...', end=' ', flush=True)

            try:
                df = fetch_ohlcv(pair, tf_cfg['interval'], tf_cfg['period'])

                if df.empty:
                    print('データなし（スキップ）')
                    errors.append(f'{pair}_{tf_name}: データ取得失敗')
                else:
                    save_csv(df, csv_path)
                    print(f'OK ({len(df)}行) -> {csv_path}')

            except Exception as e:
                print(f'ERROR: {e}')
                errors.append(f'{pair}_{tf_name}: {e}')

            # API負荷軽減
            time.sleep(0.5)

    print(f'\n=== 完了: {datetime.now():%Y-%m-%d %H:%M:%S} ===')
    if errors:
        print(f'\n失敗 ({len(errors)}件):')
        for e in errors:
            print(f'  - {e}')
        sys.exit(1)
    else:
        print('全ペア取得成功')

if __name__ == '__main__':
    main()