"""
export_5m_history.py - MT5から長期5m足(既定2年)をエクスポートして data/*_5m.csv を更新。

背景:
    update_data.py は yfinance 依存で 5m は直近~60日しか取得できず、data/*_5m.csv は
    約3ヶ月しか貯まらない(過去にさかのぼれない)。session_fakeout_bt 等の5m依存BTを
    2年分で検証するには MT5端末が保持する長期履歴を一括エクスポートする必要がある。

動作:
    1. 有効ブローカーに順次接続を試行 (axiory -> exness -> oanda)。最初に成功した端末を使用。
    2. PAIRS x M5 を copy_rates_range で START..now 取得。
    3. data/<pair>_5m.csv を「datetime,open,high,low,close,volume」形式で上書き保存。
       (update_data.py の追記形式と一致 = 以後の日次追記/read_last_datetime と互換、
        かつ BT側ローダ index_col=0+小文字ohlc とも互換)
    4. --no-push でなければ add -> commit -> pull --rebase -> push。

これは一度きりの実行を想定(常駐タスク登録は不要)。差分の日次更新は update_data.py が継続。

Usage (VPS):
    python vps\\export_5m_history.py                 # 2年・3ペア・M5・git push
    python vps\\export_5m_history.py --years 2 --no-push
    python vps\\export_5m_history.py --pairs GBPJPY EURJPY GBPUSD --tf 5m

注意:
    端末が該当ペアのM5履歴を未取得だと取得本数が極端に少ない場合がある。その場合は
    MT5でそのペアのM5チャートを一度開いて最古までスクロール(履歴DLを誘発)してから再実行。
"""

import sys
import os
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError as e:
    print(f'[ERROR] 必須パッケージ未インストール: {e}')
    sys.exit(1)

from broker_utils import connect_mt5, disconnect_mt5
from broker_config import BROKERS

BASE_DIR = Path(r'C:\Users\Administrator\fx_bot')
DATA_DIR = BASE_DIR / 'data'
JST = timezone(timedelta(hours=9))
UTC = timezone.utc

DEFAULT_PAIRS = ['GBPJPY', 'EURJPY', 'GBPUSD']
TF_MAP = {'5m': mt5.TIMEFRAME_M5, '1h': mt5.TIMEFRAME_H1}
COLUMNS = ['datetime', 'open', 'high', 'low', 'close', 'volume']
# 接続を試すブローカー優先順 (enabled=True のもののみ実際に接続)
BROKER_ORDER = ['axiory', 'exness', 'oanda']


def pick_broker() -> str | None:
    """先頭から接続成功した broker_key を返す。失敗なら None。"""
    for key in BROKER_ORDER:
        cfg = BROKERS.get(key)
        if not cfg or not cfg.get('enabled', True):
            print(f'[broker] skip {key} (未定義/enabled=False)')
            continue
        print(f'[broker] {key} へ接続試行...')
        if connect_mt5(key):
            print(f'[broker] {key} 接続成功')
            return key
        disconnect_mt5()
    return None


def resolve_symbol(pair: str) -> str | None:
    """ブローカー固有のサフィックス(.r / m 等)を吸収して実シンボル名を返す。"""
    if mt5.symbol_select(pair, True):
        return pair
    matches = [s.name for s in (mt5.symbols_get() or []) if s.name.startswith(pair)]
    # サフィックスが短いものを優先 (例: GBPJPY < GBPJPY.r < GBPJPYmicro)
    matches.sort(key=len)
    for name in matches:
        if mt5.symbol_select(name, True):
            print(f'[symbol] {pair} -> {name}')
            return name
    print(f'[symbol] {pair}: 該当シンボル無し')
    return None


def export_one(pair: str, tf_key: str, start: datetime) -> int:
    sym = resolve_symbol(pair)
    if sym is None:
        return 0
    rates = mt5.copy_rates_range(sym, TF_MAP[tf_key], start, datetime.now(tz=UTC))
    if rates is None or len(rates) == 0:
        print(f'[{pair}_{tf_key}] 取得0本 (端末にM5履歴が無い可能性 -> docstring注意参照)')
        return 0
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
    df = df.rename(columns={'tick_volume': 'volume'})
    df = df[COLUMNS].sort_values('datetime').drop_duplicates('datetime')
    out = DATA_DIR / f'{pair}_{tf_key}.csv'
    df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
    span = f"{df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()}"
    print(f'[{pair}_{tf_key}] {len(df)}本 保存 ({span}) -> {out.name}')
    return len(df)


def git_push(files: list[str], n_pairs: int) -> bool:
    repo = str(BASE_DIR)
    today = datetime.now(JST).strftime('%Y-%m-%d')

    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=180)

    r = run(['git', '-C', repo, 'add', *files])
    if r.returncode != 0:
        print(f'[git] ERROR add: {r.stderr.strip()}'); return False
    r = run(['git', '-C', repo, 'diff', '--cached', '--quiet'])
    if r.returncode == 0:
        print('[git] 変更なし、push省略'); return True
    msg = f'data: export {n_pairs} pairs 5m history {today}'
    r = run(['git', '-C', repo, 'commit', '-m', msg])
    if r.returncode != 0:
        print(f'[git] ERROR commit: {r.stderr.strip()}'); return False
    print(f'[git] commit "{msg}"')
    r = run(['git', '-C', repo, 'pull', '--rebase'])
    if r.returncode != 0:
        print(f'[git] ERROR pull --rebase: {r.stderr.strip()}'); return False
    r = run(['git', '-C', repo, 'push'])
    if r.returncode != 0:
        print(f'[git] ERROR push: {r.stderr.strip()}'); return False
    print('[git] push 成功'); return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', nargs='+', default=DEFAULT_PAIRS)
    ap.add_argument('--tf', default='5m', choices=list(TF_MAP.keys()))
    ap.add_argument('--years', type=float, default=2.0)
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    start = datetime.now(tz=UTC) - timedelta(days=int(args.years * 365) + 5)
    print('=' * 60)
    print(f'export_5m_history: pairs={args.pairs} tf={args.tf} '
          f'since={start.date()}')
    print('=' * 60)

    broker = pick_broker()
    if broker is None:
        print('[ERROR] 接続可能なブローカーが無い (MT5端末が起動中か確認)')
        sys.exit(1)

    files, n_ok = [], 0
    try:
        for pair in args.pairs:
            if export_one(pair, args.tf, start) > 0:
                files.append(f'data/{pair}_{args.tf}.csv')
                n_ok += 1
    finally:
        disconnect_mt5()

    if not files:
        print('[ERROR] エクスポート成功ペア0'); sys.exit(1)

    if args.no_push:
        print(f'[done] {n_ok}ペア保存 (--no-push: git操作なし)')
    else:
        git_push(files, n_ok)


if __name__ == '__main__':
    main()
