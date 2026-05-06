"""BB発注スキップ原因調査"""
import MetaTrader5 as mt5
import json, os
from datetime import date

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()
log = json.load(open(LOG_PATH, encoding='utf-8'))

print("=== ordersログ ===")
for o in log['orders']:
    print(f"  {o['strategy']:<14} {o['symbol']} ticket:{o['ticket']}")

print("\n=== 現在のポジション ===")
positions = mt5.positions_get()
if positions:
    for p in positions:
        print(f"  {p.symbol} {p.comment} ticket:{p.ticket}")
else:
    print("  なし")

print("\n=== スキップ判定シミュレーション ===")
BB_PAIRS = ['USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD']
current_tickets = {p.ticket for p in positions} if positions else set()
total_positions = len(positions) if positions else 0

for symbol in BB_PAIRS:
    strategy = "BB_" + symbol

    # ポジション数チェック
    pos_count = sum(1 for p in (positions or []) if strategy in p.comment)
    if pos_count >= 1:
        print(f"  {symbol}: スキップ（ポジション保有中 {pos_count}件）")
        continue

    # 重複チェック（ログ）
    is_dup = any(o['symbol']==symbol and o['strategy']==strategy
                 and o['ticket'] not in current_tickets
                 for o in log['orders'])
    if is_dup:
        orphan = [o for o in log['orders']
                  if o['symbol']==symbol and o['strategy']==strategy
                  and o['ticket'] not in current_tickets]
        print(f"  {symbol}: スキップ（ログに未決済記録あり ticket:{orphan[0]['ticket']}）")
        continue

    # 上限チェック
    if total_positions >= 13:
        print(f"  {symbol}: スキップ（最大ポジション数到達）")
        continue

    print(f"  {symbol}: 発注対象 ✅")

# ログの日付確認
print(f"\n=== ログ日付 ===")
print(f"  ログ日付: {log['date']}")
print(f"  今日:     {str(date.today())}")
if log['date'] != str(date.today()):
    print(f"  ⚠️ 日付が違います！ログが古い可能性")

mt5.shutdown()
