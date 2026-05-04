"""
check_fx_trading_data.py
Phase1完了判定用 実稼働データ確認スクリプト
- magic=20250001 (BB戦略) の取引履歴を集計
- MT5直接取得 → 失敗時はhistory.csvにフォールバック
- VPS(Windows Server 2022)で直接実行可能
"""

import sys, os, csv, re
from datetime import datetime, timedelta, timezone
from collections import defaultdict

MAGIC_BB   = 20250001
HISTORY_CSV_CANDIDATES = [
    r'C:\Users\Administrator\fx_bot\optimizer\history.csv',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', 'optimizer', 'history.csv'),
]
BB_LOG_CANDIDATES = [
    r'C:\Users\Administrator\fx_bot\vps\bb_log.txt',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bb_log.txt'),
]
PHASE1_PAIRS = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']
PF_THRESH   = 1.2
WR_THRESH   = 0.50

# ── MT5取得 ──────────────────────────────────────────────────
def fetch_from_mt5(days=180):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print('[MT5] MetaTrader5パッケージ未インストール')
        return None

    if not mt5.initialize():
        print(f'[MT5] 初期化失敗: {mt5.last_error()}')
        return None

    try:
        from_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
        to_dt   = datetime.now(tz=timezone.utc)
        deals   = mt5.history_deals_get(from_dt, to_dt)

        if deals is None or len(deals) == 0:
            print(f'[MT5] deals取得0件 (last_error={mt5.last_error()})')
            print('[MT5] OANDAブローカーはhistory_deals_getが使えないことがあります')
            return None

        print(f'[MT5] deals取得: {len(deals)}件')

        # エントリーdealのマップ
        entry_map = {d.position_id: d
                     for d in deals if d.entry == mt5.DEAL_ENTRY_IN}

        # ordersからSL/TP補完
        orders = mt5.history_orders_get(from_dt, to_dt)
        order_map = {}
        if orders:
            for o in orders:
                if o.position_id not in order_map:
                    order_map[o.position_id] = o

        rows = []
        for d in deals:
            if d.entry != mt5.DEAL_ENTRY_OUT:
                continue
            if d.magic != MAGIC_BB:
                continue

            ed = entry_map.get(d.position_id)
            rows.append({
                'ticket':     str(d.position_id),
                'symbol':     d.symbol,
                'profit':     float(d.profit),
                'close_time': datetime.fromtimestamp(d.time, tz=timezone.utc)
                               .strftime('%Y.%m.%d %H:%M:%S'),
                'open_time':  (datetime.fromtimestamp(ed.time, tz=timezone.utc)
                               .strftime('%Y.%m.%d %H:%M:%S') if ed else ''),
                'magic':      str(d.magic),
            })

        print(f'[MT5] magic={MAGIC_BB} 決済ポジション: {len(rows)}件')
        return rows

    finally:
        mt5.shutdown()


# ── history.csvパース ────────────────────────────────────────
def parse_history_csv():
    path = None
    for candidate in HISTORY_CSV_CANDIDATES:
        p = os.path.normpath(candidate)
        if os.path.exists(p):
            path = p
            break

    if path is None:
        print('[CSV] history.csvが見つかりません')
        print('  確認パス:')
        for c in HISTORY_CSV_CANDIDATES:
            print(f'    {os.path.normpath(c)}')
        return None

    print(f'[CSV] 読み込み: {path}')

    col_aliases = {
        'symbol':     ['symbol', 'Symbol'],
        'profit':     ['profit', 'Profit'],
        'magic':      ['magic',  'Magic'],
        'open_time':  ['open_time',  'Open Time'],
        'close_time': ['close_time', 'Close Time'],
        'ticket':     ['ticket', 'Ticket'],
    }

    rows = []
    with open(path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        def find_col(aliases):
            for a in aliases:
                if a in headers:
                    return a
            return None

        col_map = {k: find_col(v) for k, v in col_aliases.items()}

        for row in reader:
            magic_col = col_map['magic']
            if magic_col:
                try:
                    if int(float(row.get(magic_col, 0))) != MAGIC_BB:
                        continue
                except (ValueError, TypeError):
                    continue

            symbol_col = col_map['symbol']
            profit_col = col_map['profit']
            if not symbol_col or not profit_col:
                continue

            try:
                profit = float(row[profit_col])
            except (ValueError, TypeError):
                continue

            rows.append({
                'ticket':     row.get(col_map['ticket'] or '', ''),
                'symbol':     row.get(symbol_col, ''),
                'profit':     profit,
                'open_time':  row.get(col_map['open_time'] or '', ''),
                'close_time': row.get(col_map['close_time'] or '', ''),
                'magic':      str(MAGIC_BB),
            })

    print(f'[CSV] magic={MAGIC_BB} 件数: {len(rows)}件')
    return rows


# ── bb_log.txtパース（補助） ──────────────────────────────────
def parse_bb_log():
    path = None
    for candidate in BB_LOG_CANDIDATES:
        p = os.path.normpath(candidate)
        if os.path.exists(p):
            path = p
            break

    if path is None:
        return []

    pattern = re.compile(
        r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] '
        r'決済: (\w+) PnL=([+-]?\d+)円 (BB_\w+)'
    )
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue
            ts_str, symbol, profit_str, comment = m.groups()
            rows.append({
                'ticket':     '',
                'symbol':     symbol,
                'profit':     float(profit_str),
                'close_time': ts_str.replace('-', '.').replace(' ', ' '),
                'open_time':  '',
                'magic':      str(MAGIC_BB),
                'source':     'bb_log',
            })

    if rows:
        print(f'[LOG] bb_log.txt 決済行: {len(rows)}件')
    return rows


# ── 統計計算 ──────────────────────────────────────────────────
def calc_stats(trades):
    if not trades:
        return None

    wins   = [t['profit'] for t in trades if t['profit'] > 0]
    losses = [t['profit'] for t in trades if t['profit'] <= 0]

    total_win  = sum(wins)
    total_loss = abs(sum(losses))
    pf         = total_win / total_loss if total_loss > 0 else float('inf')
    wr         = len(wins) / len(trades) if trades else 0

    profits = [t['profit'] for t in trades]
    cumulative = 0
    peak = 0
    max_dd = 0
    for p in profits:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    close_times = []
    for t in trades:
        ct = t.get('close_time', '')
        if ct:
            for fmt in ('%Y.%m.%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                try:
                    close_times.append(datetime.strptime(ct, fmt))
                    break
                except ValueError:
                    continue

    first_date = min(close_times).strftime('%Y-%m-%d') if close_times else '不明'
    last_date  = max(close_times).strftime('%Y-%m-%d') if close_times else '不明'

    return {
        'n':          len(trades),
        'wins':       len(wins),
        'losses':     len(losses),
        'pf':         pf,
        'wr':         wr,
        'total_profit': sum(profits),
        'total_win':  total_win,
        'total_loss': total_loss,
        'max_dd':     max_dd,
        'first_date': first_date,
        'last_date':  last_date,
    }


# ── 表示 ──────────────────────────────────────────────────────
def print_phase1_table(pair_stats, overall_stats):
    sep = '-' * 72

    print()
    print('=' * 72)
    print(f'  Phase1 BB戦略 実稼働データ確認  (magic={MAGIC_BB})')
    print('=' * 72)

    if overall_stats:
        print(f'  期間: {overall_stats["first_date"]} ~ {overall_stats["last_date"]}')
        print(f'  総取引数: {overall_stats["n"]}件')
        print(f'  総損益: {overall_stats["total_profit"]:+,.0f}円')
    print()

    header = f'  {"ペア":<8} {"n":>4}  {"PF":>6}  {"勝率":>6}  {"DD(絶対)":>10}  {"PF判定"}  {"WR判定"}  {"総合"}'
    print(header)
    print(sep)

    for pair in PHASE1_PAIRS:
        s = pair_stats.get(pair)
        if s is None or s['n'] == 0:
            print(f'  {pair:<8} {"データなし":>4}')
            continue

        pf_ok = s['pf'] > PF_THRESH
        wr_ok = s['wr'] > WR_THRESH
        ok    = pf_ok and wr_ok

        pf_mark = 'OK' if pf_ok else 'NG'
        wr_mark = 'OK' if wr_ok else 'NG'
        go_mark = '合格' if ok else '不合格'

        pf_str  = f'{s["pf"]:.3f}' if s["pf"] != float('inf') else '  inf'
        dd_str  = f'{s["max_dd"]:,.0f}円'
        wr_str  = f'{s["wr"]*100:.1f}%'

        print(f'  {pair:<8} {s["n"]:>4}  {pf_str:>6}  {wr_str:>6}  {dd_str:>10}  {pf_mark:<6}  {wr_mark:<6}  {go_mark}')

    print(sep)
    print(f'  判定基準: PF>{PF_THRESH} / 勝率>{WR_THRESH*100:.0f}% / DD<15%')
    print()

    print('  【ペア別詳細】')
    for pair in PHASE1_PAIRS:
        s = pair_stats.get(pair)
        if s is None or s['n'] == 0:
            print(f'  {pair}: データなし')
            continue
        avg_win  = s['total_win']  / s['wins']   if s['wins']   > 0 else 0
        avg_loss = s['total_loss'] / s['losses']  if s['losses']  > 0 else 0
        rr_real  = avg_win / avg_loss if avg_loss > 0 else float('inf')
        print(f'  {pair}: 勝{s["wins"]}回 負{s["losses"]}回 '
              f'| 平均利益={avg_win:,.0f}円 平均損失={avg_loss:,.0f}円 '
              f'| 実RR={rr_real:.2f} '
              f'| 期間={s["first_date"]}~{s["last_date"]}')

    print('=' * 72)


# ── 未収録ペアの一覧 ────────────────────────────────────────────
def print_other_pairs(trades):
    all_symbols = defaultdict(int)
    for t in trades:
        sym = t['symbol']
        if sym not in PHASE1_PAIRS:
            all_symbols[sym] += 1

    if all_symbols:
        print()
        print('  【Phase1対象外ペアの取引件数】')
        for sym, cnt in sorted(all_symbols.items(), key=lambda x: -x[1]):
            print(f'    {sym}: {cnt}件')


# ── メイン ────────────────────────────────────────────────────
def main():
    print('FX実稼働データ確認スクリプト起動...')
    print()

    # 1. MT5から取得を試みる
    trades = fetch_from_mt5(days=180)

    # 2. MT5失敗時はhistory.csvにフォールバック
    if not trades:
        print()
        print('[フォールバック] history.csvから読み込みます...')
        trades = parse_history_csv()

    # 3. 両方失敗時はbb_log.txtから補助取得
    if not trades:
        print()
        print('[フォールバック2] bb_log.txtから読み込みます...')
        trades = parse_bb_log()

    if not trades:
        print()
        print('[ERROR] 取引データが取得できませんでした。')
        print('  確認事項:')
        print('  1. MT5ターミナルが起動しているか')
        print('  2. history.csvのパスが正しいか')
        print(f'     {os.path.normpath(HISTORY_CSV_CANDIDATES[0])}')
        sys.exit(1)

    # 4. ペア別集計
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t['symbol']].append(t)

    pair_stats   = {pair: calc_stats(by_pair[pair]) for pair in PHASE1_PAIRS}
    overall_stats = calc_stats(trades)

    # 5. 表示
    print_phase1_table(pair_stats, overall_stats)
    print_other_pairs(trades)

    # 6. 判定サマリー
    print()
    all_pass = all(
        pair_stats.get(p) and pair_stats[p]['pf'] > PF_THRESH
                           and pair_stats[p]['wr'] > WR_THRESH
        for p in PHASE1_PAIRS
        if pair_stats.get(p) and pair_stats[p]['n'] > 0
    )
    passed   = [p for p in PHASE1_PAIRS
                if pair_stats.get(p) and pair_stats[p]['n'] > 0
                and pair_stats[p]['pf'] > PF_THRESH
                and pair_stats[p]['wr'] > WR_THRESH]
    if all_pass and passed:
        print('  >>> 全ペア合格 - Phase2移行可能 <<<')
    else:
        fail = [p for p in PHASE1_PAIRS
                if pair_stats.get(p) and pair_stats[p]['n'] > 0
                and not (pair_stats[p]['pf'] > PF_THRESH
                         and pair_stats[p]['wr'] > WR_THRESH)]
        if fail:
            print(f'  >>> 未達ペア: {", ".join(fail)} - データ蓄積継続 <<<')
        else:
            print('  >>> データ不足 - 引き続き蓄積中 <<<')
    print()


if __name__ == '__main__':
    main()
