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
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import parse_csv, fetch_from_mt5

# ------------------------------------------------------------------ #
#  定数
# ------------------------------------------------------------------ #
BB_MAGIC   = 20250001
SMC_MAGIC  = 20260002
SARB_MAGIC = 20260001

EWMA_SPAN = 30

# Phase1判定条件
CRIT_N_MIN           = 50    # 条件1: 最低サンプル数
CRIT_EDGE_BUFFER     = 0.05  # 条件2: エッジバッファ（損益分岐点 + 5%）
CRIT_PF_MIN          = 1.0   # 条件2: 最低PF
CRIT_RUIN_BUFFER     = 0.10  # 条件3: 連敗耐性バッファ（損益分岐点 + 10%）
CRIT_MAX_CONSEC_LOSS = 10    # 条件3: 最大連敗上限
CRIT_DAYS_LIVE       = 30    # 条件4: 全戦略の最低稼働日数

# 推奨条件（WARNING表示のみ、必須ではない）
REC_RR_MIN       = 0.8   # 推奨 RR下限
REC_WR_DRIFT_MAX = 0.10  # 推奨 EWMA-全件WR乖離上限

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
#  ユーティリティ
# ------------------------------------------------------------------ #
def _parse_time(tv) -> datetime:
    if tv is None:
        return None
    if isinstance(tv, datetime):
        return tv if tv.tzinfo else tv.replace(tzinfo=timezone.utc)
    if isinstance(tv, (int, float)):
        try:
            return datetime.fromtimestamp(float(tv), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(tv, str):
        for fmt in ('%Y.%m.%d %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(tv.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _breakeven_wr(rr: float) -> float:
    return 1.0 / (1.0 + rr) if rr > 0 else 1.0


# ------------------------------------------------------------------ #
#  メトリクス計算
# ------------------------------------------------------------------ #
def calc_metrics(trades: list, balance: float = None) -> dict:
    if not trades:
        return None

    # 時刻順ソート（EWMAの計算に必要）
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        trades_sorted = sorted(
            trades,
            key=lambda t: _parse_time(t.get('time')) or _epoch,
        )
    except Exception:
        trades_sorted = trades

    profits  = [t['profit'] for t in trades_sorted]
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

    # RR: 平均利益 / 平均損失(絶対値)
    avg_win  = gross_profit / win_cnt  if win_cnt  > 0 else 0.0
    avg_loss = gross_loss   / loss_cnt if loss_cnt > 0 else 0.0
    rr       = avg_win / avg_loss if avg_loss > 0 else 0.0

    # 最大連敗
    max_consec_loss = 0
    cur_consec      = 0
    for p in profits:
        if p < 0:
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0

    # EWMA勝率 (span=EWMA_SPAN)
    alpha    = 2.0 / (EWMA_SPAN + 1)
    outcomes = [1.0 if p > 0 else 0.0 for p in profits]
    ewma_val = float(outcomes[0]) if outcomes else 0.0
    for v in outcomes[1:]:
        ewma_val = alpha * float(v) + (1.0 - alpha) * ewma_val
    wr_ewma = ewma_val

    # 最大DD: ピーク→トラフの絶対額
    equity     = 0.0
    peak       = 0.0
    max_dd_abs = 0.0
    for p in profits:
        equity += p
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs

    max_dd_pct = (max_dd_abs / balance) if (balance and balance > 0) else None

    return {
        'n':               total,
        'win':             win_cnt,
        'loss':            loss_cnt,
        'pf':              round(pf, 3),
        'win_rate':        round(win_rate, 4),
        'rr':              round(rr, 3),
        'wr_ewma':         round(wr_ewma, 4),
        'max_consec_loss': max_consec_loss,
        'max_dd_abs':      round(max_dd_abs, 0),
        'max_dd_pct':      round(max_dd_pct, 4) if max_dd_pct is not None else None,
    }


# ------------------------------------------------------------------ #
#  稼働日数計算
# ------------------------------------------------------------------ #
def calc_days_live(groups: dict) -> dict:
    """各戦略の最初のトレードから今日までの稼働日数を返す"""
    now = datetime.now(tz=timezone.utc)
    result = {}
    for strat, pairs in groups.items():
        all_trades = [t for ts in pairs.values() for t in ts]
        times = [_parse_time(t.get('time')) for t in all_trades]
        times = [tv for tv in times if tv is not None]
        result[strat] = (now - min(times)).days if times else None
    return result


# ------------------------------------------------------------------ #
#  トレード分類
# ------------------------------------------------------------------ #
def group_trades(trades: list) -> dict:
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
            sarb_grouped['SARB:' + sym] = ts

    return {
        'BB':   dict(bb_raw),
        'SMC':  dict(smc_raw),
        'SARB': sarb_grouped,
    }


# ------------------------------------------------------------------ #
#  判定ロジック
# ------------------------------------------------------------------ #
def judge_pair(m: dict) -> dict:
    """
    条件1: N >= 50
    条件2: WR > BE+0.05  AND  PF > 1.0
    条件3: WR > BE+0.10  AND  max_consec_loss <= 10
    推奨 (WARNING): RR > 0.8  /  abs(WR_ewma - WR) < 0.10
    """
    rr = m['rr']
    wr = m['win_rate']
    be = _breakeven_wr(rr)

    # 条件1
    cond1 = m['n'] >= CRIT_N_MIN

    # 条件2: エッジ存在証明
    cond2_wr = wr > be + CRIT_EDGE_BUFFER
    cond2_pf = m['pf'] > CRIT_PF_MIN
    cond2    = cond2_wr and cond2_pf

    # 条件3: 連敗耐性
    cond3_wr     = wr > be + CRIT_RUIN_BUFFER
    cond3_consec = m['max_consec_loss'] <= CRIT_MAX_CONSEC_LOSS
    cond3        = cond3_wr and cond3_consec

    # 推奨条件
    rec_rr    = rr > REC_RR_MIN
    wr_drift  = abs(m['wr_ewma'] - wr)
    rec_drift = wr_drift < REC_WR_DRIFT_MAX

    return {
        'pass':         cond1 and cond2 and cond3,
        'cond1':        cond1,
        'cond2':        cond2,
        'cond2_wr':     cond2_wr,
        'cond2_pf':     cond2_pf,
        'cond3':        cond3,
        'cond3_wr':     cond3_wr,
        'cond3_consec': cond3_consec,
        'rec_rr':       rec_rr,
        'rec_drift':    rec_drift,
        'be':           round(be, 4),
        'wr_req_c2':    round(be + CRIT_EDGE_BUFFER, 4),
        'wr_req_c3':    round(be + CRIT_RUIN_BUFFER, 4),
        'wr_drift':     round(wr_drift, 4),
    }


def _ok(v) -> str:
    if v is None:
        return ' N/A '
    return ' PASS' if v else ' FAIL'


# ------------------------------------------------------------------ #
#  レポート出力
# ------------------------------------------------------------------ #
def print_report(groups: dict, balance: float = None):
    all_rows  = []
    days_live = calc_days_live(groups)

    W = 112
    print()
    print('=' * W)
    print('  Phase1 完了判定レポート')
    if balance:
        print('  口座残高: {:,.0f}  (DD%判定 有効)'.format(balance))
    else:
        print('  口座残高: 未指定  (DD%判定 スキップ — --balance で指定可)')
    print('=' * W)

    for strategy in ['BB', 'SMC', 'SARB']:
        pairs = groups.get(strategy, {})
        if not pairs:
            continue

        magic_num = BB_MAGIC if strategy == 'BB' else (SMC_MAGIC if strategy == 'SMC' else SARB_MAGIC)
        dl = days_live.get(strategy)
        dl_str = '{}日'.format(dl) if dl is not None else 'N/A'
        print()
        print('  [{}]  magic={}  稼働日数={}'.format(strategy, magic_num, dl_str))
        print('  {:<22} {:>4} {:>7} {:>7} {:>6} {:>8} {:>4}  C1   C2   C3  判定'.format(
            'ペア', 'N', 'PF', 'WR', 'RR', 'WR_ewma', '連敗',
        ))
        print('-' * W)

        for pair in sorted(pairs.keys()):
            trades = pairs[pair]
            m = calc_metrics(trades, balance)
            if m is None:
                continue

            j   = judge_pair(m)
            key = '{}:{}'.format(strategy, pair)

            print(
                '  {:<22} {:>4} {:>7.3f} {:>6.1f}% {:>6.3f} {:>7.1f}% {:>4}  {} {} {}  {}'.format(
                    key, m['n'], m['pf'],
                    m['win_rate'] * 100, m['rr'],
                    m['wr_ewma'] * 100, m['max_consec_loss'],
                    _ok(j['cond1']), _ok(j['cond2']), _ok(j['cond3']),
                    '[PASS]' if j['pass'] else '[FAIL]',
                )
            )

            # FAIL詳細・推奨WARNING
            if not j['cond1']:
                print('    C1 [N>={}]:          FAIL  現在={} / 必要={}'.format(
                    CRIT_N_MIN, m['n'], CRIT_N_MIN))
            if not j['cond2_wr']:
                print('    C2 [WR>BE+{:.0f}%]:   FAIL  現在={:.1f}% / 必要={:.1f}%  (BE={:.1f}%  RR={:.3f})'.format(
                    CRIT_EDGE_BUFFER * 100,
                    m['win_rate'] * 100, j['wr_req_c2'] * 100,
                    j['be'] * 100, m['rr'],
                ))
            if not j['cond2_pf']:
                print('    C2 [PF>1.0]:         FAIL  現在={:.3f} / 必要>1.0'.format(m['pf']))
            if not j['cond3_wr']:
                print('    C3 [WR>BE+{:.0f}%]:  FAIL  現在={:.1f}% / 必要={:.1f}%'.format(
                    CRIT_RUIN_BUFFER * 100,
                    m['win_rate'] * 100, j['wr_req_c3'] * 100,
                ))
            if not j['cond3_consec']:
                print('    C3 [連敗<={}]:       FAIL  現在={} / 必要<={}'.format(
                    CRIT_MAX_CONSEC_LOSS, m['max_consec_loss'], CRIT_MAX_CONSEC_LOSS))
            if not j['rec_rr']:
                print('    推奨 [RR>{}]:        WARN  現在={:.3f} / 推奨>{}'.format(
                    REC_RR_MIN, m['rr'], REC_RR_MIN))
            if not j['rec_drift']:
                print('    推奨 [WR安定性]:      WARN  乖離={:.1f}% / 推奨<{:.0f}%'.format(
                    j['wr_drift'] * 100, REC_WR_DRIFT_MAX * 100))

            all_rows.append((strategy, pair, m, j))

    # ------------------------------------------------------------------ #
    #  総合判定
    # ------------------------------------------------------------------ #
    print()
    print('=' * W)

    active_strats = [s for s in ['BB', 'SMC', 'SARB'] if groups.get(s)]

    # 条件4: 全戦略 稼働日数 >= 30
    c4_detail = {}
    for s in active_strats:
        dl = days_live.get(s)
        c4_detail[s] = (dl is not None and dl >= CRIT_DAYS_LIVE, dl)
    cond4_pass = all(ok for ok, _ in c4_detail.values())

    print('  [C4] 全戦略 稼働日数>={0}日:  {1}'.format(CRIT_DAYS_LIVE, _ok(cond4_pass)))
    for s, (ok, dl) in c4_detail.items():
        dl_str = '{}日'.format(dl) if dl is not None else 'N/A'
        print('       {}: {}  {}'.format(s, dl_str, _ok(ok)))
        if not ok:
            req = CRIT_DAYS_LIVE - (dl or 0)
            print('         -> あと{}日'.format(max(0, req)))

    fail_c1 = ['{}:{}'.format(s, p) for s, p, m, j in all_rows if not j['cond1']]
    fail_c2 = ['{}:{}'.format(s, p) for s, p, m, j in all_rows if not j['cond2']]
    fail_c3 = ['{}:{}'.format(s, p) for s, p, m, j in all_rows if not j['cond3']]

    c1_pass = len(fail_c1) == 0
    c2_pass = len(fail_c2) == 0
    c3_pass = len(fail_c3) == 0

    print('  [C1] 全ペア N>={0}:              {1}'.format(CRIT_N_MIN, _ok(c1_pass)), end='')
    if fail_c1:
        print('  -> {}'.format(', '.join(fail_c1)), end='')
    print()

    print('  [C2] 全ペア エッジ存在:           {}'.format(_ok(c2_pass)), end='')
    if fail_c2:
        print('  -> {}'.format(', '.join(fail_c2)), end='')
    print()

    print('  [C3] 全ペア 連敗耐性:             {}'.format(_ok(c3_pass)), end='')
    if fail_c3:
        print('  -> {}'.format(', '.join(fail_c3)), end='')
    print()

    print()
    overall = c1_pass and c2_pass and c3_pass and cond4_pass
    verdict = (
        '  *** PASS *** Phase1 完了条件クリア！'
        if overall else
        '  *** FAIL *** Phase1 未完了 - データ蓄積継続'
    )
    print('=' * W)
    print(verdict)
    print('=' * W)
    print()


# ------------------------------------------------------------------ #
#  コンパクトサマリー（daily_report / Discord 埋め込み用）
# ------------------------------------------------------------------ #
def get_compact_report(groups: dict) -> str:
    """
    ペア別1行 + 総合判定のコンパクト文字列を返す。
    daily_report.py から呼び出して日次/週次レポートに埋め込む用途。
    """
    lines = ['[Phase1判定サマリー]']
    days_live = calc_days_live(groups)
    all_rows = []

    for strategy in ['BB', 'SMC', 'SARB']:
        pairs = groups.get(strategy, {})
        if not pairs:
            continue
        for pair in sorted(pairs.keys()):
            m = calc_metrics(pairs[pair])
            if m is None:
                continue
            j   = judge_pair(m)
            key = '{}:{}'.format(strategy, pair)
            c_str = 'C1:{} C2:{} C3:{}'.format(
                'OK' if j['cond1'] else 'NG',
                'OK' if j['cond2'] else 'NG',
                'OK' if j['cond3'] else 'NG',
            )
            lines.append('  {:<22} n={:>3}  WR={:.0f}%  RR={:.2f}  {}  [{}]'.format(
                key, m['n'], m['win_rate'] * 100, m['rr'],
                c_str, 'PASS' if j['pass'] else 'FAIL',
            ))
            all_rows.append((strategy, pair, m, j))

    if not all_rows:
        lines.append('  データなし')
        return '\n'.join(lines)

    # 条件4: 稼働日数
    active = [s for s in ['BB', 'SMC', 'SARB'] if groups.get(s)]
    c4_pass = all(
        days_live.get(s) is not None and days_live[s] >= CRIT_DAYS_LIVE
        for s in active
    )
    dl_parts = ['{}={}日'.format(s, days_live.get(s, '?')) for s in active]
    lines.append('  C4 稼働日数: {}  [{}]'.format(
        ' / '.join(dl_parts), 'PASS' if c4_pass else 'FAIL',
    ))

    # 総合
    overall = (
        all(j['cond1'] for _, _, _, j in all_rows) and
        all(j['cond2'] for _, _, _, j in all_rows) and
        all(j['cond3'] for _, _, _, j in all_rows) and
        c4_pass
    )
    lines.append('  総合: {}'.format('*** PASS ***' if overall else 'FAIL'))
    return '\n'.join(lines)


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
                        help='MT5 API取得期間・日数 (default: {})'.format(DEFAULT_DAYS))
    parser.add_argument('--balance', type=float, default=None,
                        help='口座残高(JPY換算)。指定するとDD%%判定が有効になる')
    args = parser.parse_args()

    if args.csv:
        if not args.csv.exists():
            print('[ERROR] CSVが見つかりません: {}'.format(args.csv))
            sys.exit(1)
        print('[INFO] CSVから読み込み: {}'.format(args.csv))
        trades = parse_csv(args.csv, magic=None)
    else:
        print('[INFO] MT5 API経由で取得中（magic=全件）...')
        try:
            trades = fetch_from_mt5(magic=None, days=args.days, save_csv=DEFAULT_CSV)
        except RuntimeError as e:
            print('[ERROR] {}'.format(e))
            print()
            print('フォールバック: python phase1_judgment.py --csv history.csv')
            sys.exit(1)

    print('[INFO] 総トレード数: {}件'.format(len(trades)))
    if not trades:
        print('[WARN] トレードが0件です。')
        sys.exit(1)

    groups = group_trades(trades)

    total_bb   = sum(len(v) for v in groups['BB'].values())
    total_smc  = sum(len(v) for v in groups['SMC'].values())
    total_sarb = sum(len(v) for v in groups['SARB'].values())
    print('[INFO] 内訳: BB={}件  SMC={}件  stat_arb={}件'.format(
        total_bb, total_smc, total_sarb))

    print_report(groups, args.balance)


if __name__ == '__main__':
    main()
