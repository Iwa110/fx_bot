"""
fetch_dukascopy_ohlc.py - Dukascopy から長期 OHLC を取得 (1h/4h/5m/D1 汎用)。

背景:
    update_data.py の yfinance は intraday(1h/5m)が ~729日/~60日上限で2年超を取得不可。
    Grid実マネー化の頑健性検証には 2015 CHFショック / 2016 GBPフラッシュクラッシュ /
    2020 COVID / 2022 英国債危機・円介入 等の「本物のテール局面」を含む長期データが要る。
    Dukascopy(無料・口座不要・~2003以降)から直接取得する。

出力:
    data/<pair>_<tf>_dukas.csv = 「datetime,open,high,low,close,volume」(UTC naive)。
    既存 yfinance 版 (data/<pair>_1h.csv) を上書きしない (検証済み2年データを温存)。
    BT側ローダ互換 (index_col=0 + 小文字ohlc)。BID側OHLC (コストはBT側でspread加味)。

実行 (専用venv):
    .venv_dukas/bin/python optimizer/fetch_dukascopy_ohlc.py --tf 1h --years 11
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import dukascopy_python as d
from dukascopy_python.instruments import (
    INSTRUMENT_FX_CROSSES_GBP_JPY,
    INSTRUMENT_FX_CROSSES_CHF_JPY,
    INSTRUMENT_FX_CROSSES_NZD_JPY,
    INSTRUMENT_FX_CROSSES_AUD_CAD,
    INSTRUMENT_FX_CROSSES_AUD_NZD,
    INSTRUMENT_FX_CROSSES_EUR_GBP,
    INSTRUMENT_FX_CROSSES_EUR_CHF,
    INSTRUMENT_FX_CROSSES_EUR_JPY,
    INSTRUMENT_FX_MAJORS_USD_JPY,
    INSTRUMENT_FX_MAJORS_NZD_USD,
    INSTRUMENT_FX_MAJORS_AUD_USD,
    INSTRUMENT_FX_MAJORS_EUR_USD,
    INSTRUMENT_FX_MAJORS_GBP_USD,
    INSTRUMENT_FX_MAJORS_USD_CHF,
    INSTRUMENT_FX_MAJORS_USD_CAD,
    # 相関クロス・スケール検証(2026-06-15)候補
    INSTRUMENT_FX_CROSSES_NZD_CAD,
    INSTRUMENT_FX_CROSSES_GBP_CHF,
    INSTRUMENT_FX_CROSSES_AUD_CHF,
    INSTRUMENT_FX_CROSSES_NZD_CHF,
    INSTRUMENT_FX_CROSSES_CAD_CHF,
    INSTRUMENT_FX_CROSSES_EUR_CAD,
    INSTRUMENT_FX_CROSSES_GBP_CAD,
    INSTRUMENT_FX_CROSSES_EUR_AUD,
    INSTRUMENT_FX_CROSSES_EUR_NZD,
    INSTRUMENT_FX_CROSSES_GBP_AUD,
    INSTRUMENT_FX_CROSSES_GBP_NZD,
    # コモディティ -> 資源通貨 外生ドライバ検証(2026-06-15)
    INSTRUMENT_FX_METALS_XAU_USD,
    INSTRUMENT_FX_METALS_XAG_USD,
    INSTRUMENT_CMD_ENERGY_E_LIGHT,
    INSTRUMENT_CMD_ENERGY_E_BRENT,
    INSTRUMENT_CMD_METALS_COPPER_CMD_USD,
)

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
UTC = timezone.utc

# Grid v7 4ペア + 実マネーBB(USDJPY) + 補助メジャー。
PAIRS = {
    'GBPJPY': INSTRUMENT_FX_CROSSES_GBP_JPY,
    'CHFJPY': INSTRUMENT_FX_CROSSES_CHF_JPY,
    'NZDJPY': INSTRUMENT_FX_CROSSES_NZD_JPY,
    'AUDCAD': INSTRUMENT_FX_CROSSES_AUD_CAD,
    'AUDNZD': INSTRUMENT_FX_CROSSES_AUD_NZD,
    'EURGBP': INSTRUMENT_FX_CROSSES_EUR_GBP,
    'EURCHF': INSTRUMENT_FX_CROSSES_EUR_CHF,
    'USDJPY': INSTRUMENT_FX_MAJORS_USD_JPY,
    'EURJPY': INSTRUMENT_FX_CROSSES_EUR_JPY,
    'NZDUSD': INSTRUMENT_FX_MAJORS_NZD_USD,
    'AUDUSD': INSTRUMENT_FX_MAJORS_AUD_USD,
    'EURUSD': INSTRUMENT_FX_MAJORS_EUR_USD,
    'GBPUSD': INSTRUMENT_FX_MAJORS_GBP_USD,
    'USDCHF': INSTRUMENT_FX_MAJORS_USD_CHF,
    'USDCAD': INSTRUMENT_FX_MAJORS_USD_CAD,
    # 相関クロス・スケール検証(2026-06-15)候補
    'NZDCAD': INSTRUMENT_FX_CROSSES_NZD_CAD,
    'GBPCHF': INSTRUMENT_FX_CROSSES_GBP_CHF,
    'AUDCHF': INSTRUMENT_FX_CROSSES_AUD_CHF,
    'NZDCHF': INSTRUMENT_FX_CROSSES_NZD_CHF,
    'CADCHF': INSTRUMENT_FX_CROSSES_CAD_CHF,
    'EURCAD': INSTRUMENT_FX_CROSSES_EUR_CAD,
    'GBPCAD': INSTRUMENT_FX_CROSSES_GBP_CAD,
    'EURAUD': INSTRUMENT_FX_CROSSES_EUR_AUD,
    'EURNZD': INSTRUMENT_FX_CROSSES_EUR_NZD,
    'GBPAUD': INSTRUMENT_FX_CROSSES_GBP_AUD,
    'GBPNZD': INSTRUMENT_FX_CROSSES_GBP_NZD,
    # コモディティ -> 資源通貨 外生ドライバ検証(2026-06-15)
    'XAUUSD': INSTRUMENT_FX_METALS_XAU_USD,
    'XAGUSD': INSTRUMENT_FX_METALS_XAG_USD,
    'WTI': INSTRUMENT_CMD_ENERGY_E_LIGHT,
    'BRENT': INSTRUMENT_CMD_ENERGY_E_BRENT,
    'COPPER': INSTRUMENT_CMD_METALS_COPPER_CMD_USD,
}

# tf -> (dukascopy interval, chunk日数). limit=30,000本/fetch を超えないよう設定。
TF = {
    '1h': (d.INTERVAL_HOUR_1, 300),    # 300d*24=7,200 < 30k
    '4h': (d.INTERVAL_HOUR_4, 1000),   # 1000d*6=6,000
    '15m': (d.INTERVAL_MIN_15, 160),   # 160d*96≈15,360
    '5m': (d.INTERVAL_MIN_5, 55),      # 55d*288≈15,840
    'D1': (d.INTERVAL_DAY_1, 3000),
}


def fetch_pair(instrument, interval, start, end, chunk_days):
    frames = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        try:
            df = d.fetch(instrument, interval, d.OFFER_SIDE_BID, cur, nxt)
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
    ap.add_argument('--tf', default='1h', choices=list(TF.keys()))
    ap.add_argument('--years', type=float, default=11.0)
    ap.add_argument('--pairs', nargs='+', default=list(PAIRS.keys()))
    ap.add_argument('--suffix', default='_dukas', help='出力ファイル名サフィックス(yfinance版温存)')
    args = ap.parse_args()

    interval, chunk = TF[args.tf]
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=int(args.years * 365))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f'fetch_dukascopy_ohlc: tf={args.tf} pairs={args.pairs} {start.date()}~{end.date()}')

    for pair in args.pairs:
        inst = PAIRS.get(pair)
        if inst is None:
            print(f'[{pair}] 未対応instrument, skip'); continue
        print(f'\n[{pair}_{args.tf}] 取得中...')
        df = fetch_pair(inst, interval, start, end, chunk)
        if df.empty:
            print(f'[{pair}_{args.tf}] 取得0本'); continue
        df = df.reset_index().rename(columns={'timestamp': 'datetime'})
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True).dt.tz_localize(None)
        df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']]
        out = DATA_DIR / f'{pair}_{args.tf}{args.suffix}.csv'
        df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
        span = f"{df['datetime'].iloc[0].date()}~{df['datetime'].iloc[-1].date()}"
        print(f'[{pair}_{args.tf}] {len(df)}本 保存 ({span}) -> {out}')


if __name__ == '__main__':
    main()
