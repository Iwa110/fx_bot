"""
check_broker_connection.py - ブローカー接続確認スクリプト

使用例:
  python check_broker_connection.py              # enabled=True の全ブローカーを確認
  python check_broker_connection.py --broker oanda
  python check_broker_connection.py --broker oanda_demo
  python check_broker_connection.py --discover   # 現在接続中のMT5情報を表示（.env設定用）
  # OANDA live端末を確実に狙って .env値・symbol suffix・4ペア取扱いを確認:
  python check_broker_connection.py --discover --path "C:\\Program Files\\OANDA MetaTrader 5\\terminal64.exe"
  # live接続方式の診断(path_only vs 認証情報渡し / trade_allowed / suffix):
  python check_broker_connection.py --diag-live   # 要 .env OANDA_LIVE_LOGIN/PASSWORD/SERVER
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from broker_config import BROKERS
from broker_utils import connect_mt5, disconnect_mt5, resolve_symbol


def diag_live(broker_key: str = 'oanda_live') -> None:
    """live接続方式の診断: path_only と 認証情報渡し の両方を試し、接続可否・
    trade_allowed・grid 4ペアの suffix を表示する。go-live の接続方式を確定するため。
    .env に {PREFIX}_LOGIN/PASSWORD/SERVER が設定済みであること。"""
    cfg = BROKERS.get(broker_key)
    if cfg is None:
        print(f'[ERROR] 不明なブローカーキー: {broker_key}')
        return
    path     = cfg.get('path', '')
    login    = cfg.get('login', 0)
    password = cfg.get('password', '')
    server   = cfg.get('server', '')
    print('=' * 64)
    print(f'live接続診断: {broker_key}')
    print(f'  path  : {path}')
    print(f'  login : {login}   server: {server}')
    print(f'  (.env から login/password/server を読込。未設定なら 0/空 になります)')
    print('=' * 64)

    def _report(tag: str, ok: bool) -> None:
        print(f'\n--- 方式[{tag}] initialize -> {"OK" if ok else "NG"} ---')
        if not ok:
            print(f'  last_error: {mt5.last_error()}')
            return
        acc  = mt5.account_info()
        term = mt5.terminal_info()
        if acc:
            mode = {0: 'DEMO', 1: 'CONTEST', 2: 'REAL'}.get(acc.trade_mode, '?')
            print(f'  login={acc.login} server={acc.server} trade_mode={acc.trade_mode}({mode}) '
                  f'balance={acc.balance:,.0f} lev=1:{acc.leverage}')
        if term:
            print(f'  terminal.trade_allowed={term.trade_allowed}  '
                  f'(False=自動売買不可=go-live不可。要対策)')
        all_syms = mt5.symbols_get() or []
        for base in GRID_LIVE_SYMBOLS:
            matches = [s.name for s in all_syms if s.name.startswith(base)]
            if matches:
                sufs = sorted({m[len(base):] for m in matches})
                print(f'    {base:8s}: {matches}  suffix={sufs}')
            else:
                print(f'    {base:8s}: [NOT FOUND]')

        # H1 履歴プローブ: grid は regime_short で 1205本(sma1200+5)必要。
        # None=同期されていない / 少数=履歴不足。AUDNZD の data_fetch_failed 切り分け用。
        print('  --- H1 bar probe (regime grid は 1205本必要) ---')
        for base in GRID_LIVE_SYMBOLS:
            sym = base  # OANDA live は suffix 空
            mt5.symbol_select(sym, True)
            def _cnt(n):
                r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, n)
                return 'None' if r is None else str(len(r))
            c10, c1205, cmax = _cnt(10), _cnt(1205), _cnt(100000)
            si = mt5.symbol_info(sym)
            vis = si.visible if si else '?'
            print(f'    {sym:8s}: H1 count(10)={c10}  count(1205)={c1205}  '
                  f'count(max)={cmax}  visible={vis}')
        print('    判定: count(10)=None なら端末未同期(チャート/履歴DL要)。'
              ' count(max)<1205 なら履歴不足=sma1200不可。')

    # 方式A: path_only (grid_monitor の既定方式)
    okA = mt5.initialize(path=path) if path else mt5.initialize()
    _report('A path_only', okA)
    mt5.shutdown()

    # 方式B: 認証情報渡し
    if login:
        okB = mt5.initialize(path=path, login=login, password=password, server=server)
        _report('B with-credentials', okB)
        mt5.shutdown()
    else:
        print('\n--- 方式[B with-credentials] スキップ: .env に OANDA_LIVE_LOGIN 未設定 ---')

    print('\n判定:')
    print('  - trade_allowed=True で接続できた方式を go-live の接続方式に採用。')
    print('  - 両方 trade_allowed=False なら端末側「自動売買を許可」を確認後に再診断。')
    print('  - 4ペアに [NOT FOUND] があれば実口座で取引不可（バスケット見直し）。')


# Grid live basket (oanda_live) - suffix / 取扱い確認用
GRID_LIVE_SYMBOLS = ['AUDCAD', 'CADCHF', 'AUDNZD', 'EURGBP']


def discover_current(path: str | None = None) -> None:
    """既に起動・ログイン済みのMT5ターミナルに接続して情報を表示する。
    .env に設定すべき値・symbol suffix を確認するために使う。
    path 指定時はそのターミナルを確実に狙う（複数端末起動時の曖昧さ回避）。"""
    print('=' * 60)
    print('現在のMT5ターミナル情報（--discover モード）')
    if path:
        print(f'対象ターミナル（--path）: {path}')
    else:
        print('ターミナルは既に起動・ログイン済みである必要があります。')
        print('（複数端末起動時は --path で対象を明示してください）')
    print()

    ok = mt5.initialize(path=path) if path else mt5.initialize()
    if not ok:
        print(f'接続失敗: {mt5.last_error()}')
        print('MT5ターミナルが起動していないか、接続できません。')
        return

    account = mt5.account_info()
    terminal = mt5.terminal_info()

    if account:
        mode_txt = {0: 'DEMO', 1: 'CONTEST', 2: 'REAL(実口座)'}.get(account.trade_mode, '?')
        print('--- account_info ---')
        print(f'  login   : {account.login}   ← .env の XXXX_LOGIN に設定')
        print(f'  server  : {account.server}  ← .env の XXXX_SERVER に設定')
        print(f'  company : {account.company}')
        print(f'  balance : {account.balance:,.0f}')
        print(f'  currency: {account.currency}')
        print(f'  leverage: 1:{account.leverage}')
        print(f'  trade_mode: {account.trade_mode}  -> {mode_txt}  '
              '(実口座なら必ず REAL=2 を確認)')
    else:
        print('account_info 取得失敗')

    if terminal:
        print()
        print('--- terminal_info ---')
        print(f'  path    : {terminal.path}  ← broker_config.py の path に設定')
        print(f'  data_path: {terminal.data_path}')

    # Grid live basket: 取扱い有無 + suffix を確認
    print()
    print('--- grid live basket symbols (oanda_live suffix 確認) ---')
    all_syms = mt5.symbols_get() or []
    for base in GRID_LIVE_SYMBOLS:
        matches = [s.name for s in all_syms if s.name.startswith(base)]
        if matches:
            suffixes = sorted({m[len(base):] for m in matches})
            print(f'  {base:8s}: {matches}   suffix候補={suffixes}')
        else:
            print(f'  {base:8s}: [NOT FOUND] OANDAがこのペアを扱っていない可能性=要対応')
    print('  -> 4ペアで suffix が揃っているか確認し broker_config.py oanda_live の')
    print("     symbol_suffix に設定（例 '.cl' / '.oj1m' / 空文字 ''）。")
    print('     NOT FOUND があれば、そのペアは実口座で取引不可（運用見直し）。')

    mt5.shutdown()
    print()
    print('.env に追記する例:')
    if account and terminal:
        prefix = 'OANDA_LIVE' if account.trade_mode == 2 else 'OANDA_DEMO'
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
        help='現在起動中のMT5ターミナルに接続して .env 設定値・symbol suffix を表示する',
    )
    parser.add_argument(
        '--path',
        default=None,
        help='--discover で接続するMT5ターミナルのパス（複数端末起動時に対象を明示）',
    )
    parser.add_argument(
        '--diag-live',
        action='store_true',
        help='live接続診断: path_only と 認証情報渡し を両方試し可否/trade_allowed/suffixを表示',
    )
    args = parser.parse_args()

    if args.diag_live:
        diag_live()
        return

    if args.discover:
        discover_current(args.path)
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
