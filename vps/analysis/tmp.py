import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta

mt5.initialize()
deals = mt5.history_deals_get(
    datetime(2025, 11, 1, tzinfo=timezone.utc),
    datetime.now(tz=timezone.utc)
)
# magic=20240101のdealsのcommentを確認
for d in deals:
    if d.magic == 20240101:
        print(f"ticket={d.ticket} comment='{d.comment}' symbol={d.symbol} profit={d.profit} entry={d.entry}")
mt5.shutdown()