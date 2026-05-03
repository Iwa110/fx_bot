"""
analyze_performance.py  v3
========================================================
MT5の約定履歴のみから取引パフォーマンスを分析する。
trade_log.json不要。

【v3の改善点（v2からの追加）】
  - 勝ちの3分類: TP到達 / TrailSL脱出（引き分け） / SL負け
  - TP想定額をhistory_orders_get(TP/SL価格)から正確に取得
    → 取得できない場合はRR比から逆算（フォールバック）
  - 週次KPIサマリーを出力（summary_notify.pyに流せる形式）
  - BB戦略ペア別のTP到達率を個別集計
  - Phase判定（月30万ロードマップの現在地）
  - 月次収益推移
  - v2の実RR比・損益分岐勝率・ペア別BT勝率比較を維持

【勝ちの定義（ロードマップ準拠）】
  TP到達  : profit >= TP想定額 × TP_THRESHOLD(0.8)  ← 真の勝ち
  Trail脱出: 0 < profit < TP想定額 × TP_THRESHOLD    ← 引き分け（ボーナス）
  SL負け  : profit <= 0                              ← 負け

【修正履歴】
  v3.1: rr変数スコープバグ修正 / history_orders_getでTP/SL取得復活
        profit_pip・hold_min・win フィールド復活
        実RR比・損益分岐勝率の表示復活
        ペア別BT勝率を個別値に戻す
        時間帯UTC/JST二重変換バグ修正（VPS_IS_UTC定数で制御）
========================================================
"""

import sys, os
sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')

import MetaTrader5 as mt5
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'bb_log.txt')

# ══════════════════════════════════════════
# 設定
# ══════════════════════════════════════════

# 分析対象期間（日数）
ANALYSIS_DAYS = 30

# TP到達判定の閾値（TP想定額の何%以上で「TP到達」とみなすか）
TP_THRESHOLD = 0.80

# VPSのタイムゾーン設定
# True  = VPSがUTC設定 → JSTに+9時間する
# False = VPSがJST設定 → 変換しない
VPS_IS_UTC = False

# TP/SL想定Multiplier（risk_manager.pyと同じ値を維持すること）
MULTIPLIERS = {
    'BB':      {'tp': 3.0, 'sl': 2.0},   # RR = 1.5
    'MOM_JPY': {'tp': 3.0, 'sl': 1.0},   # RR = 3.0
    'MOM_GBJ': {'tp': 1.0, 'sl': 0.5},   # RR = 2.0
    'CORR':    {'tp': 2.0, 'sl': 1.5},
    'TRI':     {'tp': 2.0, 'sl': 1.5},
    'STR':     {'tp': 2.0, 'sl': 1.5},
}

# ペア別バックテスト勝率（比較用・v2互換）
BT_WINRATE = {
    'USDCAD': 97.6, 'GBPJPY': 94.4, 'EURJPY': 94.3,
    'USDJPY': 93.0, 'AUDJPY': 92.8, 'EURUSD': 85.8, 'GBPUSD': 82.0,
}
# BTのTP到達率参照値（逆張りBB）
BT_TP_RATE = 62.0

# 戦略識別プレフィックス（長いものを先に）
STRATEGY_PREFIXES = [
    ('MOM_JPY', 'MOM_JPY'),
    ('MOM_GBJ', 'MOM_GBJ'),
    ('BB_',     'BB'),
    ('TRI_',    'TRI'),
    ('CORR_',   'CORR'),
    ('STR_',    'STR'),
]

# SLクールダウン検出（損切後何分以内の再エントリーを警告するか）
COOLDOWN_MINUTES = 15

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════

def to_jst(ts: int) -> datetime:
    """MT5のUNIXタイムスタンプをVPS設定に応じてJSTに変換する"""
    dt = datetime.fromtimestamp(ts)
    if VPS_IS_UTC:
        dt = dt + timedelta(hours=9)
    return dt


def pip_size(symbol: str) -> float:
    return 0.01 if 'JPY' in symbol else 0.0001


def get_strategy(comment: str) -> str:
    """commentから戦略名を返す"""
    if not comment:
        return 'UNKNOWN'
    for prefix, label in STRATEGY_PREFIXES:
        if comment.startswith(prefix):
            return label
    # MT5自動コメント（[sl xx]等）はUNKNOWN
    if comment.startswith('['):
        return 'UNKNOWN'
    # その他は先頭8文字
    return comment[:8]


def get_rr(strategy: str) -> float:
    """戦略名からRR比を返す"""
    for key, mult in MULTIPLIERS.items():
        if strategy == key or strategy.startswith(key):
            return mult['tp'] / mult['sl'] if mult['sl'] > 0 else 1.5
    return 1.5  # デフォルト


def classify_trade(profit: float, tp_expected: float) -> str:
    """
    取引を3分類する。
      TP到達  : profit >= tp_expected × TP_THRESHOLD
      Trail脱出: 0 < profit < tp_expected × TP_THRESHOLD
      SL負け  : profit <= 0
    tp_expectedが0以下の場合はprofit > 0 でTRAILと判定（フォールバック）
    """
    if tp_expected <= 0:
        return 'TRAIL' if profit > 0 else 'SL'
    if profit >= tp_expected * TP_THRESHOLD:
        return 'TP'
    elif profit > 0:
        return 'TRAIL'
    else:
        return 'SL'


# ══════════════════════════════════════════
# MT5データ取得・トレード構築
# ══════════════════════════════════════════

def fetch_deals(days: int = ANALYSIS_DAYS) -> list:
    """過去N日分の約定履歴を取得"""
    since = datetime.now() - timedelta(days=days)
    deals = mt5.history_deals_get(since, datetime.now())
    return list(deals) if deals else []


def build_trades(deals: list) -> list:
    """
    IN/OUTをposition_idでペアリングしてトレードリストを構築。
    戦略名はINのcommentから取得（OUTはMT5が[sl xx]等に上書きするため）。
    TP/SL価格はhistory_orders_getから取得し、tp_expectedを正確に計算する。
    """
    in_deals  = {}
    out_deals = {}

    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_IN:
            in_deals[d.position_id] = d
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            out_deals[d.position_id] = d

    trades = []
    for pos_id, out_d in out_deals.items():
        in_d = in_deals.get(pos_id)
        if in_d is None:
            continue

        symbol    = out_d.symbol
        profit    = out_d.profit
        comment   = (in_d.comment or out_d.comment or '').strip()
        lot       = out_d.volume
        direction = 1 if in_d.type == mt5.DEAL_TYPE_BUY else -1
        entry_px  = in_d.price
        exit_px   = out_d.price
        pip       = pip_size(symbol)
        strategy  = get_strategy(comment)
        rr        = get_rr(strategy)   # トレードごとに計算（スコープ問題を回避）

        # ── TP/SL価格をhistory_ordersから取得 ────────
        tp_px = sl_px = 0.0
        orders = mt5.history_orders_get(position=pos_id)
        if orders:
            for o in orders:
                if o.tp and o.tp != 0.0:
                    tp_px = o.tp
                if o.sl and o.sl != 0.0:
                    sl_px = o.sl

        # ── TP想定損益の計算 ──────────────────────────
        # 優先: TP価格が取得できた場合 → 距離×lot×pip換算
        if tp_px > 0.0 and entry_px > 0.0:
            tp_dist     = abs(tp_px - entry_px)
            tp_expected = tp_dist / pip * lot * 100
        elif profit < 0:
            # フォールバック: SLヒット損からRR比で逆算
            tp_expected = abs(profit) * rr
        else:
            # フォールバック: プラス決済でTP価格不明 → 0（分類でフォールバック使用）
            tp_expected = 0.0

        # ── 分類・補助フィールド ──────────────────────
        result     = classify_trade(profit, tp_expected)
        profit_pip = round((exit_px - entry_px) * direction / pip, 1)
        hold_min   = int((out_d.time - in_d.time) / 60)

        trades.append({
            'pos_id':      pos_id,
            'symbol':      symbol,
            'strategy':    strategy,
            'direction':   direction,
            'lot':         lot,
            'entry':       entry_px,
            'exit':        exit_px,
            'tp':          tp_px,
            'sl':          sl_px,
            'profit':      profit,
            'profit_pip':  profit_pip,
            'tp_expected': tp_expected,
            'result':      result,
            'win':         profit > 0,          # 広義の勝ち（v2互換）
            'open_time':   to_jst(in_d.time),
            'close_time':  to_jst(out_d.time),
            'hold_min':    hold_min,
            'comment':     comment,
        })

    trades.sort(key=lambda x: x['close_time'])
    return trades


# ══════════════════════════════════════════
# 統計計算
# ══════════════════════════════════════════

def calc_stats(trades: list, label: str = '') -> dict:
    """トレードリストから統計を計算"""
    if not trades:
        return {}

    profits   = [t['profit'] for t in trades]
    tp_list   = [t for t in trades if t['result'] == 'TP']
    tr_list   = [t for t in trades if t['result'] == 'TRAIL']
    sl_list   = [t for t in trades if t['result'] == 'SL']
    wins      = [t for t in trades if t['win']]    # 広義（v2互換）
    losses    = [t for t in trades if not t['win']]

    n          = len(trades)
    tp_rate    = len(tp_list) / n * 100
    trail_rate = len(tr_list) / n * 100
    sl_rate    = len(sl_list) / n * 100
    win_rate   = len(wins) / n * 100   # 広義勝率（TP+Trail）

    total_profit  = sum(profits)
    avg_profit    = np.mean(profits)
    avg_tp_profit = np.mean([t['profit'] for t in tp_list]) if tp_list else 0
    avg_tr_profit = np.mean([t['profit'] for t in tr_list]) if tr_list else 0
    avg_sl_loss   = np.mean([t['profit'] for t in sl_list]) if sl_list else 0
    avg_hold      = np.mean([t['hold_min'] for t in trades])

    # 実RR比（v2互換）
    avg_win_pnl  = np.mean([t['profit'] for t in wins])   if wins   else 0
    avg_loss_pnl = np.mean([t['profit'] for t in losses]) if losses else 0
    real_rr      = abs(avg_win_pnl / avg_loss_pnl) if avg_loss_pnl != 0 else 0

    # 損益分岐勝率（v2互換）
    be_winrate = 1 / (1 + real_rr) * 100 if real_rr > 0 else 50.0
    ev_status  = '✅ プラス期待値' if win_rate > be_winrate else '❌ マイナス期待値'

    # Sharpe
    sharpe = 0.0
    if len(profits) > 1 and np.std(profits) > 0:
        sharpe = round(np.mean(profits) / np.std(profits) * np.sqrt(252), 2)

    # 最大DD
    cum = peak = max_dd = 0
    for p in profits:
        cum   += p
        peak   = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        'label':          label,
        'n':              n,
        'tp':             len(tp_list),
        'trail':          len(tr_list),
        'sl':             len(sl_list),
        'tp_rate':        round(tp_rate, 1),
        'trail_rate':     round(trail_rate, 1),
        'sl_rate':        round(sl_rate, 1),
        'win_rate':       round(win_rate, 1),
        'total_profit':   round(total_profit),
        'avg_profit':     round(avg_profit),
        'avg_tp':         round(avg_tp_profit),
        'avg_trail':      round(avg_tr_profit),
        'avg_sl':         round(avg_sl_loss),
        'avg_hold':       round(avg_hold),
        'real_rr':        round(real_rr, 2),
        'be_winrate':     round(be_winrate, 1),
        'ev_status':      ev_status,
        'sharpe':         sharpe,
        'max_dd':         round(max_dd),
        'expected_value': round(avg_profit),
    }


# ══════════════════════════════════════════
# SLクールダウン検出
# ══════════════════════════════════════════

def detect_sl_cooldown(trades: list) -> list:
    """SL後COOLDOWN_MINUTES以内の同一シンボル再エントリーを検出"""
    result   = []
    sl_times = defaultdict(list)

    for t in trades:
        sym = t['symbol']
        for sl_time in sl_times[sym]:
            gap = (t['open_time'] - sl_time).total_seconds() / 60
            if 0 < gap <= COOLDOWN_MINUTES:
                result.append({
                    'symbol':     sym,
                    'strategy':   t['strategy'],
                    'sl_time':    sl_time,
                    'entry_time': t['open_time'],
                    'gap_min':    round(gap, 1),
                    're_result':  t['result'],
                })
        if t['result'] == 'SL':
            sl_times[sym].append(t['close_time'])

    return result


# ══════════════════════════════════════════
# 週次KPI生成（summary_notify.py用）
# ══════════════════════════════════════════

def build_weekly_kpi(trades: list) -> str:
    """直近7日のKPIサマリー文字列を生成（Discord通知用）"""
    since       = datetime.now() - timedelta(days=7)
    week_trades = [t for t in trades if t['close_time'] >= since]

    if not week_trades:
        return '【週次KPI】取引なし'

    s = calc_stats(week_trades)
    lines = [
        '📊 **週次KPI**',
        f'期間: {since.strftime("%m/%d")}〜{datetime.now().strftime("%m/%d")}',
        f'取引数: {s["n"]}件 (TP:{s["tp"]} Trail:{s["trail"]} SL:{s["sl"]})',
        f'TP到達率: {s["tp_rate"]}%  ← 目標35%以上',
        f'広義勝率: {s["win_rate"]}%  実RR: {s["real_rr"]}  {s["ev_status"]}',
        f'期待値/件: {s["expected_value"]:+,}円',
        f'週間損益: {s["total_profit"]:+,}円',
        f'最大DD: {s["max_dd"]:,}円',
    ]
    if s['tp_rate'] >= 35:
        lines.append('✅ Phase1クリア基準達成')
    elif s['tp_rate'] >= 25:
        lines.append('⚠️ TP到達率改善中（目標35%）')
    else:
        lines.append('❌ TP到達率低下（要見直し）')

    return '\n'.join(lines)


# ══════════════════════════════════════════
# 表示ヘルパー
# ══════════════════════════════════════════

def print_stats(s: dict):
    if not s:
        print('  データなし')
        return
    print(f'  件数: {s["n"]}件  '
          f'(TP:{s["tp"]} {s["tp_rate"]}% | '
          f'Trail:{s["trail"]} {s["trail_rate"]}% | '
          f'SL:{s["sl"]} {s["sl_rate"]}%)')
    print(f'  広義勝率: {s["win_rate"]}%  '
          f'実RR: {s["real_rr"]}  '
          f'損益分岐勝率: {s["be_winrate"]}%  {s["ev_status"]}')
    print(f'  期待値/件: {s["expected_value"]:+,}円  '
          f'Sharpe: {s["sharpe"]}  '
          f'平均保有: {s["avg_hold"]}分')
    print(f'  損益合計: {s["total_profit"]:+,}円  '
          f'最大DD: {s["max_dd"]:,}円')
    print(f'  平均TP益: {s["avg_tp"]:+,}円  '
          f'平均Trail益: {s["avg_trail"]:+,}円  '
          f'平均SL損: {s["avg_sl"]:+,}円')


def print_section(title: str):
    print('\n' + '=' * 65)
    print(f'  {title}')
    print('=' * 65)


# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════

def main():
    print('analyze_performance.py v3 起動')
    print(f'分析期間: 直近{ANALYSIS_DAYS}日')
    print(f'TP到達判定閾値: TP想定額の{int(TP_THRESHOLD*100)}%以上')
    print(f'時刻変換: {"UTC→JST(+9h)" if VPS_IS_UTC else "JST(変換なし)"}')

    if not mt5.initialize():
        print('MT5初期化失敗')
        return

    account = mt5.account_info()
    if account is None:
        print('口座情報取得失敗')
        mt5.shutdown()
        return

    balance = round(account.balance)
    equity  = round(account.equity)
    print('=' * 65)
    print(f'MT5接続: {account.company}  残高: {balance:,}円  有効証拠金: {equity:,}円')
    print('=' * 65)

    deals  = fetch_deals(ANALYSIS_DAYS)
    trades = build_trades(deals)

    if not trades:
        print('分析対象取引なし（IN/OUTペアリング失敗の可能性）')
        mt5.shutdown()
        return

    print(f'\n取得取引数: {len(trades)}件')

    # ──────────────────────────────────────
    # 1. 全体統計
    # ──────────────────────────────────────
    print_section('1. 全体統計')
    all_stats = calc_stats(trades, '全戦略合計')
    print_stats(all_stats)

    # ──────────────────────────────────────
    # 2. 戦略別統計
    # ──────────────────────────────────────
    print_section('2. 戦略別統計')
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t['strategy']].append(t)

    for strat in sorted(by_strategy.keys()):
        s = calc_stats(by_strategy[strat], strat)
        print(f'\n■ 戦略: {strat} ({s["n"]}件)')
        print_stats(s)

    # ──────────────────────────────────────
    # 3. BB戦略ペア別詳細 vs バックテスト比較
    # ──────────────────────────────────────
    bb_trades = [t for t in trades if t['strategy'] == 'BB']
    if bb_trades:
        print_section('3. BB戦略ペア別実績 vs バックテスト比較')
        print(f'  {"ペア":<10} {"取引":>4} {"TP":>4} {"Trail":>5} {"SL":>4} '
              f'{"TP率":>6} {"BT勝率":>7} {"差分":>6} {"累計損益":>10}')
        print('  ' + '-' * 68)

        by_pair = defaultdict(list)
        for t in bb_trades:
            by_pair[t['symbol']].append(t)

        for pair in sorted(by_pair.keys()):
            pt = by_pair[pair]
            s  = calc_stats(pt)
            if not s:
                continue
            bt_wr  = BT_WINRATE.get(pair, 0)
            diff   = s['win_rate'] - bt_wr
            emoji  = '✅' if diff >= -5 else ('⚠️ ' if diff >= -15 else '❌')
            bt_str = f'{bt_wr:.1f}%' if bt_wr else '  n/a '
            print(f'  {pair:<10} {s["n"]:>4}回 {s["tp"]:>4} {s["trail"]:>5} {s["sl"]:>4} '
                  f'{s["tp_rate"]:>5.1f}% {bt_str:>7} {diff:>+5.1f}%  {emoji} '
                  f'{s["total_profit"]:>+10,}円')

        print(f'\n  BT広義勝率との比較（各ペア個別）')
        print(f'  ✅=BT比-5%以内  ⚠️=BT比-15%以内  ❌=要見直し')
        print(f'  TP到達率参照値(BT): {BT_TP_RATE}%')
        print(f'\n■ BB全体')
        print_stats(calc_stats(bb_trades))

    # ──────────────────────────────────────
    # 4. SLクールダウン検出
    # ──────────────────────────────────────
    print_section(f'4. SLクールダウン検出（{COOLDOWN_MINUTES}分以内再エントリー）')
    cooldowns = detect_sl_cooldown(trades)
    if cooldowns:
        print(f'  ⚠️ {len(cooldowns)}件の過剰エントリーを検出:')
        for w in cooldowns[:10]:
            re_label = {'TP': '✅勝', 'TRAIL': '△引分', 'SL': '❌負'}.get(w['re_result'], '?')
            print(f'  {w["symbol"]} [{w["strategy"]}] '
                  f'SL: {w["sl_time"].strftime("%m/%d %H:%M")} → '
                  f'再IN: {w["entry_time"].strftime("%H:%M")} '
                  f'({w["gap_min"]}分後) 結果:{re_label}')
        if len(cooldowns) > 10:
            print(f'  ... 他{len(cooldowns)-10}件')
    else:
        print('  ✅ クールダウン違反なし')

    # ──────────────────────────────────────
    # 5. 時間帯別TP到達率（JST）
    # ──────────────────────────────────────
    print_section('5. 時間帯別TP到達率（JST）')
    hour_data = defaultdict(lambda: {'n': 0, 'tp': 0, 'profit': 0})
    for t in trades:
        h = t['open_time'].hour   # to_jst()で変換済みなのでそのまま使う
        hour_data[h]['n']      += 1
        hour_data[h]['profit'] += t['profit']
        if t['result'] == 'TP':
            hour_data[h]['tp'] += 1

    print(f'  {"時刻(JST)":>8} {"件数":>4} {"TP率":>6} {"損益":>10}')
    for h in sorted(hour_data.keys()):
        d    = hour_data[h]
        if d['n'] < 2:
            continue
        tp_r = d['tp'] / d['n'] * 100
        bar  = '█' * int(tp_r / 10)
        flag = '🔥' if tp_r >= 40 else ('⚠️' if tp_r < 20 else '')
        print(f'  {h:>6}時  {bar:<10} {d["n"]:>3}件  {tp_r:>5.1f}%  {d["profit"]:>+10,}円  {flag}')

    # ──────────────────────────────────────
    # 6. BUY/SELL別統計
    # ──────────────────────────────────────
    print_section('6. BUY/SELL別統計')
    for direction, label in [(1, 'BUY'), (-1, 'SELL')]:
        dt = [t for t in trades if t['direction'] == direction]
        if dt:
            print(f'\n■ {label} ({len(dt)}件)')
            print_stats(calc_stats(dt, label))

    # ──────────────────────────────────────
    # 7. 月次収益推移
    # ──────────────────────────────────────
    print_section('7. 月次収益推移')
    monthly = defaultdict(list)
    for t in trades:
        monthly[t['close_time'].strftime('%Y-%m')].append(t)

    print(f'  {"月":>8} {"件数":>4} {"TP率":>6} {"広義勝率":>8} {"損益":>12} {"累積":>12}')
    print('  ' + '-' * 58)
    cumulative = 0
    for month in sorted(monthly.keys()):
        s  = calc_stats(monthly[month])
        cumulative += s['total_profit']
        flag = '📈' if s['total_profit'] > 0 else '📉'
        print(f'  {month:>8} {s["n"]:>4}件 {s["tp_rate"]:>5.1f}% '
              f'{s["win_rate"]:>7.1f}% '
              f'{s["total_profit"]:>+12,} {cumulative:>+12,}  {flag}')

    # ──────────────────────────────────────
    # 8. オープンポジション
    # ──────────────────────────────────────
    print_section('8. 現在のオープンポジション')
    positions = mt5.positions_get()
    if positions:
        total_float = 0.0
        for p in positions:
            pip   = pip_size(p.symbol)
            tick  = mt5.symbol_info_tick(p.symbol)
            d     = 1 if p.type == 0 else -1
            cur   = (tick.bid if d == 1 else tick.ask) if tick else p.price_open
            pips  = round((cur - p.price_open) * d / pip, 1)
            pnl   = round(p.profit)
            strat = get_strategy(p.comment)
            total_float += p.profit
            print(f'  {p.symbol:<10} {"BUY" if d==1 else "SELL":<5} '
                  f'{strat:<10} 含み:{pips:+.1f}pips ({pnl:+,}円)  '
                  f'lot={p.volume}  {p.comment}')
        print(f'  {"─"*50}')
        print(f'  含み益合計: {round(total_float):+,}円')
    else:
        print('  オープンポジションなし')

    # ──────────────────────────────────────
    # 9. 総括 & Phase判定
    # ──────────────────────────────────────
    print_section('9. 総括 & Phase判定（月30万ロードマップ）')
    s = all_stats

    # v2互換の総括判定
    if s['win_rate'] > s['be_winrate']:
        verdict = '✅ プラス期待値で推移中'
    else:
        verdict = f'⚠️  損益分岐勝率{s["be_winrate"]}%以上が必要（現在{s["win_rate"]}%）'
    print(f'\n  {verdict}')

    if bb_trades:
        bb_s   = calc_stats(bb_trades)
        bt_avg = sum(BT_WINRATE.values()) / len(BT_WINRATE)
        gap    = bb_s['win_rate'] - bt_avg
        print(f'  BB戦略: 広義勝率{bb_s["win_rate"]}% vs BT平均{bt_avg:.1f}%（差{gap:+.1f}%）')
        if gap < -20:
            print('  → スプレッドコスト・フィルター効果を要再検討')
        elif gap < -10:
            print('  → 軽微な乖離、引き続きモニタリング')
        else:
            print('  → BT比較で許容範囲内')

    print(f'\n  全期間TP到達率: {s["tp_rate"]}%  (目標: Phase1=35%以上)')
    print(f'  期待値/件:      {s["expected_value"]:+,}円')
    print()

    if s['tp_rate'] >= 35 and s['expected_value'] > 0:
        print('  ✅ Phase1クリア → Phase2（月次黒字化）へ移行可能')
        monthly_est = s['expected_value'] * (s['n'] / (ANALYSIS_DAYS / 30))
        print(f'  📈 月次損益推計: {round(monthly_est):+,}円/月')
        if monthly_est >= 300000:
            print('  🎯 月30万円達成ペース！')
        elif monthly_est >= 100000:
            print('  📊 Phase3水準。ロット増加で目標射程内。')
        else:
            print('  📊 Phase2水準。トレード頻度またはロット増加が次の課題。')
    elif s['tp_rate'] >= 25:
        print('  ⚠️ Phase1継続中 → TP到達率を35%以上に改善が必要')
        print('  　 確認事項: HTFフィルターσ / RSI閾値 / 対象ペアの絞り込み')
    else:
        print('  ❌ Phase1未達 → パラメーター見直しが必要')
        print('  　 確認事項: TP/SL比率 / エントリー精度 / 相場環境とフィルターの整合性')

    # ──────────────────────────────────────
    # 10. 週次KPI（Discord用）
    # ──────────────────────────────────────
    print_section('10. 週次KPI（summary_notify.py用）')
    print(build_weekly_kpi(trades))

    mt5.shutdown()
    print(f'\n分析完了: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')


if __name__ == '__main__':
    main()