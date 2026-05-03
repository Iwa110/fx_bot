"""
deal_mapをposition_idで照合するように修正
bb_monitor.py と daily_trade.py を修正
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

for filename in ['bb_monitor.py', 'daily_trade.py']:
    path = os.path.join(BASE, filename)
    f    = open(path, encoding='utf-8').read()

    # 全パターンを統一してposition_idで照合
    old_patterns = [
        'deal_map  = {d.order: d for d in deals} if deals else {}',
        'deal_map  = {d.order:d for d in deals} if deals else {}',
        'deal_map  = {d.order: d for d in deals if d.entry == 1} if deals else {}',
        'deal_map  = {d.order:d for d in deals if d.entry == 1} if deals else {}',
    ]

    new = 'deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}'

    replaced = False
    for old in old_patterns:
        if old in f:
            f = f.replace(old, new)
            replaced = True
            break

    # deal_map.update行も削除
    f = f.replace(
        '\n    deal_map.update({d.ticket:d for d in deals} if deals else {})', ''
    )

    # 照合キーもticket→position_idに変更（orderから検索している場合も）
    f = f.replace(
        "deal_map.get(order['ticket']) or deal_map.get(order.get('order'))",
        "deal_map.get(order['ticket'])"
    )

    open(path, 'w', encoding='utf-8').write(f)
    status = '修正完了' if replaced else '新パターンで修正'
    print(f'{filename}: {status}')

# 動作確認
import sys
sys.path.insert(0, BASE)
import MetaTrader5 as mt5
import json
from datetime import datetime, date

mt5.initialize()
from_date = datetime(date.today().year, date.today().month, date.today().day)
deals     = mt5.history_deals_get(from_date, datetime.now())
deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

log_path = os.path.join(BASE, 'trade_log.json')
log      = json.load(open(log_path, encoding='utf-8'))

print('\n=== position_id照合テスト ===')
for o in log['orders'][-5:]:
    t   = o['ticket']
    hit = deal_map.get(t)
    print(f"  ticket:{t} symbol:{o['symbol']} → "
          f"{'profit:' + str(hit.profit) if hit else 'miss（未決済）'}")

mt5.shutdown()
