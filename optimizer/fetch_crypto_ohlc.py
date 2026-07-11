"""
fetch_crypto_ohlc.py - 取引所(Binance)から長期 OHLC を取得 (crypto 拡張の真値データ役)。

背景:
    FX bot の土日無稼働を埋めるための crypto 拡張 (2026-07-11 設計)。
    MT5 が使えない crypto では ccxt(取引所REST) が最大の実装差分。本スクリプトは
    Dukascopy 相当の「口座不要・公開API・長期」データ取得を担う。
    候補A(ETH/BTC比率の平均回帰)の検証に必要な 4h・5年+ を取得する。

    ETH/BTC は Binance 現物に直接上場(ETHBTC)しているため、比率を USDT建て2本から
    合成せず「実際に約定される真の比率OHLC」をそのまま取れる(合成ノイズ・非同期の排除)。
    BTC/USDT・ETH/USDT も併せて取り、水準/クロスチェック用に保持する。

出力:
    data/<SYMBOL>_<tf>.csv = 「datetime,open,high,low,close,volume」(UTC naive)。
    SYMBOL は BT ローダ互換のため区切り無し大文字(ETHBTC / BTCUSDT / ETHUSDT)。
    既存 FX データ(data/*_dukas.csv 等)とは名前空間が別で衝突しない。
    data/ は .gitignore 済(Binance から再取得可能)。BT 結果 CSV のみ commit する。

実行 (専用venv):
    .venv_crypto/bin/python optimizer/fetch_crypto_ohlc.py --tf 4h --years 6
    .venv_crypto/bin/python optimizer/fetch_crypto_ohlc.py --tf 1d --symbols ETHBTC
"""

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import ccxt

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
UTC = timezone.utc

# ccxt symbol(取引所表記) -> 出力ファイルの SYMBOL(区切り無し大文字, BTローダ互換)。
SYMBOLS = {
    'ETH/BTC': 'ETHBTC',      # 候補A の主対象(現物直接上場=真の比率)
    'BTC/USDT': 'BTCUSDT',    # 水準/クロスチェック用
    'ETH/USDT': 'ETHUSDT',    # 水準/クロスチェック用
}

# ccxt timeframe -> (ミリ秒/本)。ページング境界計算に使用。
TF_MS = {
    '1h': 3_600_000,
    '4h': 14_400_000,
    '1d': 86_400_000,
}


def fetch_symbol(ex, symbol, timeframe, since_ms, end_ms, limit=1000):
    """since_ms~end_ms を limit 本ずつページングして OHLCV を全取得。"""
    step = TF_MS[timeframe]
    frames = []
    cur = since_ms
    while cur < end_ms:
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cur, limit=limit)
        except Exception as e:
            print(f'    [warn] {datetime.fromtimestamp(cur/1000, UTC).date()} fetch失敗: {e}')
            time.sleep(2.0)
            continue
        if not batch:
            break
        frames.extend(batch)
        last = batch[-1][0]
        first_d = datetime.fromtimestamp(batch[0][0] / 1000, UTC).date()
        last_d = datetime.fromtimestamp(last / 1000, UTC).date()
        print(f'    {first_d}~{last_d}: {len(batch)}本')
        nxt = last + step
        if nxt <= cur:              # 進捗が無ければ打ち切り(無限ループ防止)
            break
        cur = nxt
        time.sleep(ex.rateLimit / 1000.0)   # ccxt 推奨レート制限を尊重
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates(subset='ts').sort_values('ts')
    df = df[df['ts'] < end_ms]
    df['datetime'] = pd.to_datetime(df['ts'], unit='ms', utc=True).dt.tz_localize(None)
    return df[['datetime', 'open', 'high', 'low', 'close', 'volume']]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='4h', choices=list(TF_MS.keys()))
    ap.add_argument('--years', type=float, default=6.0)
    ap.add_argument('--symbols', nargs='+', default=list(SYMBOLS.keys()),
                    help='ccxt表記(ETH/BTC 等) または 出力SYMBOL(ETHBTC 等)')
    ap.add_argument('--exchange', default='binance')
    args = ap.parse_args()

    ex = getattr(ccxt, args.exchange)({'enableRateLimit': True})
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=int(args.years * 365))
    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ccxt表記/出力SYMBOL どちらの指定も受け付ける。
    out_to_ccxt = {v: k for k, v in SYMBOLS.items()}
    requested = []
    for s in args.symbols:
        if s in SYMBOLS:
            requested.append((s, SYMBOLS[s]))
        elif s in out_to_ccxt:
            requested.append((out_to_ccxt[s], s))
        else:
            print(f'[{s}] 未対応symbol, skip')

    print(f'fetch_crypto_ohlc: exchange={args.exchange} tf={args.tf} '
          f'{start.date()}~{end.date()}')
    for ccxt_sym, out_sym in requested:
        print(f'\n[{out_sym}_{args.tf}] 取得中... ({ccxt_sym})')
        df = fetch_symbol(ex, ccxt_sym, args.tf, since_ms, end_ms)
        if df.empty:
            print(f'[{out_sym}_{args.tf}] 取得0本'); continue
        out = DATA_DIR / f'{out_sym}_{args.tf}.csv'
        df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
        span = f"{df['datetime'].iloc[0].date()}~{df['datetime'].iloc[-1].date()}"
        print(f'[{out_sym}_{args.tf}] {len(df)}本 保存 ({span}) -> {out}')


if __name__ == '__main__':
    main()
