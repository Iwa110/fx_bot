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


# 取引コスト設定（単位: pips、往復ラウンドトリップ）
# commission_pips: 手数料のpips換算（動的取得優先、失敗時フォールバック用）
# spread_pips: 平均スプレッド（変動制のため保守的な推定値）
BROKER_COSTS: dict[str, dict] = {
    'axiory': {
        'commission_usd_per_lot': 6.0,  # テラ口座固定
        'spread_pips': 0.3,             # 主要ペア平均（保守推定）
        'use_dynamic_commission': True,  # MT5から動的取得を優先
    },
    'exness': {
        'commission_usd_per_lot': 0.2,  # ゼロ口座最低値（ペアにより変動）
        'spread_pips': 0.0,             # ゼロ口座はほぼ0
        'use_dynamic_commission': True,
    },
    'oanda': {
        'commission_usd_per_lot': 0.0,
        'spread_pips': 1.5,
        'use_dynamic_commission': False,
    },
    'oanda_live': {
        'commission_usd_per_lot': 0.0,
        'spread_pips': 1.5,
        'use_dynamic_commission': False,
    },
    'oanda_demo': {
        'commission_usd_per_lot': 0.0,
        'spread_pips': 1.5,
        'use_dynamic_commission': False,
    },
}

BROKERS: dict[str, dict[str, Any]] = {
    'oanda': {
        'path':           r'C:\Program Files\OANDA MetaTrader 5\terminal64.exe',
        'server':         _ENV.get('OANDA_SERVER', 'OANDA-Japan MT5 Demo'),
        'login':          _int('OANDA_LOGIN'),
        'password':       _ENV.get('OANDA_PASSWORD', ''),
        'symbol_suffix':  '.cl',   # 裁量プランの場合。スタンダードは '.oj1m'
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        False,   # demo. 2026-06-21 退役: 実口座は oanda_live を使用
        'enabled':        False,   # RETIRED demo (default-path terminal re-logged to LIVE).
                                   # path_only login-check would refuse this key anyway, but
                                   # disable explicitly so no --broker oanda process connects.
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
    'oanda_live': {
        # [REAL-MONEY / 実口座] OANDA証券 (domestic JFSA, 申告分離20.315%, MT5).
        # Go-live 2026-06-21: 4-pair correlated-cross Grid basket, S0 (risk_frac=0.5).
        # SETUP (user) - single terminal at the DEFAULT path (OANDA demo retired,
        # so no second terminal needed; path_only login-check guards the old 'oanda'
        # demo key against ever trading on this live login):
        #   1) Log the EXISTING default-path OANDA MT5 terminal into the LIVE account
        #      (File > Login to Trade Account). No separate install / no delete needed.
        #   2) Put live credentials in .env: OANDA_LIVE_LOGIN / OANDA_LIVE_PASSWORD /
        #      OANDA_LIVE_SERVER (live server name, e.g. 'OANDA-Japan MT5 Live').
        #   3) Confirm no scheduled task launches '--broker oanda' (retired demo).
        #   4) Flip 'enabled' to True.
        # CONNECTION MODE = credentials (NOT path_only). Diagnosed 2026-06-24:
        # path_only initialize(path) -> (-6) Authorization failed on OANDA live, but
        # initialize(path, login, password, server) -> OK with terminal.trade_allowed=True
        # (the old "creds -> trade_allowed=False" issue does NOT reproduce on live).
        # connect_mt5 bottom branch passes path+login+password+server.
        # symbol_suffix='' : OANDA live uses plain names (AUDCAD/CADCHF/AUDNZD/EURGBP).
        # All 4 grid pairs confirmed available; account REAL, balance 500k, lev 1:25.
        'path':           r'C:\Program Files\OANDA MetaTrader 5\terminal64.exe',
        'server':         _ENV.get('OANDA_LIVE_SERVER', 'OANDA-Japan MT5 Live'),
        'login':          _int('OANDA_LIVE_LOGIN'),
        'password':       _ENV.get('OANDA_LIVE_PASSWORD', ''),
        'symbol_suffix':  '',
        'timezone':       'GMT+2/+3',
        'min_lot':        0.01,
        'is_live':        True,
        'enabled':        True,    # LIVE since 2026-06-24 go-live (.env creds set, tasks cleaned)
        # path_only NOT set -> connect_mt5 uses the credentials branch (verified working).
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
