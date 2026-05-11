"""
make_4h_from_1h.py - Resample 1h OHLCV CSV to 4h for SMA squeeze strategy.
Pairs: USDJPY, GBPJPY, EURUSD, GBPUSD, EURJPY
Output: data/{pair}_4h.csv
"""

import os
import pandas as pd

DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
PAIRS    = ['USDJPY', 'GBPJPY', 'EURUSD', 'GBPUSD', 'EURJPY']


def load_1h(pair):
    candidates = [
        os.path.join(DATA_DIR, f'{pair}_1h.csv'),
        os.path.join(DATA_DIR, f'{pair.lower()}_1h.csv'),
        os.path.join(DATA_DIR, f'{pair}_H1.csv'),
        os.path.join(DATA_DIR, f'{pair}_1H.csv'),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        # normalize columns
        df.columns = [c.lower().strip() for c in df.columns]
        # set datetime as index (column or positional)
        if 'datetime' in df.columns:
            df = df.set_index('datetime')
        else:
            df = df.set_index(df.columns[0])
        df.index = pd.to_datetime(df.index)
        try:
            df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass
        # deduplicate columns before filtering
        df = df.loc[:, ~df.columns.duplicated()]
        keep = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        if 'close' not in keep:
            print(f'[WARN] no close column in {path}  found: {list(df.columns)}')
            continue
        df = df[keep].dropna(subset=['close'])
        return df.sort_index()
    return None


def resample_4h(df_1h):
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in df_1h.columns:
        agg['volume'] = 'sum'
    return df_1h.resample('4h').agg(agg).dropna(subset=['close'])


def main():
    for pair in PAIRS:
        df_1h = load_1h(pair)
        if df_1h is None:
            print(f'[WARN] 1h CSV not found: {pair}  (skipped)')
            continue
        df_4h    = resample_4h(df_1h)
        out_path = os.path.join(DATA_DIR, f'{pair}_4h.csv')
        df_4h.to_csv(out_path)
        print(f'{pair}: {len(df_4h)} rows  '
              f'{df_4h.index[0].date()} - {df_4h.index[-1].date()}  -> {out_path}')


if __name__ == '__main__':
    main()
