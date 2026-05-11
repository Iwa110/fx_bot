"""
broker_config.py - ブローカー設定の一元管理

login/password は .env ファイルから読み込む。
enabled=False にすれば接続試行をスキップできる。

.env キー一覧:
  OANDA_LOGIN / OANDA_PASSWORD / OANDA_SERVER
  OANDA_DEMO_LOGIN / OANDA_DEMO_PASSWORD / OANDA_DEMO_SERVER
  AXIORY_LOGIN / AXIORY_PASSWORD / AXIORY_SERVER
  EXNESS_LOGIN / EXNESS_PASSWORD / EXNESS_SERVER
"""
import os
from typing import Any

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_BASE_DIR, '.env')


def _load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


_ENV = _load_env(_ENV_PATH)


def _int(key: str, default: int = 0) -> int:
    try:
        return int(_ENV.get(key, str(default)) or str(default))
    except ValueError:
        return default


BROKERS: dict[str, dict[str, Any]] = {
    'oanda': {
        'path':           r'C:\Program Files\OANDA MetaTrader 5\terminal64.exe',
        'server':         _ENV.get('OANDA_SERVER', 'OANDA-Japan MT5 Demo'),
        'login':          _int('OANDA_LOGIN'),
        'password':       _ENV.get('OANDA_PASSWORD', ''),
        'symbol_suffix':  '.cl',   # 裁量プランの場合。スタンダードは '.oj1m'
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        False,   # 現在はデモ口座。実口座開設後に True に変更する
        'enabled':        True,
        'path_only':      True,    # path指定でOANDA端末を特定。credentialsは渡さない
                                   # (credentials渡しでterminal.trade_allowed=Falseになるため)
                                   # attach=Trueは複数端末起動時に別端末に接続してしまう問題があるため不使用
    },
    'oanda_demo': {
        # [実口座未開設のため無効化。実口座開設後に oanda を is_live=True にして
        #  oanda_demo を別デモ口座として設定する]
        'path':           r'C:\Program Files\OANDA MetaTrader 5\terminal64.exe',
        'server':         _ENV.get('OANDA_DEMO_SERVER', 'OANDA-Japan MT5 Demo'),
        'login':          _int('OANDA_DEMO_LOGIN'),
        'password':       _ENV.get('OANDA_DEMO_PASSWORD', ''),
        'symbol_suffix':  '.cl',
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        False,
        'enabled':        False,
    },
    'axiory': {
        'path':           r'C:\Program Files\Axiory MetaTrader 5\terminal64.exe',
        'server':         _ENV.get('AXIORY_SERVER', 'Axiory-Demo'),
        'login':          _int('AXIORY_LOGIN'),
        'password':       _ENV.get('AXIORY_PASSWORD', ''),
        'symbol_suffix':  '',
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        False,
        'enabled':        True,
    },
    'exness': {
        'path':           r'C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe',
        'server':         _ENV.get('EXNESS_SERVER', 'Exness-MT5Trial7'),
        'login':          _int('EXNESS_LOGIN'),
        'password':       _ENV.get('EXNESS_PASSWORD', ''),
        'symbol_suffix':  'm',
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        False,
        'enabled':        True,
    },
}
