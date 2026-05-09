"""
check_broker_connection.py - ブローカー接続確認スクリプト

使用例:
  python check_broker_connection.py              # enabled=True の全ブローカーを確認
  python check_broker_connection.py --broker oanda
  python check_broker_connection.py --broker oanda_demo
  python check_broker_connection.py --discover   # 現在接続中のMT5情報を表示（.env設定用）
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from broker_config import BROKERS
from broker_utils import connect_mt5, disconnect_mt5, resolve_symbol


def discover_current() -> None:
    """既に起動・ログイン済みのMT5ターミナルに接続して情報を表示する。
    .env に設定すべき値を確認するために使う。"""
    print('=' * 60)
    print('現在のMT5ターミナル情報（--discover モード）')
    print('ターミナルは既に起動・ログイン済みである必要があります。')
    print()

    ok = mt5.initialize()
    if not ok:
        print(f'接続失敗: {mt5.last_error()}')
        print('MT5ターミナルが起動していないか、接続できません。')
        return

    account = mt5.account_info()
    terminal = mt5.terminal_info()

    if account:
        print('--- account_info ---')
        print(f'  login   : {account.login}   ← .env の XXXX_LOGIN に設定')
        print(f'  server  : {account.server}  ← .env の XXXX_SERVER に設定')
        print(f'  company : {account.company}')
        print(f'  balance : {account.balance:,.0f}')
        print(f'  currency: {account.currency}')
        print(f'  is_demo : {account.trade_mode}  (0=DEMO 1=CONTEST 2=REAL)')
    else:
        print('account_info 取得失敗')

    if terminal:
        print()
        print('--- terminal_info ---')
        print(f'  path    : {terminal.path}  ← broker_config.py の path に設定')
        print(f'  data_path: {terminal.data_path}')

    mt5.shutdown()
    print()
    print('.env に追記する例:')
    if account and terminal:
        prefix = 'OANDA_DEMO' if account.trade_mode != 2 else 'OANDA'
        print(f'  {prefix}_LOGIN={account.login}')
        print(f'  {prefix}_PASSWORD=（パスワードを手動で記入）')
        print(f'  {prefix}_SERVER={account.server}')

CHECK_SYMBOLS = [
    'GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD',
    'EURJPY', 'AUDJPY', 'EURNZD', 'EURCAD',
]


def check_one(broker_key: str) -> bool:
    sep = '=' * 60
    print(sep)
    print(f'ブローカー: {broker_key}')

    cfg = BROKERS.get(broker_key)
    if cfg is None:
        print(f'  [ERROR] 不明なブローカーキー: {broker_key}')
        return False

    if not cfg.get('enabled', True):
        print(f'  [SKIP] enabled=False')
        return False

    print(f'  server : {cfg["server"]}')
    print(f'  login  : {cfg["login"]}')
    print(f'  suffix : "{cfg["symbol_suffix"]}"')
    print(f'  is_live: {cfg["is_live"]}')

    if not connect_mt5(broker_key):
        print(f'  [ERROR] MT5接続失敗')
        return False

    account = mt5.account_info()
    if account is None:
        print(f'  [ERROR] account_info 取得失敗')
        disconnect_mt5()
        return False

    print(f'  --- account_info ---')
    print(f'  company : {account.company}')
    print(f'  server  : {account.server}')
    print(f'  balance : {account.balance:,.0f}')
    print(f'  currency: {account.currency}')
    print(f'  leverage: 1:{account.leverage}')

    print(f'  --- symbol resolution ---')
    all_ok = True
    for base in CHECK_SYMBOLS:
        resolved = resolve_symbol(base, broker_key)
        status = 'OK  -> ' + str(resolved) if resolved else 'NG  (not found)'
        print(f'  {base:12s}: {status}')
        if resolved is None:
            all_ok = False

    disconnect_mt5()
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description='MT5ブローカー接続確認')
    parser.add_argument(
        '--broker',
        choices=list(BROKERS.keys()),
        default=None,
        help='確認するブローカーキー（省略時は enabled=True 全ブローカー）',
    )
    parser.add_argument(
        '--discover',
        action='store_true',
        help='現在起動中のMT5ターミナルに接続して .env 設定値を表示する',
    )
    args = parser.parse_args()

    if args.discover:
        discover_current()
        return

    if args.broker:
        targets = [args.broker]
    else:
        targets = [k for k, v in BROKERS.items() if v.get('enabled', True)]

    results: dict[str, bool] = {}
    for key in targets:
        results[key] = check_one(key)

    print('=' * 60)
    print('結果サマリー:')
    for key, ok in results.items():
        mark = 'OK' if ok else 'NG'
        print(f'  [{mark}] {key}')


if __name__ == '__main__':
    main()
