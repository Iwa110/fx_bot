"""
analyze_bb_v8.py
BB戦略 v8限定パフォーマンス分析
  - magic=20250001 かつ open_time >= V8_START_TIME のものだけ対象
  - デフォルト24時間 or 引数で時間数を指定可能: python analyze_bb_v8.py 48
  - HOURS=0 で v8開始以降の全件
  - TP/Trail/SL分類・ペア別・時間帯別・BUY/SELL別統計

使い方:
  python analyze_bb_v8.py        # 直近24時間
  python analyze_bb_v8.py 48     # 直近48時間
  python analyze_bb_v8.py 0      # v8開始以降の全件
"""

import sys, os
sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')

import MetaTrader5 as mt5
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

# ══════════════════════════════════════════
# 設定
# ══════════════════════════════════════════
HOURS        = int(sys.argv[1]) if len(sys.argv) > 1 else 24
MAGIC_V8     = 20250001
TP_THRESHOLD = 0.80

# v8配置時刻（ログ[2026-04-23 09:19:54]をそのまま使用）
V8_START_TIME = datetime(2026, 4, 23, 9, 19, 54)

# VPS時刻がUTCならTrue（JSTに+9h）、JSTならFalse
VPS_IS_UTC = False

BT_WINRATE = {
    'USDCAD': 97.6, 'GBPJPY': 94.4, 'EURJPY': 94.3,
    'USDJPY': 93.0, 'AUDJPY': 92.8, 'EURUSD': 85.8, 'GBPUSD': 82.0,
}
BT_TP_RATE  = 62.0
MULTIPLIERS = {
    'BB':      {'tp': 3.0, 'sl': 2.0},
    'MOM_JPY': {'tp': 3.0, 'sl': 1.0},
    'MOM_GBJ': {'tp': 1.0, 'sl': 0.5},
    'CORR':    {'tp': 2.0, 'sl': 1.5},
    'TRI':     {'tp': 2.0, 'sl': 1.5},
    'STR':     {'tp': 2.0, 'sl': 1.5},
}
COOLDOWN_MINUTES = 15

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
def to_dt(ts: int) -> datetime:
    dt = datetime.fromtimestamp(ts)
    if VPS_IS_UTC:
        dt += timedelta(hours=9)
    return dt

def pip_size(symbol: str) -> float:
    return 0.01 if 'JPY' in symbol else 0.0001

def get_strategy(comment: str) -> str:
    if not comment:
        return 'UNKNOWN'
    for prefix, label in [('MOM_JPY','MOM_JPY'),('MOM_GBJ','MOM_GBJ'),
                           ('BB_','BB'),('TRI_','TRI'),('CORR_','CORR'),('STR_','STR')]:
        if comment.startswith(prefix):
            return label
    return 'UNKNOWN'

def get_rr(strategy: str) -> float:
    for key, mult in MULTIPLIERS.items():
        if strategy == key or strategy.startswith(key):
            return mult['tp'] / mult['sl'] if mult['sl'] > 0 else 1.5
    return 1.5

def classify(profit: float, tp_expected: float) -> str:
    if tp_expected <= 0:
        return 'TRAIL' if profit > 0 else 'SL'
    if profit >= tp_expected * TP_THRESHOLD:
        return 'TP'
    elif profit > 0:
        return 'TRAIL'
    else:
        return 'SL'

# ══════════════════════════════════════════
# データ取得・トレード構築
# ══════════════════════════════════════════
def fetch_trades() -> list:
    """magic=20250001 / comment=BB_ / open>=V8_START のトレードを返す"""
    since = datetime.now() - timedelta(days=7)  # 最大7日さかのぼって取得
    deals = mt5.history_deals_get(since, datetime.now())
    if not deals:
        return []

    in_deals  = {}
    out_deals = {}
    for d in deals:
        if d.magic != MAGIC_V8:
            continue
        comment = (d.comment or '').strip()
        if d.entry == mt5.DEAL_ENTRY_IN and comment.startswith('BB_'):
            in_deals[d.position_id] = d
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            out_deals[d.position_id] = d

    since_hours = (datetime.now() - timedelta(hours=HOURS)) if HOURS > 0 else None

    trades = []
    for pos_id, out_d in out_deals.items():
        in_d = in_deals.get(pos_id)
        if in_d is None:
            continue

        open_dt = to_dt(in_d.time)

        # v8開始前のトレードを除外
        if open_dt < V8_START_TIME:
            continue

        # 時間範囲フィルター
        if since_hours and open_dt < since_hours:
            continue

        symbol    = out_d.symbol
        profit    = out_d.profit
        comment   = (in_d.comment or '').strip()
        lot       = out_d.volume
        direction = 1 if in_d.type == mt5.DEAL_TYPE_BUY else -1
        entry_px  = in_d.price
        exit_px   = out_d.price
        pip       = pip_size(symbol)
        strategy  = get_strategy(comment)
        rr        = get_rr(strategy)

        # TP/SL価格取得
        tp_px = sl_px = 0.0
        orders = mt5.history_orders_get(position=pos_id)
        if orders:
            for o in orders:
                if o.tp and o.tp != 0.0:
                    tp_px = o.tp
                if o.sl and o.sl != 0.0:
                    sl_px = o.sl

        # TP想定損益
        if tp_px > 0.0 and entry_px > 0.0:
            tp_expected = abs(tp_px - entry_px) / pip * lot * 100
        elif profit < 0:
            tp_expected = abs(profit) * rr
        else:
            tp_expected = 0.0

        result     = classify(profit, tp_expected)
        profit_pip = round((exit_px - entry_px) * direction / pip, 1)
        hold_min   = int((out_d.time - in_d.time) / 60)

        trades.append({
            'pos_id':     pos_id,
            'symbol':     symbol,
            'direction':  direction,
            'lot':        lot,
            'profit':     profit,
            'profit_pip': profit_pip,
            'tp_expected':tp_expected,
            'result':     result,
            'win':        profit > 0,
            'open_time':  open_dt,
            'close_time': to_dt(out_d.time),
            'hold_min':   hold_min,
            'comment':    comment,
        })

    trades.sort(key=lambda x: x['close_time'])
    return trades

# ══════════════════════════════════════════
# 統計
# ══════════════════════════════════════════
def calc_stats(trades: list) -> dict:
    if not trades:
        return {}
    profits  = [t['profit'] for t in trades]
    tp_list  = [t for t in trades if t['result'] == 'TP']
    tr_list  = [t for t in trades if t['result'] == 'TRAIL']
    sl_list  = [t for t in trades if t['result'] == 'SL']
    wins     = [t for t in trades if t['win']]
    losses   = [t for t in trades if not t['win']]
    n        = len(trades)

    win_rate   = len(wins) / n * 100
    avg_win    = np.mean([t['profit'] for t in wins])   if wins   else 0
    avg_loss   = np.mean([t['profit'] for t in losses]) if losses else 0
    real_rr    = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    be_winrate = 1 / (1 + real_rr) * 100 if real_rr > 0 else 50.0
    ev_ok      = win_rate > be_winrate

    sharpe = 0.0
    if len(profits) > 1 and np.std(profits) > 0:
        sharpe = round(np.mean(profits) / np.std(profits) * np.sqrt(252), 2)

    cum = peak = max_dd = 0
    for p in profits:
        cum   += p
        peak   = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        'n': n,
        'tp': len(tp_list), 'trail': len(tr_list), 'sl': len(sl_list),
        'tp_rate':    round(len(tp_list) / n * 100, 1),
        'trail_rate': round(len(tr_list) / n * 100, 1),
        'sl_rate':    round(len(sl_list) / n * 100, 1),
        'win_rate':   round(win_rate, 1),
        'total':      round(sum(profits)),
        'avg':        round(np.mean(profits)),
        'avg_tp':     round(np.mean([t['profit'] for t in tp_list])) if tp_list else 0,
        'avg_trail':  round(np.mean([t['profit'] for t in tr_list])) if tr_list else 0,
        'avg_sl':     round(np.mean([t['profit'] for t in sl_list])) if sl_list else 0,
        'avg_hold':   round(np.mean([t['hold_min'] for t in trades])),
        'real_rr':    round(real_rr, 2),
        'be_winrate': round(be_winrate, 1),
        'ev_ok':      ev_ok,
        'sharpe':     sharpe,
        'max_dd':     round(max_dd),
    }

def show(s: dict):
    if not s:
        print('  データなし'); return
    ev = '✅ プラス期待値' if s['ev_ok'] else '❌ マイナス期待値'
    print(f'  件数: {s["n"]}件  '
          f'(TP:{s["tp"]} {s["tp_rate"]}% | Trail:{s["trail"]} {s["trail_rate"]}% | SL:{s["sl"]} {s["sl_rate"]}%)')
    print(f'  広義勝率: {s["win_rate"]}%  実RR: {s["real_rr"]}  '
          f'損益分岐勝率: {s["be_winrate"]}%  {ev}')
    print(f'  期待値/件: {s["avg"]:+,}円  Sharpe: {s["sharpe"]}  平均保有: {s["avg_hold"]}分')
    print(f'  損益合計: {s["total"]:+,}円  最大DD: {s["max_dd"]:,}円')
    print(f'  平均TP益: {s["avg_tp"]:+,}円  平均Trail益: {s["avg_trail"]:+,}円  平均SL損: {s["avg_sl"]:+,}円')

def sep(title):
    print('\n' + '='*65)
    print(f'  {title}')
    print('='*65)

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    period_label = f'直近{HOURS}時間' if HOURS > 0 else 'v8開始以降の全件'
    print(f'BB v8限定分析 [{period_label}]')
    print(f'v8開始基準: {V8_START_TIME}  magic={MAGIC_V8}')

    if not mt5.initialize():
        print('MT5初期化失敗'); return

    account = mt5.account_info()
    if account is None:
        print('口座情報取得失敗'); mt5.shutdown(); return

    print('='*65)
    print(f'MT5: {account.company}  残高: {round(account.balance):,}円  '
          f'有効証拠金: {round(account.equity):,}円')
    print('='*65)

    trades = fetch_trades()

    if not trades:
        print(f'\n対象取引なし')
        print(f'確認: magic={MAGIC_V8} / comment=BB_SYMBOL / open >= {V8_START_TIME}')
        mt5.shutdown(); return

    print(f'\nv8対象取引数: {len(trades)}件')
    print(f'期間: {trades[0]["open_time"].strftime("%m/%d %H:%M")} 〜 '
          f'{trades[-1]["close_time"].strftime("%m/%d %H:%M")}')

    # 1. 全体統計
    sep('1. 全体統計（v8限定）')
    all_s = calc_stats(trades)
    show(all_s)

    # 2. ペア別
    sep('2. ペア別実績 vs バックテスト比較')
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t['symbol']].append(t)

    print(f'  {"ペア":<10} {"取引":>4} {"TP":>4} {"Trail":>5} {"SL":>4} '
          f'{"TP率":>6} {"BT勝率":>7} {"差分":>6} {"累計損益":>10}')
    print('  ' + '-'*68)
    for pair in sorted(by_pair.keys()):
        s     = calc_stats(by_pair[pair])
        bt_wr = BT_WINRATE.get(pair, 0)
        diff  = s['win_rate'] - bt_wr
        emoji = '✅' if diff >= -5 else ('⚠️ ' if diff >= -15 else '❌')
        bt_str = f'{bt_wr:.1f}%' if bt_wr else '  n/a '
        print(f'  {pair:<10} {s["n"]:>4}回 {s["tp"]:>4} {s["trail"]:>5} {s["sl"]:>4} '
              f'{s["tp_rate"]:>5.1f}% {bt_str:>7} {diff:>+5.1f}%  {emoji} '
              f'{s["total"]:>+10,}円')

    # 3. BUY/SELL別
    sep('3. BUY/SELL別統計')
    for direction, label in [(1,'BUY'),(-1,'SELL')]:
        dt = [t for t in trades if t['direction'] == direction]
        if dt:
            print(f'\n■ {label} ({len(dt)}件)')
            show(calc_stats(dt))

    # 4. 時間帯別
    sep('4. 時間帯別TP到達率')
    hour_data = defaultdict(lambda: {'n':0,'tp':0,'profit':0.0})
    for t in trades:
        h = t['open_time'].hour
        hour_data[h]['n']      += 1
        hour_data[h]['profit'] += t['profit']
        if t['result'] == 'TP':
            hour_data[h]['tp'] += 1

    print(f'  {"時刻":>5} {"bar":<10} {"件数":>4} {"TP率":>6} {"損益":>10}')
    for h in sorted(hour_data.keys()):
        d    = hour_data[h]
        tp_r = d['tp'] / d['n'] * 100
        bar  = '█' * int(tp_r / 10)
        flag = '🔥' if tp_r >= 40 else ('⚠️' if tp_r < 20 and d['n'] >= 3 else '')
        print(f'  {h:>4}時  {bar:<10} {d["n"]:>3}件  {tp_r:>5.1f}%  {d["profit"]:>+10,.0f}円  {flag}')

    # 5. SLクールダウン検出
    sep(f'5. SLクールダウン検出（{COOLDOWN_MINUTES}分以内再エントリー）')
    sl_times  = defaultdict(list)
    cooldowns = []
    for t in sorted(trades, key=lambda x: x['open_time']):
        sym = t['symbol']
        for sl_t in sl_times[sym]:
            gap = (t['open_time'] - sl_t).total_seconds() / 60
            if 0 < gap <= COOLDOWN_MINUTES:
                cooldowns.append({
                    'symbol': sym, 'sl_time': sl_t,
                    'entry_time': t['open_time'],
                    'gap_min': round(gap, 1), 're_result': t['result'],
                })
        if t['result'] == 'SL':
            sl_times[sym].append(t['close_time'])

    if cooldowns:
        print(f'  ⚠️ {len(cooldowns)}件検出（v8クールダウン機能が未効果または新たなケース）:')
        for w in cooldowns[:10]:
            re_label = {'TP':'✅勝','TRAIL':'△引分','SL':'❌負'}.get(w['re_result'],'?')
            print(f'  {w["symbol"]} SL:{w["sl_time"].strftime("%m/%d %H:%M")} → '
                  f'再IN:{w["entry_time"].strftime("%H:%M")} ({w["gap_min"]}分後) {re_label}')
        if len(cooldowns) > 10:
            print(f'  ... 他{len(cooldowns)-10}件')
    else:
        print('  ✅ クールダウン違反なし（v8クールダウン機能が正常動作中）')
    # 5.5 特定ペア詳細分析
    sep('5.5 ペア別詳細統計')
    TARGET_PAIRS = ['USDJPY', 'USDCAD', 'EURUSD', 'GBPUSD']
    for pair in TARGET_PAIRS:
        pt = by_pair.get(pair, [])
        if not pt:
            continue
        s = calc_stats(pt)
        print(f'\n■ {pair} ({s["n"]}件)')
        show(s)

        # BUY/SELL別
        for direction, label in [(1,'BUY'),(-1,'SELL')]:
            dt = [t for t in pt if t['direction'] == direction]
            if dt:
                ds = calc_stats(dt)
                print(f'  [{label} {len(dt)}件] 勝率:{ds["win_rate"]}% '
                      f'TP:{ds["tp"]} Trail:{ds["trail"]} SL:{ds["sl"]} '
                      f'合計:{ds["total"]:+,}円')

        # 時間帯別（件数3件以上のみ）
        hd = defaultdict(lambda: {'n':0,'tp':0,'trail':0,'profit':0.0})
        for t in pt:
            h = t['open_time'].hour
            hd[h]['n'] += 1
            hd[h]['profit'] += t['profit']
            if t['result'] == 'TP':    hd[h]['tp'] += 1
            if t['result'] == 'TRAIL': hd[h]['trail'] += 1

        print(f'  {"時刻":>4} {"件数":>4} {"TP":>4} {"Trail":>5} {"TP率":>6} {"損益":>10}')
        for h in sorted(hd.keys()):
            d = hd[h]
            tp_r = d['tp'] / d['n'] * 100
            print(f'  {h:>3}時 {d["n"]:>4}件 {d["tp"]:>4} {d["trail"]:>5} '
                  f'{tp_r:>5.1f}% {d["profit"]:>+10,.0f}円')

        # avg_exitの近似（Trail익의 TP比率）
        trail_trades = [t for t in pt if t['result'] == 'TRAIL']
        if trail_trades and s['avg_tp'] > 0:
            avg_trail_pct = (s['avg_trail'] / s['avg_tp'] * 100) if s['avg_tp'] else 0
            print(f'  Trail平均exit: TP比 {avg_trail_pct:.1f}%')
            
    # 6. Phase判定
    sep('6. Phase判定（月30万ロードマップ）')
    s = all_s
    print(f'\n  TP到達率: {s["tp_rate"]}%  (Phase1目標: 35%以上)')
    print(f'  件数: {s["n"]}件 '
          f'{"✅ 100件超" if s["n"] >= 100 else f"（Phase1評価にあと{100-s[chr(110)]}件）" if s["n"] < 100 else ""}')

    if s['n'] < 20:
        print('\n  ⏳ データ蓄積中（20件未満）→ 判定保留')
    elif s['tp_rate'] >= 35 and s['n'] >= 100 and s['avg'] > 0:
        print('\n  ✅ Phase1クリア条件を満たしています')
    elif s['tp_rate'] >= 35:
        print(f'\n  ⚠️ TP到達率は目標達成 → 100件到達まで継続観察（現在{s["n"]}件）')
    else:
        print(f'\n  ⚠️ Phase1継続中 → TP到達率あと{35-s["tp_rate"]:.1f}%改善が必要')

    # 7. 直近20件ログ
    sep('7. 直近20件の取引ログ')
    print(f'  {"決済時刻":<14} {"ペア":<10} {"方向":<5} {"損益":>8} {"分類":<10} {"保有":>5}  comment')
    print('  ' + '-'*75)
    for t in trades[-20:]:
        res_lbl = {'TP':'TP到達 ✅','TRAIL':'Trail △','SL':'SL負け ❌'}.get(t['result'],'?')
        print(f'  {t["close_time"].strftime("%m/%d %H:%M"):<14} '
              f'{t["symbol"]:<10} '
              f'{"BUY" if t["direction"]==1 else "SELL":<5} '
              f'{t["profit"]:>+8,.0f}円 '
              f'{res_lbl:<12}'
              f'{t["hold_min"]:>4}分  '
              f'{t["comment"]}')

    mt5.shutdown()
    print(f'\n分析完了: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

if __name__ == '__main__':
    main()