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

    # path_only=True: pathのみでinitialize（credentials渡しで口座変更イベントが発火し
    # terminal.trade_allowed=Falseになる問題の回避）
    # attach=Trueと異なりpathでターミナルを特定するため複数端末でも安全
    if cfg.get('path_only'):
        path = cfg.get('path', '')
        print('[broker_utils] initialize kwargs: {path: ...} (path_only mode)')
        ok = mt5.initialize(path=path) if path else mt5.initialize()
        if not ok:
            print('[broker_utils] MT5初期化失敗 (' + broker_key + '): ' + str(mt5.last_error()))
            return False
        expected_login = cfg.get('login')
        if expected_login:
            account = mt5.account_info()
            if account is None:
                print('[broker_utils] ' + broker_key + ': account_info取得失敗（path_only後）')
                mt5.shutdown()
                return False
            if account.login != expected_login:
                print('[broker_utils] ' + broker_key + ': 接続先不一致 '
                      '(connected=' + str(account.login) + ', expected=' + str(expected_login) + ')')
                mt5.shutdown()
                return False
        return True

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


def get_cost_pips(broker: str, symbol: str) -> float:
    """
    ブローカー・シンボルのラウンドトリップコストをpipsで返す。
    MT5のsymbol_info()から手数料を動的取得できる場合はそちらを優先。
    取得できない場合はBROKER_COSTSのデフォルト値を使用。
    口座通貨JPY想定: tick_valueはJPY建てで返るため USDJPY レートで USD 換算する。
    """
    from broker_config import BROKER_COSTS

    cfg = BROKER_COSTS.get(broker, BROKER_COSTS.get('oanda', {}))
    commission_pips = 0.0

    if cfg.get('use_dynamic_commission'):
        info = mt5.symbol_info(symbol)
        if info is not None:
            tick_val  = info.trade_tick_value  # 1tick分の損益（口座通貨建て、JPY口座ならJPY）
            tick_size = info.trade_tick_size
            commission_per_lot_usd = cfg['commission_usd_per_lot']

            if tick_val > 0 and tick_size > 0:
                pip_size = tick_size * 10  # 5桁表示の場合 pip = tick * 10
                # pip_value_per_lot [口座通貨/lot]
                pip_value_per_lot = tick_val / tick_size * pip_size

                # 口座通貨がJPYの場合、pip_value_per_lot はJPY建て → USD換算
                usdjpy_tick = mt5.symbol_info_tick('USDJPY')
                if usdjpy_tick is None:
                    # OANDA では USDJPY.cl 等のsuffixが付く場合がある
                    for sym in mt5.symbols_get() or []:
                        if sym.name.startswith('USDJPY'):
                            usdjpy_tick = mt5.symbol_info_tick(sym.name)
                            break
                if usdjpy_tick and usdjpy_tick.bid > 0:
                    pip_value_usd = pip_value_per_lot / usdjpy_tick.bid
                else:
                    # USD/JPY レート取得失敗時は145と仮定
                    pip_value_usd = pip_value_per_lot / 145.0

                if pip_value_usd > 0:
                    commission_pips = (commission_per_lot_usd * 2.0) / pip_value_usd
        else:
            # symbol_info取得失敗: フォールバック固定計算（USDJPY=145想定）
            commission_per_lot_usd = cfg['commission_usd_per_lot']
            if 'JPY' in symbol:
                commission_pips = (commission_per_lot_usd * 2.0) / (10.0 * 145.0 / 100.0)
            else:
                commission_pips = (commission_per_lot_usd * 2.0) / 10.0

    spread_pips = cfg.get('spread_pips', 0.0)
    total = commission_pips + spread_pips
    return round(total, 2)
