# debug_cooldown.py に追記して再実行
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

mt5.initialize()

symbol = 'GBPUSD'
COOLDOWN_MINUTES = 15

now_utc  = datetime.now(timezone.utc).replace(tzinfo=None)
from_utc = now_utc - timedelta(minutes=COOLDOWN_MINUTES)

deals = mt5.history_deals_get(from_utc, now_utc)
print(f"deals件数 = {len(deals) if deals else 0}")

if deals:
    for d in deals:
        print(f"symbol={d.symbol} magic={d.magic} entry={d.entry} reason={d.reason} time={datetime.utcfromtimestamp(d.time)}")
        print(f"  DEAL_ENTRY_OUT={mt5.DEAL_ENTRY_OUT} DEAL_REASON_SL={mt5.DEAL_REASON_SL}")

mt5.shutdown()