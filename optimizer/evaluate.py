"""
evaluate.py - Phase1評価器
MT5 Python APIでトレード履歴を取得してmetrics.jsonを生成する

使い方（デフォルト: MT5 API自動取得）:
  python evaluate.py [--magic INT] [--days INT] [--out PATH]

フォールバック（MT5未起動環境）:
  python evaluate.py --csv history.csv [--magic INT]
"""

import argparse
import json
import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# MT5 Python API
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ------------------------------------------------------------------ #
#  定数
# ------------------------------------------------------------------ #
DEFAULT_OUT         = Path(__file__).parent / 'metrics.json'
DEFAULT_CSV         = Path(__file__).parent / 'history.csv'
CURRENT_PARAMS_PATH = Path(__file__).parent / 'candidates.json'
DEFAULT_DAYS        = 90   # 取得対象期間（日）

# MT5 CSVヘッダー候補（バージョン差吸収）
COL_MAP = {
    'ticket':     ['Ticket', 'ticket', '#'],
    'open_time':  ['Open Time', 'open_time', 'Time'],
    'close_time': ['Close Time', 'close_time'],
    'type':       ['Type', 'type', 'Direction'],
    'lots':       ['Lots', 'lots', 'Volume', 'Size'],
    'symbol':     ['Symbol', 'symbol', 'Instrument'],
    'open_price': ['Open Price', 'open_price', 'Price'],
    'sl':         ['S/L', 'sl', 'Stop Loss'],
    'tp':         ['T/P', 'tp', 'Take Profit'],
    'close_price':['Close Price', 'close_price'],
    'profit':     ['Profit', 'profit', 'P&L', 'Net P&L'],
    'magic':      ['Magic', 'magic', 'Magic Number'],
    'comment':    ['Comment', 'comment'],
}


# ------------------------------------------------------------------ #
#  MT5 Python API経由で直接取得（メイン取得経路）
# ------------------------------------------------------------------ #
def fetch_from_mt5(
    magic: int | None,
    days: int,
    save_csv: Path | None = None,
) -> list[dict]:
    """
    MT5 Python APIでクローズ済みポジションを取得する。

    手順:
      1. history_deals_get() で全deal取得
      2. DEAL_ENTRY_IN  -> position_id をキーにエントリー情報を収集
      3. DEAL_ENTRY_OUT -> クローズ情報を収集
      4. position_id で突合してopen_time/open_price/SL/TPを補完
      5. history_orders_get() でSL/TPを補完（dealにない場合）
    """
    if not MT5_AVAILABLE:
        raise RuntimeError(
            'MetaTrader5パッケージが未インストールです。\n'
            '  pip install MetaTrader5\n'
            'MT5が起動していないVPSでは --csv オプションを使用してください。'
        )

    if not mt5.initialize():
        err = mt5.last_error()
        raise RuntimeError(
            f'MT5初期化失敗: {err}\n'
            'MT5ターミナルが起動しているか確認してください。'
        )

    try:
        from_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
        to_dt   = datetime.now(tz=timezone.utc)

        print(f'[MT5] 取得期間: {from_dt.strftime("%Y-%m-%d")} ~ {to_dt.strftime("%Y-%m-%d")}')

        # --- deals取得 ---
        deals = mt5.history_deals_get(from_dt, to_dt)
        if deals is None:
            raise RuntimeError(f'history_deals_get失敗: {mt5.last_error()}')

        print(f'[MT5] 総deal数: {len(deals)}')

        # --- エントリーdeal収集（position_id -> IN deal） ---
        entry_map: dict[int, object] = {}
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_IN:
                entry_map[d.position_id] = d

        # --- orders取得（SL/TP補完用） ---
        orders = mt5.history_orders_get(from_dt, to_dt)
        order_map: dict[int, object] = {}
        if orders:
            for o in orders:
                if o.position_id not in order_map:
                    order_map[o.position_id] = o

        # --- クローズdealを処理 ---
        rows = []
        for d in deals:
            if d.entry != mt5.DEAL_ENTRY_OUT:
                continue

            if magic is not None and d.magic != magic:
                continue

            pos_id = d.position_id

            # エントリー情報補完
            entry_d = entry_map.get(pos_id)
            open_time  = (
                datetime.fromtimestamp(entry_d.time, tz=timezone.utc)
                        .strftime('%Y.%m.%d %H:%M:%S')
                if entry_d else ''
            )
            open_price = entry_d.price if entry_d else d.price

            # ポジションの向き:
            #   DEAL_ENTRY_IN の type で判定（BUY=0 はロング）
            #   エントリーdealがない場合はクローズdealのtypeの逆転
            if entry_d:
                is_buy = (entry_d.type == mt5.DEAL_TYPE_BUY)
            else:
                is_buy = (d.type == mt5.DEAL_TYPE_SELL)

            # SL/TP補完: order_mapから取得
            order = order_map.get(pos_id)
            sl = order.sl if order else 0.0
            tp = order.tp if order else 0.0

            close_time = datetime.fromtimestamp(
                d.time, tz=timezone.utc
            ).strftime('%Y.%m.%d %H:%M:%S')

            rows.append({
                'ticket':      str(pos_id),
                'open_time':   open_time,
                'close_time':  close_time,
                'type':        'buy' if is_buy else 'sell',
                'lots':        float(d.volume),
                'symbol':      d.symbol,
                'open_price':  float(open_price),
                'sl':          float(sl),
                'tp':          float(tp),
                'close_price': float(d.price),
                'profit':      float(d.profit),
                'magic':       str(d.magic),
                'comment':     d.comment,
            })

        print(f'[MT5] クローズ済みポジション: {len(rows)}件取得')

        if rows and save_csv:
            _save_rows_to_csv(rows, save_csv)
            print(f'[MT5] バックアップCSV保存: {save_csv}')

        return rows

    finally:
        mt5.shutdown()


def _save_rows_to_csv(rows: list[dict], path: Path):
    if not rows:
        print(f'[ERROR] _save_rows_to_csv: 保存対象データが空です ({path})')
        return
    try:
        fieldnames = list(rows[0].keys())
        str_rows = [{k: str(v) for k, v in r.items()} for r in rows]
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(str_rows)
    except (KeyError, ValueError) as e:
        print(f'[ERROR] _save_rows_to_csv: CSV形式エラー: {e} ({path})')
        raise


# ------------------------------------------------------------------ #
#  CSVパーサー（フォールバック用）
# ------------------------------------------------------------------ #
def _resolve_col(header: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in header:
            return c
    return None


def parse_csv(path: Path, magic: int | None) -> list[dict]:
    trades = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader_lines = list(f)

    header_idx = None
    for i, line in enumerate(reader_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if any(k in stripped for k in ('Ticket', 'ticket', 'Open Time', 'Profit')):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError('CSVのヘッダー行が見つかりません。MT5エクスポート形式を確認してください。')

    csv_data = ''.join(reader_lines[header_idx:])
    reader   = csv.DictReader(io.StringIO(csv_data))
    header   = reader.fieldnames or []

    cols    = {k: _resolve_col(header, v) for k, v in COL_MAP.items()}
    missing = [k for k, v in cols.items() if v is None and k in ('profit', 'close_time')]
    if missing:
        raise ValueError(f'必須列が見つかりません: {missing}\n検出ヘッダー: {header}')

    for row in reader:
        symbol_val = row.get(cols.get('symbol') or '', '').strip()
        if not symbol_val or symbol_val.lower() in ('balance', 'credit', ''):
            continue

        if magic is not None:
            magic_col = cols.get('magic')
            if magic_col and row.get(magic_col, '').strip():
                try:
                    if int(float(row[magic_col])) != magic:
                        continue
                except ValueError:
                    pass

        def g(key: str, default: str = '0') -> str:
            col = cols.get(key)
            return row.get(col, default).strip() if col else default

        try:
            profit = float(g('profit', '0').replace(',', ''))
        except ValueError:
            continue

        try:
            lots = float(g('lots', '0.01').replace(',', ''))
        except ValueError:
            lots = 0.01

        trades.append({
            'ticket':      g('ticket'),
            'symbol':      symbol_val,
            'type':        g('type').lower(),
            'lots':        lots,
            'open_price':  _safe_float(g('open_price')),
            'close_price': _safe_float(g('close_price')),
            'sl':          _safe_float(g('sl')),
            'tp':          _safe_float(g('tp')),
            'profit':      profit,
            'open_time':   g('open_time'),
            'close_time':  g('close_time'),
            'magic':       g('magic'),
            'comment':     g('comment'),
        })

    return trades


def _safe_float(s: str) -> float:
    try:
        return float(s.replace(',', ''))
    except (ValueError, AttributeError):
        return 0.0


# ------------------------------------------------------------------ #
#  メトリクス計算
# ------------------------------------------------------------------ #
def calc_metrics(trades: list[dict]) -> dict:
    if not trades:
        return _zero_metrics()

    profits  = [t['profit'] for t in trades]
    wins     = [p for p in profits if p > 0]
    losses   = [p for p in profits if p < 0]

    total_trades = len(profits)
    win_count    = len(wins)
    loss_count   = len(losses)

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    pf           = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    win_rate = win_count / total_trades if total_trades > 0 else 0.0

    avg_win   = gross_profit / win_count  if win_count  > 0 else 0.0
    avg_loss  = gross_loss   / loss_count if loss_count > 0 else 0.0
    rr_actual = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    breakeven_winrate = (1.0 / (1.0 + rr_actual)) if rr_actual > 0 else 0.5

    tp_reach_count = 0
    tp_eligible    = 0
    for t in trades:
        tp  = t['tp']
        cp  = t['close_price']
        typ = t['type']
        if tp == 0:
            continue
        tp_eligible += 1
        if typ in ('buy', 'b', '0'):
            if cp >= tp * 0.9999:
                tp_reach_count += 1
        else:
            if cp <= tp * 1.0001:
                tp_reach_count += 1

    tp_reach_rate = (tp_reach_count / tp_eligible) if tp_eligible > 0 else 0.0

    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for p in profits:
        equity += p
        if equity > peak:
            peak = equity
        dd = (equity - peak) / abs(peak) if peak != 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    total_profit = sum(profits)

    return {
        'pf':               round(pf, 4),
        'win_rate':         round(win_rate, 4),
        'rr_actual':        round(rr_actual, 4),
        'tp_reach_rate':    round(tp_reach_rate, 4),
        'max_dd':           round(max_dd, 4),
        'breakeven_winrate':round(breakeven_winrate, 4),
        'total_profit':     round(total_profit, 2),
        '_total_trades':    total_trades,
        '_win_count':       win_count,
        '_loss_count':      loss_count,
        '_gross_profit':    round(gross_profit, 2),
        '_gross_loss':      round(gross_loss, 2),
    }


def _zero_metrics() -> dict:
    return {
        'pf': 0.0, 'win_rate': 0.0, 'rr_actual': 0.0,
        'tp_reach_rate': 0.0, 'max_dd': 0.0,
        'breakeven_winrate': 0.0, 'total_profit': 0.0,
        '_total_trades': 0, '_win_count': 0, '_loss_count': 0,
        '_gross_profit': 0.0, '_gross_loss': 0.0,
    }


# ------------------------------------------------------------------ #
#  current_params取得
# ------------------------------------------------------------------ #
def load_current_params(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        candidates = data if isinstance(data, list) else data.get('candidates', [])
        if candidates:
            return candidates[0].get('parameters', candidates[0])
        return {}
    except Exception:
        return {}


# ------------------------------------------------------------------ #
#  メイン
# ------------------------------------------------------------------ #
def main():
    parser = argparse.ArgumentParser(
        description='MT5履歴 -> metrics.json  (デフォルト: MT5 API自動取得)',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--magic',  type=int,  default=None,
                        help='magic番号フィルター (省略時: 全件)')
    parser.add_argument('--days',   type=int,  default=DEFAULT_DAYS,
                        help=f'取得対象の過去日数 (default: {DEFAULT_DAYS})')
    parser.add_argument('--out',    type=Path, default=DEFAULT_OUT,
                        help='出力先JSONパス (default: metrics.json)')
    parser.add_argument('--params', type=Path, default=CURRENT_PARAMS_PATH,
                        help='current_params参照先 (default: candidates.json)')
    parser.add_argument('--csv',    type=Path, default=None,
                        help='CSVファイルから読み込む場合に指定\n'
                             '（MT5未起動環境用フォールバック）')
    args = parser.parse_args()

    # --- トレード取得 ---
    if args.csv:
        if not args.csv.exists():
            print(f'[ERROR] CSVが見つかりません: {args.csv}')
            sys.exit(1)
        print(f'[INFO] CSVから読み込み: {args.csv}')
        trades = parse_csv(args.csv, args.magic)
    else:
        print('[INFO] MT5 API経由で取得中...')
        try:
            trades = fetch_from_mt5(args.magic, args.days, save_csv=DEFAULT_CSV)
        except RuntimeError as e:
            print(f'[ERROR] {e}')
            print()
            print('フォールバック: 手動CSVを使う場合は')
            print('  python evaluate.py --csv history.csv [--magic MAGIC]')
            sys.exit(1)

    print(f'[INFO] 対象トレード数: {len(trades)}')
    if not trades:
        print('[WARN] トレードが0件です。magic番号や期間（--days）を確認してください。')

    metrics        = calc_metrics(trades)
    current_params = load_current_params(args.params)

    output = {
        'metrics':        {k: v for k, v in metrics.items() if not k.startswith('_')},
        'current_params': current_params,
        '_debug':         {k: v for k, v in metrics.items() if k.startswith('_')},
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f'[INFO] metrics.json出力: {args.out}')
    print()
    print('=== 結果サマリー ===')
    m = output['metrics']
    d = output['_debug']
    print(f"  トレード数  : {d['_total_trades']} (勝:{d['_win_count']} 負:{d['_loss_count']})")
    print(f"  PF          : {m['pf']:.3f}")
    print(f"  勝率        : {m['win_rate']*100:.1f}%  (損益分岐: {m['breakeven_winrate']*100:.1f}%)")
    print(f"  実RR        : {m['rr_actual']:.3f}")
    print(f"  TP到達率    : {m['tp_reach_rate']*100:.1f}%")
    print(f"  最大DD      : {m['max_dd']*100:.2f}%")
    print(f"  合計損益    : {m['total_profit']:.2f}")


if __name__ == '__main__':
    main()