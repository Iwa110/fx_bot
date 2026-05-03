import MetaTrader5 as mt5
from datetime import datetime, date

mt5.initialize()
from_date = datetime(date.today().year, date.today().month, date.today().day)
deals = mt5.history_deals_get(from_date, datetime.now())

print('=== dealの全フィールド確認（決済dealのみ）===')
if deals:
    for d in deals:
        if d.entry == 1:
            print(f"order:{d.order} ticket:{d.ticket} "
                  f"position_id:{d.position_id} symbol:{d.symbol} profit:{d.profit}")
mt5.shutdown()
