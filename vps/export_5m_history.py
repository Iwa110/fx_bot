"""
export_5m_history.py - MT5から長期5m足(既定2年)をエクスポートして data/*_5m.csv を更新。

背景:
    update_data.py は yfinance 依存で 5m は直近~60日しか取得できず、data/*_5m.csv は
    約3ヶ月しか貯まらない(過去にさかのぼれない)。session_fakeout_bt 等の5m依存BTを
    2年分で検証するには MT5端末が保持する長期履歴を一括エクスポートする必要がある。

動作:
    1. enabled な全ブローカー(axiory/exness/oanda)に順次接続し、ペアごとに最も深い
       (本数の多い)履歴を採用する。demo端末の浅い供給を別端末で補える。
    2. fetch_deep: copy_rates_from / copy_rates_range を sleep付きで複数回呼び、端末の
       非同期履歴DLを能動的に誘発(初回が空/浅くてもリトライで深掘り)。
    3. data/<pair>_5m.csv を「datetime,open,high,low,close,volume」形式で上書き保存。
       (update_data.py の追記形式と一致 = 以後の日次追記/read_last_datetime と互換、
        かつ BT側ローダ index_col=0+小文字ohlc とも互換)
    4. --no-push でなければ add -> commit -> pull --rebase -> push。

これは一度きりの実行を想定(常駐タスク登録は不要)。差分の日次更新は update_data.py が継続。
2年に満たなくても取得できた最深を保存し、目標比%を報告する(浅くても判定材料になる)。

Usage (VPS):
    python vps\\export_5m_history.py                 # 2年・3ペア・M5・git push
    python vps\\export_5m_history.py --years 2 --no-push
    python vps\\export_5m_history.py --pairs GBPJPY EURJPY GBPUSD --tf 5m

フォールバック(全ブローカーで0本/浅すぎる時のみ手作業):
    - MT5で対象ペアのM5チャートを開き End→PageUp で最古までスクロール(履歴DL誘発)→再実行。
    - Tools > Options > Charts の "Max bars in chart" を Unlimited に。
    - それでも浅い場合、その口座のサーバが深いM5を配信していない(口座/ブローカー固有の上限)。
"""

import sys
import os
import time
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


def fetch_deep(sym: str, tf, start: datetime, target_bars: int,
               tries: int = 10, wait: float = 4.0):
    """履歴DLを能動的に誘発しつつ最深データを取得。
    copy_rates_range は未DL区間を要求すると端末がサーバから非同期DLを開始するが、
    初回は空/浅いことがある。本数が増えなくなるか目標到達まで sleep+再試行する。
    戻り: (rates(numpy) or None, 試行ログ文字列)"""
    now = datetime.now(tz=UTC)
    best = None
    best_n = 0
    stale = 0
    for i in range(tries):
        # 古い側を起点にした copy_rates_from でも DL を誘発できる(2方向で確実化)
        mt5.copy_rates_from(sym, tf, start, target_bars)
        rates = mt5.copy_rates_range(sym, tf, start, now)
        n = 0 if rates is None else len(rates)
        if n > best_n:
            best, best_n, stale = rates, n, 0
        else:
            stale += 1
        oldest = (pd.to_datetime(rates['time'][0], unit='s').date()
                  if n else 'なし')
        print(f'    try{i+1}: {n}本 (最古={oldest})')
        if best_n >= int(target_bars * 0.95):
            break
        if stale >= 2:   # 2回連続で増えない=端末の供給上限
            break
        time.sleep(wait)
    return best, best_n


def fetch_pair_on_broker(pair: str, tf_key: str, start: datetime,
                         target_bars: int):
    """現在接続中のブローカーで pair を取得。戻り: (df or None, n)。"""
    sym = resolve_symbol(pair)
    if sym is None:
        return None, 0
    rates, n = fetch_deep(sym, TF_MAP[tf_key], start, target_bars)
    if not n:
        return None, 0
    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
    df = df.rename(columns={'tick_volume': 'volume'})
    df = df[COLUMNS].sort_values('datetime').drop_duplicates('datetime')
    return df, len(df)


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
    # 2年M5の理論本数 ≈ 365*2*24*12*(5/7営業) ≈ 150k。tfで調整。
    bars_per_day = 288 if args.tf == '5m' else 24
    target_bars = int(args.years * 365 * (5 / 7) * bars_per_day)
    print('=' * 60)
    print(f'export_5m_history: pairs={args.pairs} tf={args.tf} '
          f'since={start.date()} target≈{target_bars}本')
    print('=' * 60)

    # enabled全ブローカーを探索し、ペアごとに「最深(最多本数)」を採用する。
    # demo端末の浅い供給を別端末で補える。
    best = {p: (None, 0, None) for p in args.pairs}  # pair -> (df, n, broker)
    any_conn = False
    for key in BROKER_ORDER:
        cfg = BROKERS.get(key)
        if not cfg or not cfg.get('enabled', True):
            print(f'\n[broker] skip {key} (未定義/enabled=False)')
            continue
        print(f'\n[broker] {key} へ接続試行...')
        if not connect_mt5(key):
            disconnect_mt5(); continue
        any_conn = True
        print(f'[broker] {key} 接続成功')
        try:
            for pair in args.pairs:
                print(f'  [{pair}_{args.tf}] @ {key}')
                df, n = fetch_pair_on_broker(pair, args.tf, start, target_bars)
                if n > best[pair][1]:
                    best[pair] = (df, n, key)
                    print(f'    -> 暫定最深 {n}本 ({key})')
        finally:
            disconnect_mt5()

    if not any_conn:
        print('\n[ERROR] 接続可能なブローカーが無い (MT5端末が起動中か確認)')
        sys.exit(1)

    files, n_ok = [], 0
    print('\n' + '=' * 60 + '\n[結果サマリ]')
    for pair in args.pairs:
        df, n, src = best[pair]
        if n == 0:
            print(f'  {pair}_{args.tf}: 全ブローカーで取得0本 '
                  f'(深いM5を配信する口座が無い → 下記フォールバック参照)')
            continue
        out = DATA_DIR / f'{pair}_{args.tf}.csv'
        df.to_csv(out, index=False, date_format='%Y-%m-%d %H:%M:%S')
        span = f"{df['datetime'].iloc[0].date()}~{df['datetime'].iloc[-1].date()}"
        cover = n / target_bars * 100
        print(f'  {pair}_{args.tf}: {n}本 ({span}, 目標比{cover:.0f}%) '
              f'src={src} -> {out.name}')
        files.append(f'data/{pair}_{args.tf}.csv')
        n_ok += 1

    if not files:
        print('\n[ERROR] エクスポート成功ペア0')
        print('  フォールバック: MT5で対象ペアのM5チャートを開き End/PageUp で最古まで'
              'スクロール(履歴DL誘発)→再実行。'
              'または Tools>Options>Charts の "Max bars" を Unlimited に。')
        sys.exit(1)

    if args.no_push:
        print(f'[done] {n_ok}ペア保存 (--no-push: git操作なし)')
    else:
        git_push(files, n_ok)


if __name__ == '__main__':
    main()
