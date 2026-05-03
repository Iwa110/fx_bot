import MetaTrader5 as mt5
import json, os
from datetime import datetime, date

mt5.initialize()

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_log.json')
log = json.load(open(log_path, encoding='utf-8'))

print('=== ログのorders（直近5件）===')
for o in log['orders'][-5:]:
    print(f"  ticket:{o['ticket']} symbol:{o['symbol']} strategy:{o.get('strategy','')}")

from_date = datetime(date.today().year, date.today().month, date.today().day)
deals = mt5.history_deals_get(from_date, datetime.now())

print('\n=== 本日の決済deals（entry=1）===')
if deals:
    for d in deals:
        if d.entry == 1:
            print(f"  order:{d.order} ticket:{d.ticket} symbol:{d.symbol} profit:{d.profit}")
else:
    print('  なし')

print('\n=== ticket照合テスト ===')
deal_map = {d.order: d for d in deals if d.entry == 1} if deals else {}
for o in log['orders'][-5:]:
    t = o['ticket']
    hit = deal_map.get(t)
    print(f"  ticket:{t} → {'hit profit:' + str(hit.profit) if hit else 'miss'}")

mt5.shutdown()
