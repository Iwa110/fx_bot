"""
check_broker_connection.py - ブローカー接続確認スクリプト

使用例:
  python check_broker_connection.py              # enabled=True の全ブローカーを確認
  python check_broker_connection.py --broker oanda
  python check_broker_connection.py --broker axiory
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from broker_config import BROKERS
from broker_utils import connect_mt5, disconnect_mt5, resolve_symbol

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
    args = parser.parse_args()

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
