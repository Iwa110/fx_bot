"""
fetch_funding_rates.py - 無期限先物(perpetual)のファンディングレート履歴を取得。

背景 (memory: project_crypto_extension_plan_20260711):
    候補B(研究BTのみ・国内現物では執行不可): ファンディング・ベーシス収穫
    = 現物ロング + 無期限ショート(デルタ中立)でファンディングを収穫する carry。
    無期限先物が前提のため海外取引所寄り。今は執行しないが、
    「税55%+繰越なし+取引所カウンターパーティ・リスクを上回るエッジか」を数字で見て
    将来の海外拡張の是非を判断するための研究データを取得する。

    ファンディングは通常 8h ごと(Binance)。funding>0 = ロングがショートへ支払う
    → デルタ中立(現物long+perp short)の建玉は funding>0 の時に収穫、funding<0 で支払い。

出力:
    data/FUNDING_<SYMBOL>_<exchange>.csv = 「datetime,funding_rate」(UTC naive, 8h間隔)。
    data/ は .gitignore 済(公開APIから再取得可能)。

実行 (専用venv):
    .venv_crypto/bin/python optimizer/fetch_funding_rates.py --years 6
    .venv_crypto/bin/python optimizer/fetch_funding_rates.py --exchange bybit
"""

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import ccxt

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
UTC = timezone.utc

# ccxt perp symbol -> 出力SYMBOL。linear USDT無期限。
SYMBOLS = {
    'BTC/USDT:USDT': 'BTCUSDT',
    'ETH/USDT:USDT': 'ETHUSDT',
}


def fetch_funding(ex, symbol, since_ms, end_ms, limit=1000):
    """funding rate history を since_ms~end_ms でページング取得。"""
    frames = []
    cur = since_ms
    last_seen = None
    while cur < end_ms:
        try:
            batch = ex.fetch_funding_rate_history(symbol, since=cur, limit=limit)
        except Exception as e:
            print(f'    [warn] {datetime.fromtimestamp(cur/1000, UTC).date()} fetch失敗: {e}')
            time.sleep(2.0)
            continue
        if not batch:
            break
        frames.extend(batch)
        last = batch[-1]['timestamp']
        first_d = datetime.fromtimestamp(batch[0]['timestamp'] / 1000, UTC).date()
        last_d = datetime.fromtimestamp(last / 1000, UTC).date()
        print(f'    {first_d}~{last_d}: {len(batch)}本')
        if last == last_seen:                 # 進捗なし=打ち切り
            break
        last_seen = last
        cur = last + 1
        time.sleep(ex.rateLimit / 1000.0)
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame([{'ts': r['timestamp'], 'funding_rate': r['fundingRate']}
                       for r in frames if r.get('fundingRate') is not None])
    df = df.drop_duplicates(subset='ts').sort_values('ts')
    df = df[df['ts'] < end_ms]
    df['datetime'] = pd.to_datetime(df['ts'], unit='ms', utc=True).dt.tz_localize(None)
    return df[['datetime', 'funding_rate']]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=float, default=6.0)
    ap.add_argument('--exchange', default='binance', help='binance / bybit')
    ap.add_argument('--symbols', nargs='+', default=list(SYMBOLS.keys()))
    args = ap.parse_args()

    ex = getattr(ccxt, args.exchange)({'enableRateLimit': True,
                                       'options': {'defaultType': 'swap'}})
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=int(args.years * 365))
    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f'fetch_funding_rates: exchange={args.exchange} {start.date()}~{end.date()}')
    for ccxt_sym in args.symbols:
        out_sym = SYMBOLS.get(ccxt_sym, ccxt_sym.replace('/', '').replace(':', ''))
        print(f'\n[FUNDING {out_sym} {args.exchange}] 取得中... ({ccxt_sym})')
        df = fetch_funding(ex, ccxt_sym, since_ms, end_ms)
        if df.empty:
            print(f'[FUNDING {out_sym}] 取得0本'); continue
        out = DATA_DIR / f'FUNDING_{out_sym}_{args.exchange}.csv'
        df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
        ann = df['funding_rate'].mean() * 3 * 365 * 100    # 8h×3/日×365 の粗い年率
        span = f"{df['datetime'].iloc[0].date()}~{df['datetime'].iloc[-1].date()}"
        print(f'[FUNDING {out_sym}] {len(df)}本 保存 ({span}, 粗年率≈{ann:.1f}%) -> {out}')


if __name__ == '__main__':
    main()
