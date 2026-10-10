"""
account_snapshot.py - 全有効ブローカーの口座状態を optimizer/account_snapshot.csv に追記

列: date_jst,broker,balance,equity,margin_level
  - margin_level は MT5 account_info().margin_level(%)。margin=0(ノーポジ)時は空欄
    (MT5は0.0を返すため、そのまま書くと daily_gate の維持率<150% を誤発火する)。
  - 口座通貨はブローカー依存(oanda_live/axiory=JPY, exness=USD)。

同期: 本スクリプトは追記のみ(git操作なし)。sync_history.py の git_push が
  history.csv と同じコミットで account_snapshot.csv を add → push する。
  → daily_gate.py(check_margin_snapshot)が LIVE(oanda_live) の検査窓内最小維持率を判定。

Task Scheduler: register_account_snapshot.bat で FX_Account_Snapshot(1時間毎)を登録。

IPC注意: ブローカー間は mt5.shutdown() で都度切断してから次に接続する(sync_history.pyと同様)。
"""

import sys, os, csv
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
except ImportError as e:
    print(f'[ERROR] 必須パッケージ未インストール: {e}')
    sys.exit(1)

from broker_utils import connect_mt5, disconnect_mt5
from broker_config import BROKERS

BASE_DIR     = Path(r'C:\Users\Administrator\fx_bot')
SNAPSHOT_CSV = BASE_DIR / 'optimizer' / 'account_snapshot.csv'
JST          = timezone(timedelta(hours=9))
COLS         = ['date_jst', 'broker', 'balance', 'equity', 'margin_level']


def snapshot(broker: str, ts: str) -> dict | None:
    if not connect_mt5(broker):
        print(f'[{broker}] ERROR: MT5接続失敗')
        return None
    try:
        a = mt5.account_info()
        if a is None:
            print(f'[{broker}] ERROR: account_info失敗: {mt5.last_error()}')
            return None
        lv = f'{a.margin_level:.1f}' if a.margin > 0 else ''
        return {'date_jst': ts, 'broker': broker, 'balance': f'{a.balance:.2f}',
                'equity': f'{a.equity:.2f}', 'margin_level': lv}
    finally:
        disconnect_mt5()


def main():
    ts = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    rows = []
    for broker, cfg in BROKERS.items():
        if not cfg.get('enabled', False):
            continue
        r = snapshot(broker, ts)
        if r:
            print(f"[{broker}] balance={r['balance']} equity={r['equity']} "
                  f"margin_level={r['margin_level'] or '-'}")
            rows.append(r)

    if not rows:
        print('[ERROR] 取得0件')
        sys.exit(1)

    new = not SNAPSHOT_CSV.exists() or SNAPSHOT_CSV.stat().st_size == 0
    with open(SNAPSHOT_CSV, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f'[OK] {SNAPSHOT_CSV.name} +{len(rows)}行 ({ts} JST)')


if __name__ == '__main__':
    main()
