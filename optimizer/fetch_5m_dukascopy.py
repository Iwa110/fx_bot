"""
fetch_5m_dukascopy.py - Dukascopy から長期5m足を取得し data/<pair>_5m.csv を生成。

背景:
    VPSのMT5 demo口座(Axiory-Demo/Exness-Trial)は深い5m履歴を配信せず copy_rates が
    0本(probe直近10本すら0)。update_data.py の yfinance も5mは~60日上限。よって
    session_fakeout等の5m依存BTを2年で検証するための代替データ源として Dukascopy
    (無料・口座不要・深い履歴)を採用。ローカル実行で完結(VPS作業不要)・再現可能。

出力形式:
    data/<pair>_5m.csv = 「datetime,open,high,low,close,volume」(index=False, UTC naive)。
    update_data.py の追記形式と一致(以後の日次追記/read_last_datetime 互換)、かつ
    BT側ローダ(index_col=0 + 小文字ohlc)とも互換。BID側OHLCを採用(コストはBT側でspread加味)。

実行 (専用venv):
    python3 -m venv .venv_dukas
    .venv_dukas/bin/pip install dukascopy-python pandas
    .venv_dukas/bin/python optimizer/fetch_5m_dukascopy.py --years 2
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import dukascopy_python as d
from dukascopy_python.instruments import (
    INSTRUMENT_FX_CROSSES_GBP_JPY,
    INSTRUMENT_FX_CROSSES_EUR_JPY,
    INSTRUMENT_FX_MAJORS_GBP_USD,
)

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
UTC = timezone.utc

PAIRS = {
    'GBPJPY': INSTRUMENT_FX_CROSSES_GBP_JPY,
    'EURJPY': INSTRUMENT_FX_CROSSES_EUR_JPY,
    'GBPUSD': INSTRUMENT_FX_MAJORS_GBP_USD,
}

CHUNK_DAYS = 55   # 55d*288 ≈ 15,840 本 < ライブラリ limit=30,000


def fetch_pair(instrument: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        try:
            df = d.fetch(instrument, d.INTERVAL_MIN_5, d.OFFER_SIDE_BID, cur, nxt)
        except Exception as e:
            print(f'    [warn] {cur.date()}~{nxt.date()} fetch失敗: {e}')
            df = None
        if df is not None and len(df):
            frames.append(df)
            print(f'    {cur.date()}~{nxt.date()}: {len(df)}本')
        cur = nxt
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep='first')].sort_index()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=float, default=2.0)
    ap.add_argument('--pairs', nargs='+', default=list(PAIRS.keys()))
    args = ap.parse_args()

    end = datetime.now(tz=UTC)
    start = end - timedelta(days=int(args.years * 365))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f'fetch_5m_dukascopy: pairs={args.pairs} {start.date()}~{end.date()}')

    for pair in args.pairs:
        inst = PAIRS.get(pair)
        if inst is None:
            print(f'[{pair}] 未対応instrument, skip'); continue
        print(f'\n[{pair}_5m] 取得中...')
        df = fetch_pair(inst, start, end)
        if df.empty:
            print(f'[{pair}_5m] 取得0本'); continue
        df = df.reset_index().rename(columns={'timestamp': 'datetime'})
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True).dt.tz_localize(None)
        df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']]
        out = DATA_DIR / f'{pair}_5m.csv'
        df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
        span = f"{df['datetime'].iloc[0].date()}~{df['datetime'].iloc[-1].date()}"
        print(f'[{pair}_5m] {len(df)}本 保存 ({span}) -> {out}')


if __name__ == '__main__':
    main()
