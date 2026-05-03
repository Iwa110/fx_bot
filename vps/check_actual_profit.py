"""
実際の取引履歴と通知損益を比較して問題を特定
"""
import MetaTrader5 as mt5
import json, os
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()

# 全期間のdeal履歴を取得
from_date = datetime(2026, 4, 1, 0, 0)
deals     = mt5.history_deals_get(from_date, datetime.now())

# 決済dealのみ抽出
closed_deals = {}
if deals:
    for d in deals:
        if d.entry == 1:
            closed_deals[d.position_id] = d

# ログのclosedと比較
log = json.load(open(LOG_PATH, encoding='utf-8'))

print("=" * 70)
print("【通知損益 vs MT5実績 比較】")
print("=" * 70)
print(f"{'戦略':<14} {'symbol':<8} {'通知損益':>10} {'MT5実績':>10} {'差分':>8} {'状態'}")
print("-" * 70)

total_notified = 0
total_actual   = 0
mismatch_count = 0

for c in log.get('closed', []):
    if 'BB_' not in c.get('strategy', ''):
        continue
    ticket          = c['ticket']
    notified_profit = c.get('profit', 0)
    deal            = closed_deals.get(ticket)
    actual_profit   = deal.profit if deal else None

    total_notified += notified_profit

    if actual_profit is not None:
        total_actual += actual_profit
        diff  = actual_profit - notified_profit
        state = '✅ 一致' if abs(diff) < 1 else '❌ 不一致'
        if abs(diff) >= 1:
            mismatch_count += 1
        print(f"{c['strategy']:<14} {c['symbol']:<8} "
              f"{notified_profit:>+9,.0f}円 {actual_profit:>+9,.0f}円 "
              f"{diff:>+7,.0f}円 {state}")
    else:
        # dealなし→TP/SL計算で取得
        print(f"{c['strategy']:<14} {c['symbol']:<8} "
              f"{notified_profit:>+9,.0f}円 {'deal履歴なし':>10} {'':>8} ⚠️ フォールバック")

print("=" * 70)
print(f"通知合計損益: {total_notified:+,.0f}円")
print(f"MT5実績合計:  {total_actual:+,.0f}円")
print(f"不一致件数:   {mismatch_count}件")

# MT5口座の実際の損益
info = mt5.account_info()
print(f"\nMT5残高:      {info.balance:,.0f}円")
print(f"MT5資産:      {info.equity:,.0f}円")

mt5.shutdown()
