"""
phase1_judgment.py - Phase1完了判定スクリプト
BB/SMC/stat_arb戦略をmagic別・ペア別に集計してPhase1完了条件を判定する

使い方:
  python phase1_judgment.py [--csv history.csv] [--days 90] [--balance 500000]

  --balance: 口座残高(JPY換算)を指定するとDD%判定が有効になる
             省略時はDD絶対額のみ表示（%判定スキップ）

フォールバック（MT5未起動環境）:
  python phase1_judgment.py --csv history.csv
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import parse_csv, fetch_from_mt5

# ------------------------------------------------------------------ #
#  定数
# ------------------------------------------------------------------ #
BB_MAGIC   = 20250001
SMC_MAGIC  = 20260002
SARB_MAGIC = 20260001

# Phase1判定条件
CRIT_AVG_PF = 1.2   # BB アクティブペア 平均PF
CRIT_MIN_PF = 1.0   # 全ペア 足切りライン
CRIT_WIN    = 0.50  # 全ペア 勝率
CRIT_DD     = 0.15  # 全ペア 最大DD（口座残高比、--balance指定時のみ判定）

# BB稼働対象ペア（USDCAD等は除外）
BB_ACTIVE_PAIRS  = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']
BB_EXCLUDE_PAIRS = ['USDCAD']

# stat_arb: 2シンボルの合算でペア評価
SARB_PAIR_DEFS = [
    ('GBPJPY-USDJPY', ['GBPJPY', 'USDJPY']),
    ('EURUSD-GBPUSD', ['EURUSD', 'GBPUSD']),
]

DEFAULT_DAYS = 90
DEFAULT_CSV  = Path(__file__).parent / 'history.csv'


# ------------------------------------------------------------------ #
#  メトリクス計算
# ------------------------------------------------------------------ #
def calc_metrics(trades: list[dict], balance: float | None = None) -> dict | None:
    if not trades:
        return None

    profits  = [t['profit'] for t in trades]
    wins     = [p for p in profits if p > 0]
    losses   = [p for p in profits if p < 0]

    total    = len(profits)
    win_cnt  = len(wins)
    loss_cnt = len(losses)

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    pf = (
        gross_profit / gross_loss if gross_loss > 0
        else (999.0 if gross_profit > 0 else 0.0)
    )
    win_rate = win_cnt / total if total > 0 else 0.0

    # 最大DD: ピーク→トラフの絶対額（口座残高なし環境向け）
    equity  = 0.0
    peak    = 0.0
    max_dd_abs = 0.0
    for p in profits:
        equity += p
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs

    # DD%は口座残高が与えられた場合のみ計算
    max_dd_pct = (max_dd_abs / balance) if (balance and balance > 0) else None

    return {
        'n':          total,
        'win':        win_cnt,
        'loss':       loss_cnt,
        'pf':         round(pf, 3),
        'win_rate':   round(win_rate, 4),
        'max_dd_abs': round(max_dd_abs, 0),
        'max_dd_pct': round(max_dd_pct, 4) if max_dd_pct is not None else None,
    }


# ------------------------------------------------------------------ #
#  トレード分類
# ------------------------------------------------------------------ #
def group_trades(trades: list[dict]) -> dict:
    """
    Returns:
      {
        'BB':   { 'GBPJPY': [...], ... },
        'SMC':  { 'GBPAUD': [...], ... },
        'SARB': { 'GBPJPY-USDJPY': [...], ... },
      }
    """
    bb_raw   = defaultdict(list)
    smc_raw  = defaultdict(list)
    sarb_raw = defaultdict(list)

    for t in trades:
        try:
            magic = int(float(t.get('magic', 0)))
        except (ValueError, TypeError):
            magic = 0

        symbol = t.get('symbol', '').strip().upper()

        if magic == BB_MAGIC:
            if symbol not in BB_EXCLUDE_PAIRS:
                bb_raw[symbol].append(t)
        elif magic == SMC_MAGIC:
            smc_raw[symbol].append(t)
        elif magic == SARB_MAGIC:
            sarb_raw[symbol].append(t)

    # stat_arb: 定義済みペアを合算
    sarb_grouped = {}
    defined_syms = {s for _, syms in SARB_PAIR_DEFS for s in syms}
    for pair_label, symbols in SARB_PAIR_DEFS:
        combined = []
        for sym in symbols:
            combined.extend(sarb_raw.get(sym, []))
        if combined:
            sarb_grouped[pair_label] = combined

    # 定義外シンボルは個別表示
    for sym, ts in sarb_raw.items():
        if sym not in defined_syms:
            sarb_grouped[f'SARB:{sym}'] = ts

    return {
        'BB':   dict(bb_raw),
        'SMC':  dict(smc_raw),
        'SARB': sarb_grouped,
    }


# ------------------------------------------------------------------ #
#  判定ロジック
# ------------------------------------------------------------------ #
def judge_pair(m: dict) -> dict:
    pf_cut = m['pf'] >= CRIT_MIN_PF
    wr_ok  = m['win_rate'] >= CRIT_WIN
    # DD%が計算できない場合はパス扱い（--balance未指定）
    if m['max_dd_pct'] is not None:
        dd_ok = m['max_dd_pct'] <= CRIT_DD
    else:
        dd_ok = None   # 判定不能

    passed = pf_cut and wr_ok and (dd_ok is None or dd_ok)

    return {
        'pf_cut':  pf_cut,
        'wr_ok':   wr_ok,
        'dd_ok':   dd_ok,
        'pass':    passed,
        'pf_gap':  round(CRIT_MIN_PF - m['pf'], 3) if not pf_cut else 0.0,
        'wr_gap':  round(CRIT_WIN - m['win_rate'], 4) if not wr_ok else 0.0,
        'dd_gap':  round((m['max_dd_pct'] or 0) - CRIT_DD, 4) if (m['max_dd_pct'] is not None and not dd_ok) else 0.0,
    }


def _ok(v) -> str:
    if v is None:
        return ' N/A'
    return ' PASS' if v else ' FAIL'


# ------------------------------------------------------------------ #
#  レポート出力
# ------------------------------------------------------------------ #
def print_report(groups: dict, balance: float | None):
    all_rows   = []
    bb_pf_list = []

    dd_label = f'MaxDD%' if balance else 'MaxDD(abs)'

    print()
    print('=' * 100)
    print('  Phase1 完了判定レポート')
    if balance:
        print(f'  口座残高: {balance:,.0f}  (DD%判定 有効)')
    else:
        print('  口座残高: 未指定  (DD%判定 スキップ — --balance で指定可)')
    print('=' * 100)

    header = (
        f"  {'戦略:ペア':<22} {'N':>4} {'PF':>7} {'勝率':>7} {dd_label:>12}"
        f"  {'PF足切':>6} {'勝率':>6} {'DD':>6}  {'判定':>6}"
    )
    sep = '-' * 100

    for strategy in ['BB', 'SMC', 'SARB']:
        pairs = groups.get(strategy, {})
        if not pairs:
            continue

        magic_num = BB_MAGIC if strategy == 'BB' else SMC_MAGIC if strategy == 'SMC' else SARB_MAGIC
        print()
        print(f'  [{strategy}]  magic={magic_num}')
        print(header)
        print(sep)

        for pair in sorted(pairs.keys()):
            trades = pairs[pair]
            m = calc_metrics(trades, balance)
            if m is None:
                continue

            j   = judge_pair(m)
            key = f'{strategy}:{pair}'

            if balance and m['max_dd_pct'] is not None:
                dd_str = f'{m["max_dd_pct"]*100:6.1f}%'
            else:
                dd_str = f'{m["max_dd_abs"]:>10,.0f}'

            print(
                f"  {key:<22} {m['n']:>4} {m['pf']:>7.3f} {m['win_rate']*100:>6.1f}%"
                f" {dd_str:>12}"
                f"  {_ok(j['pf_cut']):>6} {_ok(j['wr_ok']):>6} {_ok(j['dd_ok']):>6}"
                f"  {'[PASS]' if j['pass'] else '[FAIL]':>6}"
            )

            gap_parts = []
            if not j['pf_cut']:
                gap_parts.append(f'PF不足={j["pf_gap"]:.3f}')
            if not j['wr_ok']:
                gap_parts.append(f'勝率不足={j["wr_gap"]*100:.1f}%')
            if j['dd_ok'] is False:
                gap_parts.append(f'DD超過=+{j["dd_gap"]*100:.1f}%')
            if gap_parts:
                print(f"  {'':22}  -> {', '.join(gap_parts)}")

            all_rows.append((strategy, pair, m, j))

            if strategy == 'BB' and pair in BB_ACTIVE_PAIRS:
                bb_pf_list.append(m['pf'])

    # ------------------------------------------------------------------ #
    #  総合判定
    # ------------------------------------------------------------------ #
    print()
    print('=' * 100)

    avg_pf      = sum(bb_pf_list) / len(bb_pf_list) if bb_pf_list else 0.0
    avg_pf_pass = avg_pf >= CRIT_AVG_PF
    print(f'  [1] BB アクティブペア 平均PF: {avg_pf:.3f}  (条件>{CRIT_AVG_PF})  {_ok(avg_pf_pass)}')
    if not avg_pf_pass:
        print(f'       -> 不足={CRIT_AVG_PF - avg_pf:.3f}')

    fail_pf = [f'{s}:{p}' for s, p, m, j in all_rows if not j['pf_cut']]
    fail_wr = [f'{s}:{p}' for s, p, m, j in all_rows if not j['wr_ok']]
    fail_dd = [f'{s}:{p}' for s, p, m, j in all_rows if j['dd_ok'] is False]

    pf_cut_pass = len(fail_pf) == 0
    wr_pass     = len(fail_wr) == 0
    dd_pass     = len(fail_dd) == 0
    dd_na       = all(j['dd_ok'] is None for _, _, _, j in all_rows)

    print(f'  [2] 全ペア PF>{CRIT_MIN_PF}:              {_ok(pf_cut_pass)}', end='')
    if fail_pf:
        print(f'  -> {", ".join(fail_pf)}', end='')
    print()

    print(f'  [3] 全ペア 勝率>{CRIT_WIN*100:.0f}%:            {_ok(wr_pass)}', end='')
    if fail_wr:
        print(f'  -> {", ".join(fail_wr)}', end='')
    print()

    if dd_na:
        print(f'  [4] 全ペア 最大DD<{CRIT_DD*100:.0f}%:            N/A  (--balance 未指定のためスキップ)')
    else:
        print(f'  [4] 全ペア 最大DD<{CRIT_DD*100:.0f}%:            {_ok(dd_pass)}', end='')
        if fail_dd:
            print(f'  -> {", ".join(fail_dd)}', end='')
        print()

    print()
    overall = avg_pf_pass and pf_cut_pass and wr_pass and (dd_na or dd_pass)
    if overall:
        verdict = '  *** PASS *** Phase1 完了条件クリア！'
    else:
        verdict = '  *** FAIL *** Phase1 未完了 - データ蓄積継続'
    print('=' * 100)
    print(verdict)
    print('=' * 100)
    print()

    # サンプル数警告
    small_n = [(f'{s}:{p}', m['n']) for s, p, m, j in all_rows if m['n'] < 30]
    if small_n:
        print('  [!] サンプル数 < 30 のペア（判定信頼性低）:')
        for label, n in small_n:
            print(f'      {label}: {n}件')
        print()


# ------------------------------------------------------------------ #
#  メイン
# ------------------------------------------------------------------ #
def main():
    parser = argparse.ArgumentParser(
        description='Phase1完了判定 (BB/SMC/stat_arb)',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--csv',     type=Path,  default=None,
                        help='CSVファイル指定（MT5未起動環境用）')
    parser.add_argument('--days',    type=int,   default=DEFAULT_DAYS,
                        help=f'MT5 API取得期間・日数 (default: {DEFAULT_DAYS})')
    parser.add_argument('--balance', type=float, default=None,
                        help='口座残高(JPY換算)。指定するとDD%%判定が有効になる')
    args = parser.parse_args()

    if args.csv:
        if not args.csv.exists():
            print(f'[ERROR] CSVが見つかりません: {args.csv}')
            sys.exit(1)
        print(f'[INFO] CSVから読み込み: {args.csv}')
        trades = parse_csv(args.csv, magic=None)
    else:
        print('[INFO] MT5 API経由で取得中（magic=全件）...')
        try:
            trades = fetch_from_mt5(magic=None, days=args.days, save_csv=DEFAULT_CSV)
        except RuntimeError as e:
            print(f'[ERROR] {e}')
            print()
            print('フォールバック: python phase1_judgment.py --csv history.csv')
            sys.exit(1)

    print(f'[INFO] 総トレード数: {len(trades)}件')
    if not trades:
        print('[WARN] トレードが0件です。')
        sys.exit(1)

    groups = group_trades(trades)

    total_bb   = sum(len(v) for v in groups['BB'].values())
    total_smc  = sum(len(v) for v in groups['SMC'].values())
    total_sarb = sum(len(v) for v in groups['SARB'].values())
    print(f'[INFO] 内訳: BB={total_bb}件  SMC={total_smc}件  stat_arb={total_sarb}件')

    print_report(groups, args.balance)


if __name__ == '__main__':
    main()
