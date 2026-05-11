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

    kwargs: dict = {}

    # attach=True: MT5が既に起動・ログイン済みの場合は引数なしでアタッチ
    # 認証情報を渡すとIPC timeoutが発生するため
    if cfg.get('attach'):
        print('[broker_utils] initialize kwargs: {} (attach mode)')
        ok = mt5.initialize()
        if not ok:
            print('[broker_utils] MT5初期化失敗 (' + broker_key + '): ' + str(mt5.last_error()))
            return False
        # [FIX: 複数MT5端末が起動中の場合、意図しない端末にアタッチする問題を防ぐ]
        # attach後に正しいブローカーのloginか検証する
        expected_login = cfg.get('login')
        if expected_login:
            account = mt5.account_info()
            if account is None:
                print('[broker_utils] ' + broker_key + ': account_info取得失敗（attach後）')
                mt5.shutdown()
                return False
            if account.login != expected_login:
                print('[broker_utils] ' + broker_key + ': 接続先不一致 '
                      '(connected=' + str(account.login) + ', expected=' + str(expected_login) + ')'
                      ' -> MT5をシャットダウンして再試行してください')
                mt5.shutdown()
                return False
        return True

    # path が設定されていればターミナルを指定して起動
    if cfg.get('path'):
        kwargs['path'] = cfg['path']

    # login=0 は「未設定」として扱い、login/password を渡さない。
    if cfg.get('login'):
        kwargs['login']    = cfg['login']
        kwargs['password'] = cfg['password']
        kwargs['server']   = cfg['server']
    elif cfg.get('server'):
        kwargs['server'] = cfg['server']

    print('[broker_utils] initialize kwargs: ' +
          str({k: v if k != 'password' else '***' for k, v in kwargs.items()}))

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
