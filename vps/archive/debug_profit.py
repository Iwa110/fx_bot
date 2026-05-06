"""
損益計算のデバッグ：deal.profitと計算値を両方表示
"""
import MetaTrader5 as mt5
import json, os
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()

from_date = datetime(2026, 4, 17, 0, 0)
deals     = mt5.history_deals_get(from_date, datetime.now())
deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

log = json.load(open(LOG_PATH, encoding='utf-8'))
bb_closed = [c for c in log['closed'] if 'BB_' in c.get('strategy','')]

# 直近5件を詳細確認
print("=== 直近5件の損益デバッグ ===")
for c in bb_closed[-5:]:
    ticket    = c['ticket']
    symbol    = c['symbol']
    entry     = c.get('entry', 0)
    tp        = c.get('tp', 0)
    sl        = c.get('sl', 0)
    lot       = c.get('lot', 0.1)
    direction = 1 if c.get('direction','買い') == '買い' else -1
    is_jpy    = 'JPY' in symbol
    conv      = 1.0 if is_jpy else 150.0
    volume    = lot * 10000  # 0.1lot=1000通貨

    deal        = deal_map.get(ticket)
    deal_profit = deal.profit if deal else None

    tp_dist = abs(tp - entry)
    sl_dist = abs(sl - entry)
    calc_tp = tp_dist * volume * conv
    calc_sl = -sl_dist * volume * conv

    notified = c.get('profit', 0)

    print(f"\n{c['strategy']} {symbol}")
    print(f"  lot={lot} volume={volume} conv={conv}")
    print(f"  entry={entry} tp={tp} sl={sl}")
    print(f"  tp_dist={tp_dist:.5f} sl_dist={sl_dist:.5f}")
    print(f"  計算TP利益: {calc_tp:,.0f}円 / 計算SL損失: {calc_sl:,.0f}円")
    print(f"  deal.profit: {deal_profit}")
    print(f"  通知損益:   {notified:+,.0f}円")
    if deal_profit:
        ratio = notified / deal_profit if deal_profit != 0 else 0
        print(f"  比率: {ratio:.2f}倍（1.0が正常）")

mt5.shutdown()
