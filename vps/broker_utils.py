"""
broker_utils.py - MT5接続・シンボル解決ユーティリティ

使用例:
    from broker_utils import connect_mt5, disconnect_mt5, resolve_symbol, is_live_broker

    connect_mt5('oanda')
    sym = resolve_symbol('GBPJPY', 'oanda')  # -> 'GBPJPY.cl' など
    disconnect_mt5()
"""
import MetaTrader5 as mt5
from typing import Optional
from broker_config import BROKERS


def connect_mt5(broker_key: str) -> bool:
    """
    BROKERS[broker_key] の設定で mt5.initialize() を呼び出す。
    enabled=False のエントリは即座に False を返す。
    is_live=True の場合は接続前に警告ログを出力する。
    成功時 True、失敗時 False。
    """
    cfg = BROKERS.get(broker_key)
    if cfg is None:
        print(f'[broker_utils] 不明なブローカーキー: {broker_key}')
        return False

    if not cfg.get('enabled', True):
        print(f'[broker_utils] {broker_key}: enabled=False のためスキップ')
        return False

    if cfg.get('is_live'):
        print('[broker_utils] *** 警告: ライブ口座 (' + broker_key + ') へ接続します ***')

    kwargs: dict = {
        'login':    cfg['login'],
        'password': cfg['password'],
        'server':   cfg['server'],
    }
    if cfg.get('path'):
        kwargs['path'] = cfg['path']

    ok = mt5.initialize(**kwargs)
    if not ok:
        print('[broker_utils] MT5初期化失敗 (' + broker_key + '): ' + str(mt5.last_error()))
    return ok


def disconnect_mt5() -> None:
    """mt5.shutdown() のラッパー"""
    mt5.shutdown()


def resolve_symbol(base: str, broker_key: str) -> Optional[str]:
    """
    ベースシンボル名とブローカーキーから実際のMT5シンボル名を動的解決して返す。
    解決順:
      1. base + suffix を symbol_info() で直接確認
      2. suffix なしの base を symbol_info() で確認
      3. symbols_get() で base に前方一致する最初のシンボルを返す
    見つからない場合は None を返す。
    """
    cfg = BROKERS.get(broker_key)
    if cfg is None:
        return None

    suffix: str = cfg.get('symbol_suffix', '')

    # 1. suffix 付きで直接確認
    if suffix:
        candidate = base + suffix
        if mt5.symbol_info(candidate) is not None:
            return candidate

    # 2. suffix なしで確認
    if mt5.symbol_info(base) is not None:
        return base

    # 3. 全シンボルから前方一致で探索
    all_syms = mt5.symbols_get()
    if all_syms:
        for s in all_syms:
            if s.name.startswith(base):
                return s.name

    return None


def build_symbol_map(base_symbols: list, broker_key: str) -> dict[str, str]:
    """
    ベースシンボルリストを {base: resolved_name} の辞書に変換する。
    解決できなかった場合はベース名をそのまま使う（フォールバック）。
    """
    result: dict[str, str] = {}
    for base in base_symbols:
        resolved = resolve_symbol(base, broker_key)
        result[base] = resolved if resolved is not None else base
        if resolved is None:
            print('[broker_utils] シンボル解決失敗（フォールバック）: ' + base)
    return result


def is_live_broker(broker_key: str) -> bool:
    """is_live=True のブローカーか判定する"""
    return bool(BROKERS.get(broker_key, {}).get('is_live', False))
