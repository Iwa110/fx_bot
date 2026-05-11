"""
test_trade_execution.py - 発注・決済の疎通テスト

各ブローカーで最小ロット(0.01)の成行注文を発注し、即決済する。
MT5接続・シンボル解決・order_send の一連の動作を確認する。

使用例:
  python test_trade_execution.py --broker oanda
  python test_trade_execution.py --broker axiory
  python test_trade_execution.py --broker exness
  python test_trade_execution.py --broker axiory --symbol GBPJPY
  python test_trade_execution.py --all   # enabled=True の全ブローカーを順次テスト
"""
import argparse
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from broker_config import BROKERS
from broker_utils import connect_mt5, disconnect_mt5, resolve_symbol

TEST_MAGIC   = 20269999   # テスト専用magic番号
TEST_LOT     = 0.01
TEST_SYMBOL  = 'USDJPY'   # デフォルトテストシンボル
COMMENT      = 'TEST_EXEC'


def _sep():
    print('=' * 60)


def _get_price(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None, None
    return tick.ask, tick.bid


def _place_order(symbol, direction, lot):
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f'  [ERROR] symbol_info 取得失敗: {symbol}')
        return None

    ask, bid = _get_price(symbol)
    if ask is None:
        print(f'  [ERROR] tick 取得失敗: {symbol}')
        return None

    if direction == 'buy':
        order_type = mt5.ORDER_TYPE_BUY
        price      = ask
        # SLはask-200pipsで十分離す（即決済するので実質使わない）
        sl = round(price - 200 * info.point, info.digits)
        tp = round(price + 200 * info.point, info.digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price      = bid
        sl = round(price + 200 * info.point, info.digits)
        tp = round(price - 200 * info.point, info.digits)

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        price,
        'sl':           sl,
        'tp':           tp,
        'deviation':    20,
        'magic':        TEST_MAGIC,
        'comment':      COMMENT,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        comment = result.comment if result else ''
        print(f'  [ERROR] 発注失敗: retcode={code} {comment}')
        return None

    print(f'  [OK]    発注成功: {direction.upper()} {lot}lot @ {price:.5f}  ticket={result.order}')
    return result.order


def _close_position(ticket, symbol):
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        # IOC + 即決済されている場合もある
        print(f'  [INFO]  ポジション未検出 ticket={ticket} (すでに決済済みの可能性)')
        return True

    p = pos[0]
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f'  [ERROR] symbol_info 取得失敗（決済時）')
        return False

    ask, bid = _get_price(symbol)
    if p.type == mt5.ORDER_TYPE_BUY:
        close_type  = mt5.ORDER_TYPE_SELL
        close_price = bid
    else:
        close_type  = mt5.ORDER_TYPE_BUY
        close_price = ask

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       p.volume,
        'type':         close_type,
        'position':     ticket,
        'price':        close_price,
        'deviation':    20,
        'magic':        TEST_MAGIC,
        'comment':      COMMENT + '_CLOSE',
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code    = result.retcode if result else 'None'
        comment = result.comment if result else ''
        print(f'  [ERROR] 決済失敗: retcode={code} {comment}')
        return False

    print(f'  [OK]    決済成功: @ {close_price:.5f}')
    return True


def test_one(broker_key, base_symbol=TEST_SYMBOL):
    _sep()
    print(f'ブローカー: {broker_key}  シンボル: {base_symbol}')

    cfg = BROKERS.get(broker_key)
    if cfg is None:
        print(f'  [ERROR] 不明なブローカーキー')
        return False

    if not cfg.get('enabled', True):
        print(f'  [SKIP]  enabled=False')
        return False

    if not connect_mt5(broker_key):
        print(f'  [ERROR] MT5接続失敗')
        return False

    account = mt5.account_info()
    if account is None:
        print(f'  [ERROR] account_info 取得失敗')
        disconnect_mt5()
        return False

    print(f'  接続先 : {account.server}  残高: {account.balance:,.0f} {account.currency}')

    symbol = resolve_symbol(base_symbol, broker_key)
    if symbol is None:
        print(f'  [ERROR] シンボル解決失敗: {base_symbol}')
        disconnect_mt5()
        return False
    print(f'  シンボル解決: {base_symbol} -> {symbol}')

    # BUY 発注
    print(f'  --- BUY 発注テスト ---')
    ticket = _place_order(symbol, 'buy', TEST_LOT)
    ok = False
    if ticket:
        time.sleep(1)
        ok = _close_position(ticket, symbol)

    disconnect_mt5()

    result_str = '[OK]' if (ticket and ok) else '[NG]'
    print(f'  結果: {result_str} broker={broker_key}')
    return ticket is not None and ok


def main():
    parser = argparse.ArgumentParser(description='発注・決済 疎通テスト')
    parser.add_argument('--broker',
                        choices=list(BROKERS.keys()),
                        default=None,
                        help='テストするブローカーキー')
    parser.add_argument('--all',
                        action='store_true',
                        help='enabled=True の全ブローカーを順次テスト')
    parser.add_argument('--symbol',
                        default=TEST_SYMBOL,
                        help=f'テストに使うシンボル（デフォルト: {TEST_SYMBOL}）')
    args = parser.parse_args()

    if not args.broker and not args.all:
        parser.print_help()
        sys.exit(1)

    if args.all:
        targets = [k for k, v in BROKERS.items() if v.get('enabled', True)]
    else:
        targets = [args.broker]

    results = {}
    for key in targets:
        results[key] = test_one(key, args.symbol)
        time.sleep(2)  # ブローカー間のインターバル

    _sep()
    print('結果サマリー:')
    for key, ok in results.items():
        mark = 'OK' if ok else 'NG'
        print(f'  [{mark}] {key}')


if __name__ == '__main__':
    main()
