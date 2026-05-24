"""
fetch_news_data.py - 経済指標実データ取得 & news_events.csv 成形
=================================================================
データソース:
  - FRED (St. Louis Fed): US NFP / US CPI / Core CPI
    APIキー不要、直接CSV DL
    URL: https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES_ID
  - ONS (UK CPI y/y):
    https://www.ons.gov.uk/generator?format=csv&uri=.../timeseries/d7g7/mm23

Forecast近似:
  真の市場コンセンサス予想は有料データ(Bloomberg等)が必要。
  本スクリプトでは「前回値」を naive forecast として使用。
    surprise = actual - previous  (近似サプライズ)
  B条件のZ計算には使えるが、絶対値の大小よりも過去の標準偏差との相対値が重要なので
  実用上は問題ない。

出力: data/news_events.csv (既存ファイルを上書き)

Release date 推定ロジック:
  NFP:    翌月の第1金曜日、8:30 ET
  US CPI: 翌月の第2水曜日または第3火曜日を基準に推定 (BLS通常スケジュール)
          実際は毎年BLSが公表するが、ここでは第2~3週の水曜を近似
  UK CPI: 翌月の第3水曜日、7:00 GMT (=BST-1h, 夏時間考慮)

Usage:
  python fetch_news_data.py [--from 2022-01-01] [--to 2026-05-31]
"""

import argparse
import io
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

DATA_DIR = __import__('pathlib').Path(__file__).parent.parent / 'data'
OUT_CSV  = DATA_DIR.parent / 'data' / 'news_events.csv'
OUT_CSV  = DATA_DIR / 'news_events.csv'

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; fx-bot-research/1.0)'}

# ===== FRED series =====
FRED_BASE = 'https://fred.stlouisfed.org/graph/fredgraph.csv'

FRED_SERIES = {
    'PAYEMS':   'NFP total nonfarm (thousands)',
    'CPIAUCSL': 'US CPI All Items SA',
    'CPILFESL': 'US Core CPI (less food/energy) SA',
}

# ONS UK CPI y/y (timeseries code D7G7 = CPI 12-month rate)
ONS_URL = ('https://www.ons.gov.uk/generator?format=csv'
           '&uri=/economy/inflationandpriceindices/timeseries/d7g7/mm23')


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def fetch_csv(url: str, name: str) -> pd.DataFrame | None:
    """URLからCSVを取得してDataFrameを返す。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        print(f'  [{name}] {len(df)} 行取得')
        return df
    except Exception as e:
        print(f'  [{name}] 取得失敗: {e}')
        return None


def first_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """指定月の第1 weekday (0=月曜 .. 4=金曜) を返す。"""
    d = date(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta)


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """指定月の第n weekday (n=1が第1) を返す。"""
    first = first_weekday_of_month(year, month, weekday)
    return first + timedelta(weeks=n - 1)


def next_month(year: int, month: int) -> tuple[int, int]:
    """翌月の (year, month) を返す。"""
    if month == 12:
        return year + 1, 1
    return year, month + 1


# ---------------------------------------------------------------------------
# NFP 実績値取得 (FRED PAYEMS)
# ---------------------------------------------------------------------------

def fetch_nfp(start: str, end: str) -> pd.DataFrame:
    """
    PAYEMS (月次水準) から NFP 前月比変化 (千人) を計算し、
    リリース日時 (ET) を付けた DataFrame を返す。
    """
    print('\n[NFP] FRED PAYEMS 取得...')
    url = f'{FRED_BASE}?id=PAYEMS'
    raw = fetch_csv(url, 'PAYEMS')
    if raw is None:
        return pd.DataFrame()

    raw.columns = ['date', 'value']
    raw['date']  = pd.to_datetime(raw['date'])
    raw['value'] = pd.to_numeric(raw['value'], errors='coerce')
    raw = raw.dropna().sort_values('date').reset_index(drop=True)

    # 前月比変化 (千人)
    raw['actual_val']   = raw['value'].diff()
    raw['previous_val'] = raw['value'].diff().shift(1)
    raw = raw.dropna()

    # BT 期間フィルター
    raw = raw[(raw['date'] >= start) & (raw['date'] <= end)]

    rows = []
    for _, row in raw.iterrows():
        data_month = row['date'].year, row['date'].month
        rel_year, rel_month = next_month(*data_month)
        # 第1金曜日
        rel_date = first_weekday_of_month(rel_year, rel_month, 4)  # 4=金曜
        rows.append({
            'date':         rel_date.strftime('%Y-%m-%d'),
            'time':         '08:30',   # ET
            'currency':     'USD',
            'event':        'Non-Farm Employment Change',
            'actual':       f'{int(round(row["actual_val"]))}K',
            'forecast':     f'{int(round(row["previous_val"]))}K',  # naive: 前回値
            'previous':     f'{int(round(row["previous_val"]))}K',
            'actual_val':   row['actual_val'],
            'forecast_val': row['previous_val'],
            'previous_val': row['previous_val'],
            'source':       'FRED/PAYEMS',
        })

    df = pd.DataFrame(rows)
    print(f'  NFP: {len(df)} 件 ({df["date"].min()} ~ {df["date"].max()})')
    return df


# ---------------------------------------------------------------------------
# US CPI 取得 (FRED CPIAUCSL / CPILFESL)
# ---------------------------------------------------------------------------

def _estimate_cpi_release_date(year: int, month: int) -> date:
    """
    BLS の CPI リリースは翌月 10〜15 日頃の水曜または木曜。
    近似: 翌月第2水曜日 (BLS 実際スケジュールと ±1〜2 日の誤差あり)
    """
    rel_year, rel_month = next_month(year, month)
    # 第2水曜日
    return nth_weekday_of_month(rel_year, rel_month, 2, 2)  # 2=水曜, n=2


def fetch_us_cpi(start: str, end: str) -> pd.DataFrame:
    """US CPI m/m と Core CPI m/m を取得して返す。"""
    print('\n[US CPI] FRED CPIAUCSL / CPILFESL 取得...')
    rows = []

    for series_id, event_name in [
        ('CPIAUCSL', 'CPI m/m'),
        ('CPILFESL', 'Core CPI m/m'),
    ]:
        url  = f'{FRED_BASE}?id={series_id}'
        raw  = fetch_csv(url, series_id)
        if raw is None:
            continue
        raw.columns = ['date', 'value']
        raw['date']  = pd.to_datetime(raw['date'])
        raw['value'] = pd.to_numeric(raw['value'], errors='coerce')
        raw = raw.dropna().sort_values('date').reset_index(drop=True)

        # m/m % 変化
        raw['mom']      = raw['value'].pct_change() * 100
        raw['prev_mom'] = raw['mom'].shift(1)
        raw = raw.dropna()
        raw = raw[(raw['date'] >= start) & (raw['date'] <= end)]

        for _, row in raw.iterrows():
            rel_date = _estimate_cpi_release_date(row['date'].year, row['date'].month)
            rows.append({
                'date':         rel_date.strftime('%Y-%m-%d'),
                'time':         '08:30',
                'currency':     'USD',
                'event':        event_name,
                'actual':       f'{row["mom"]:.1f}%',
                'forecast':     f'{row["prev_mom"]:.1f}%',
                'previous':     f'{row["prev_mom"]:.1f}%',
                'actual_val':   round(row['mom'], 3),
                'forecast_val': round(row['prev_mom'], 3),
                'previous_val': round(row['prev_mom'], 3),
                'source':       f'FRED/{series_id}',
            })

        time.sleep(0.5)  # rate limit

    df = pd.DataFrame(rows)
    if len(df):
        print(f'  US CPI: {len(df)} 件 ({df["date"].min()} ~ {df["date"].max()})')
    return df


# ---------------------------------------------------------------------------
# UK CPI 取得 (ONS)
# ---------------------------------------------------------------------------

def _estimate_uk_cpi_release_date(year: int, month: int) -> date:
    """
    ONS は翌月第3水曜日 7:00 GMT 頃に CPI を発表。
    """
    rel_year, rel_month = next_month(year, month)
    return nth_weekday_of_month(rel_year, rel_month, 2, 3)  # 2=水曜, n=3


def fetch_uk_cpi(start: str, end: str) -> pd.DataFrame:
    """
    ONS から UK CPI y/y を取得。失敗時は空 DataFrame。
    """
    print('\n[UK CPI] ONS 取得...')
    rows = []

    # ONS は rate-limit が厳しいのでリトライあり
    for attempt in range(3):
        try:
            r = requests.get(ONS_URL, headers=HEADERS, timeout=20)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f'  Rate limited. {wait}秒後リトライ...')
                time.sleep(wait)
                continue
            r.raise_for_status()
            lines = r.text.strip().split('\n')
            break
        except Exception as e:
            print(f'  ONS 取得失敗 (attempt {attempt+1}): {e}')
            lines = []
            time.sleep(5)

    if not lines:
        print('  UK CPI: ONS 取得失敗。スキップ。')
        return pd.DataFrame()

    # ONS CSV は最初数行がメタデータ
    # 実際の数値は "2020 JAN", "2020 FEB" ... の形式
    data_rows = []
    for line in lines:
        parts = line.strip().split(',')
        if len(parts) >= 2:
            period = parts[0].strip().strip('"')
            val    = parts[1].strip().strip('"')
            # "2022 JAN" 形式
            try:
                dt = pd.to_datetime(period, format='%Y %b')
                v  = float(val)
                data_rows.append({'date': dt, 'value': v})
            except Exception:
                continue

    if not data_rows:
        print('  UK CPI: データパース失敗。スキップ。')
        return pd.DataFrame()

    raw = pd.DataFrame(data_rows).sort_values('date').reset_index(drop=True)
    raw['prev'] = raw['value'].shift(1)
    raw = raw.dropna()
    raw = raw[(raw['date'] >= start) & (raw['date'] <= end)]

    for _, row in raw.iterrows():
        rel_date = _estimate_uk_cpi_release_date(row['date'].year, row['date'].month)
        for event_name in ['CPI y/y']:
            rows.append({
                'date':         rel_date.strftime('%Y-%m-%d'),
                'time':         '07:00',   # GMT
                'currency':     'GBP',
                'event':        event_name,
                'actual':       f'{row["value"]:.1f}%',
                'forecast':     f'{row["prev"]:.1f}%',
                'previous':     f'{row["prev"]:.1f}%',
                'actual_val':   round(row['value'], 3),
                'forecast_val': round(row['prev'], 3),
                'previous_val': round(row['prev'], 3),
                'source':       'ONS/D7G7',
            })

    df = pd.DataFrame(rows)
    if len(df):
        print(f'  UK CPI: {len(df)} 件 ({df["date"].min()} ~ {df["date"].max()})')
    return df


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='経済指標実データ取得')
    parser.add_argument('--from', dest='date_from', default='2022-01-01')
    parser.add_argument('--to',   dest='date_to',   default='2026-05-31')
    args = parser.parse_args()

    print('=' * 60)
    print('  fetch_news_data.py')
    print(f'  期間: {args.date_from} ~ {args.date_to}')
    print('  データソース: FRED (NFP/CPI) + ONS (UK CPI)')
    print('  forecast = 前回値 (naive近似)')
    print('=' * 60)

    dfs = []

    nfp = fetch_nfp(args.date_from, args.date_to)
    if len(nfp):
        dfs.append(nfp)

    us_cpi = fetch_us_cpi(args.date_from, args.date_to)
    if len(us_cpi):
        dfs.append(us_cpi)

    uk_cpi = fetch_uk_cpi(args.date_from, args.date_to)
    if len(uk_cpi):
        dfs.append(uk_cpi)

    if not dfs:
        print('\nERROR: データが1件も取得できませんでした。')
        return

    # 結合・ソート
    out = pd.concat(dfs, ignore_index=True)
    out = out.sort_values(['date', 'time', 'currency']).reset_index(drop=True)

    # news_event_bt.py が読む列のみ残す
    out_csv = out[['date', 'time', 'currency', 'event', 'actual', 'forecast', 'previous']]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_csv.to_csv(str(OUT_CSV), index=False)

    print(f'\n{"="*60}')
    print(f'  保存: {OUT_CSV}')
    print(f'  合計: {len(out_csv)} 件')
    print()
    print(out_csv.groupby(['currency', 'event']).size().to_string())
    print()
    print('  ※ forecast = 前回値 (naive近似). 真のコンセンサス予想との差異あり')
    print('  ※ release date は推定値 (BLS/ONS の実際スケジュールと ±数日誤差あり)')
    print()
    print('次のステップ:')
    print('  python3 optimizer/news_event_bt.py --quick')
    print('  python3 optimizer/news_event_bt.py')


if __name__ == '__main__':
    main()
