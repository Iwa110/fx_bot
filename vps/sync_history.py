"""
sync_history.py  v1 - MT5取引履歴をhistory.csvに追記してgit push

Usage:
    python sync_history.py [--days 14] [--no-push]
    python sync_history.py --broker axiory [--days 14]

動作フロー:
    1. 有効な全ブローカーに順次接続
    2. 直近N日のクローズ済みポジションを取得
    3. optimizer/history.csv に追記（ticket重複除外）
    4. git pull --rebase -> add -> commit -> push

IPC注意: ブローカー間は mt5.shutdown() で都度切断してから次に接続する。
"""

import sys, os, argparse, subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError as e:
    print(f'[ERROR] 必須パッケージ未インストール: {e}')
    sys.exit(1)

from broker_utils import connect_mt5, disconnect_mt5
from broker_config import BROKERS

# ── パス定数 ──────────────────────────────────────────────────────────
BASE_DIR    = Path(r'C:\Users\Administrator\fx_bot')
HISTORY_CSV = BASE_DIR / 'optimizer' / 'history.csv'
LOG_DIR     = BASE_DIR / 'logs'
JST         = timezone(timedelta(hours=9))
UTC         = timezone.utc

COL_ORDER = [
    'ticket', 'open_time', 'close_time', 'type', 'lots',
    'symbol', 'open_price', 'sl', 'tp', 'close_price',
    'profit', 'magic', 'comment',
]


# ── MT5履歴取得（connect_mt5()済みの状態で呼ぶこと） ──────────────────
def _fetch_history(days: int) -> list[dict]:
    from_dt = datetime.now(tz=UTC) - timedelta(days=days)
    to_dt   = datetime.now(tz=UTC)

    deals = mt5.history_deals_get(from_dt, to_dt)
    if deals is None:
        print(f'  [WARN] history_deals_get失敗: {mt5.last_error()}')
        return []

    # position_id -> エントリーdeal
    entry_map: dict[int, object] = {}
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_IN:
            entry_map[d.position_id] = d

    # position_id -> 最初のorder (SL/TP取得用)
    orders = mt5.history_orders_get(from_dt, to_dt)
    order_map: dict[int, object] = {}
    if orders:
        for o in orders:
            if o.position_id not in order_map:
                order_map[o.position_id] = o

    rows = []
    for d in deals:
        if d.entry != mt5.DEAL_ENTRY_OUT:
            continue

        pos_id  = d.position_id
        entry_d = entry_map.get(pos_id)
        order   = order_map.get(pos_id)

        open_time  = (datetime.fromtimestamp(entry_d.time, tz=UTC)
                      .strftime('%Y.%m.%d %H:%M:%S') if entry_d else '')
        open_price = entry_d.price if entry_d else d.price

        if entry_d:
            is_buy = (entry_d.type == mt5.DEAL_TYPE_BUY)
        else:
            is_buy = (d.type == mt5.DEAL_TYPE_SELL)

        rows.append({
            'ticket':      int(pos_id),
            'open_time':   open_time,
            'close_time':  datetime.fromtimestamp(d.time, tz=UTC)
                           .strftime('%Y.%m.%d %H:%M:%S'),
            'type':        'buy' if is_buy else 'sell',
            'lots':        float(d.volume),
            'symbol':      d.symbol,
            'open_price':  float(open_price),
            'sl':          float(order.sl) if order else 0.0,
            'tp':          float(order.tp) if order else 0.0,
            'close_price': float(d.price),
            'profit':      float(d.profit),
            'magic':       int(d.magic),
            'comment':     d.comment,
        })

    return rows


# ── 全ブローカー収集 ──────────────────────────────────────────────────
def collect_all(days: int, only_broker: str | None = None) -> pd.DataFrame:
    all_rows: list[dict] = []

    targets = (
        {only_broker: BROKERS[only_broker]}
        if only_broker
        else BROKERS
    )

    for broker, cfg in targets.items():
        if not cfg.get('enabled', False):
            print(f'[{broker}] SKIP (disabled)')
            continue

        print(f'[{broker}] 接続中...')
        if not connect_mt5(broker):
            print(f'[{broker}] ERROR: MT5接続失敗')
            continue

        try:
            rows = _fetch_history(days)
            print(f'[{broker}] {len(rows)}件取得')
            all_rows.extend(rows)
        except Exception as e:
            print(f'[{broker}] ERROR: {e}')
        finally:
            disconnect_mt5()

    if not all_rows:
        return pd.DataFrame(columns=COL_ORDER)

    return pd.DataFrame(all_rows, columns=COL_ORDER)


# ── history.csv マージ ────────────────────────────────────────────────
def merge_with_csv(df_new: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """既存CSVとマージ。重複ticketはdf_newを優先。追加件数を返す。"""
    if HISTORY_CSV.exists() and HISTORY_CSV.stat().st_size > 0:
        df_old = pd.read_csv(HISTORY_CSV,
                             dtype={'ticket': 'Int64', 'magic': 'Int64'})
        before = len(df_old.drop_duplicates('ticket'))
        df_merged = pd.concat([df_old, df_new], ignore_index=True)
    else:
        before = 0
        df_merged = df_new.copy()

    df_merged['ticket'] = df_merged['ticket'].astype('Int64')
    df_merged = (df_merged
                 .drop_duplicates(subset='ticket', keep='last')
                 .sort_values('close_time')
                 .reset_index(drop=True))

    added = len(df_merged) - before
    return df_merged, added


# ── git push ──────────────────────────────────────────────────────────
def git_push(added: int) -> bool:
    repo  = str(BASE_DIR)
    today = datetime.now(JST).strftime('%Y-%m-%d')
    cmds  = [
        ['git', '-C', repo, 'pull', '--rebase'],
        ['git', '-C', repo, 'add', 'optimizer/history.csv'],
        ['git', '-C', repo, 'commit', '-m',
         f'data: sync history {today} (+{added} trades)'],
        ['git', '-C', repo, 'push'],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        merged_out = r.stdout + r.stderr
        if r.returncode != 0 and 'nothing to commit' not in merged_out:
            print(f'[git] WARN {cmd[2]}: {r.stderr.strip()}')
            # pull失敗は致命的、それ以外は続行
            if cmd[2] == 'pull':
                return False
        else:
            action = ' '.join(cmd[2:4])
            print(f'[git] OK  {action}')
    return True


# ── main ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='MT5履歴をhistory.csvに同期')
    ap.add_argument('--broker', help='単一ブローカー指定（省略時は全有効ブローカー）')
    ap.add_argument('--days',   type=int, default=14,
                    help='取得日数 (default: 14)')
    ap.add_argument('--no-push', action='store_true',
                    help='git pushをスキップ（ローカル確認用）')
    args = ap.parse_args()

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'=== sync_history.py  {now_jst} JST  days={args.days} ===')

    if args.broker and args.broker not in BROKERS:
        print(f'[ERROR] 不明なブローカー: {args.broker}')
        sys.exit(1)

    # データ収集
    df_new = collect_all(args.days, only_broker=args.broker)

    if df_new.empty:
        print('[INFO] 新規データなし。終了。')
        return

    # マージ & 保存
    df_merged, added = merge_with_csv(df_new)
    df_merged.to_csv(HISTORY_CSV, index=False)
    print(f'\n[OK] history.csv: {len(df_merged)}件 (新規 +{added}件)')

    # git push
    if args.no_push:
        print('[INFO] --no-push: git pushスキップ')
    else:
        git_push(added)

    print('=== 完了 ===')


if __name__ == '__main__':
    main()
