"""
過去のclosedログをMT5実績で上書き修正
"""
import MetaTrader5 as mt5
import json, os
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()

from_date = datetime(2026, 4, 1, 0, 0)
deals     = mt5.history_deals_get(from_date, datetime.now())
deal_map  = {}
if deals:
    for d in deals:
        if d.entry == 1:
            deal_map[d.position_id] = d

log     = json.load(open(LOG_PATH, encoding='utf-8'))
fixed   = 0
skipped = 0

for c in log['closed']:
    if 'BB_' not in c.get('strategy', ''):
        continue
    deal = deal_map.get(c['ticket'])
    if deal:
        old    = c['profit']
        c['profit'] = deal.profit
        c['reason'] = '利確' if deal.profit >= 0 else '損切'
        if old != deal.profit:
            print(f"  修正: {c['strategy']} {c['symbol']} "
                  f"{old:+,.0f}円 → {deal.profit:+,.0f}円")
            fixed += 1
    else:
        skipped += 1

with open(LOG_PATH, 'w', encoding='utf-8') as f:
    json.dump(log, f, ensure_ascii=False, indent=2)

print(f"\n修正: {fixed}件 / deal履歴なし（スキップ）: {skipped}件")

# 修正後の合計
total = sum(c['profit'] for c in log['closed'] if 'BB_' in c.get('strategy',''))
print(f"BB戦略累計損益（修正後）: {total:+,.0f}円")

mt5.shutdown()
