"""
update_data.py - FX価格データ差分更新
各CSVの最終行以降のデータのみ取得して追記
タスクスケジューラで毎日深夜0時に実行
"""

import os
import sys
import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

# ── 設定 ────────────────────────────────────────────
PAIRS = [
    'USDCAD', 'GBPJPY', 'EURJPY', 'USDJPY', 'AUDJPY', 'EURUSD', 'GBPUSD',
    'AUDUSD', 'NZDUSD', 'USDCHF', 'NZDJPY', 'CHFJPY', 'EURGBP', 'AUDCAD',
]

DATA_DIR  = r'C:\Users\Administrator\fx_bot\data'
LOG_FILE  = os.path.join(DATA_DIR, 'update_log.txt')

TIMEFRAMES = {
    '1h': {'interval': '1h',  'fetch_days': 7},   # 余裕を持って7日分取得
    '5m': {'interval': '5m',  'fetch_days': 5},   # 5日分取得（yfinance 5m上限60日）
}

COLUMNS = ['datetime', 'open', 'high', 'low', 'close', 'volume']

# ── ユーティリティ ────────────────────────────────────
def to_yf_symbol(pair: str) -> str:
    return f'{pair}=X'

def log(msg: str, fp=None) -> None:
    """コンソールとログファイルに同時出力"""
    ts = f'[{datetime.now():%Y-%m-%d %H:%M:%S}]'
    line = f'{ts} {msg}'
    print(line)
    if fp:
        fp.write(line + '\n')
        fp.flush()

def read_last_datetime(csv_path: str):
    """CSVの最終行のdatetimeを返す。ファイルがなければNone"""
    if not os.path.exists(csv_path):
        return None
    try:
        # 末尾だけ読む（大きいCSVでも高速）
        df_tail = pd.read_csv(csv_path, usecols=['datetime'], parse_dates=['datetime'])
        if df_tail.empty:
            return None
        return pd.to_datetime(df_tail['datetime'].iloc[-1])
    except Exception:
        return None

def fetch_since(symbol: str, interval: str, fetch_days: int) -> pd.DataFrame:
    """直近 fetch_days 日分を取得"""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=fetch_days)

    ticker = yf.Ticker(to_yf_symbol(symbol))
    df = ticker.history(
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        interval=interval,
        auto_adjust=True,
    )

    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = df.reset_index()
    df.rename(columns={
        'Datetime': 'datetime', 'Date': 'datetime',
        'Open': 'open', 'High': 'high',
        'Low': 'low',  'Close': 'close', 'Volume': 'volume',
    }, inplace=True)

    df['datetime'] = pd.to_datetime(df['datetime'], utc=True).dt.tz_localize(None)
    df = df[COLUMNS].copy()
    df.sort_values('datetime', inplace=True)
    df.drop_duplicates(subset='datetime', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def append_new_rows(csv_path: str, new_df: pd.DataFrame, last_dt, fp) -> int:
    """last_dt より新しい行だけ追記。追記件数を返す"""
    if last_dt is not None:
        new_df = new_df[new_df['datetime'] > last_dt]

    if new_df.empty:
        return 0

    # ヘッダーはファイルがない場合のみ付与
    header = not os.path.exists(csv_path)
    new_df.to_csv(
        csv_path, mode='a', index=False, header=header,
        date_format='%Y-%m-%d %H:%M:%S',
    )
    return len(new_df)

# ── メイン ────────────────────────────────────────────
def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    total_added = 0
    errors = []

    with open(LOG_FILE, 'a', encoding='utf-8') as fp:
        log('=' * 60, fp)
        log(f'UPDATE START  pairs={len(PAIRS)}', fp)
        log('=' * 60, fp)

        for pair in PAIRS:
            for tf_name, tf_cfg in TIMEFRAMES.items():
                csv_path = os.path.join(DATA_DIR, f'{pair}_{tf_name}.csv')
                label    = f'{pair}_{tf_name}'

                try:
                    last_dt = read_last_datetime(csv_path)
                    log(f'{label}: last={last_dt or "CSVなし"}', fp)

                    new_df = fetch_since(pair, tf_cfg['interval'], tf_cfg['fetch_days'])

                    if new_df.empty:
                        log(f'{label}: 取得データなし', fp)
                        continue

                    added = append_new_rows(csv_path, new_df, last_dt, fp)
                    total_added += added
                    log(f'{label}: +{added}行追記', fp)

                except Exception as e:
                    msg = f'{label}: ERROR {e}'
                    log(msg, fp)
                    errors.append(msg)

                time.sleep(0.5)

        log('-' * 60, fp)
        log(f'UPDATE END  total_added={total_added}  errors={len(errors)}', fp)
        if errors:
            for e in errors:
                log(f'  FAIL: {e}', fp)
        log('=' * 60, fp)

    if errors:
        sys.exit(1)

if __name__ == '__main__':
    main()
