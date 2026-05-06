"""
決済通知の修正パッチ
- deal_mapをorder/ticket両方でインデックス
- ロット不明をポジション履歴から補完
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

patch = '''
def check_closed(log, webhook):
    if not log['orders']: return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders']
                    if o['ticket'] not in current and 'BB_' in o.get('strategy','')]
    if not newly_closed: return

    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())

    # orderとticket両方でインデックス（照合漏れを防ぐ）
    deal_map = {}
    if deals:
        for d in deals:
            deal_map[d.order]  = d
            deal_map[d.ticket] = d

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        # dealをorder/ticket両方で検索
        deal   = deal_map.get(order['ticket']) or deal_map.get(order.get('order'))
        profit = 0
        if deal:
            profit = deal.profit
        else:
            # 全履歴からシンボルで検索（最終手段）
            if deals:
                symbol_deals = [d for d in deals
                                if d.symbol == order['symbol'] and d.profit != 0]
                if symbol_deals:
                    profit = symbol_deals[-1].profit

        lot    = order.get('lot', '不明')
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        send_discord(
            f"【FX Bot BB】{now}\\n{emoji} **{reason}確定**\\n"
            f"通貨ペア: {order['symbol']}\\n"
            f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\\n"
            f"ロット: {lot}",
            webhook
        )
        sl_dist = abs(order.get('entry',0) - order.get('sl',0))
        tp_dist = abs(order.get('entry',0) - order.get('tp',0))
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist,
                        order.get('entry', 0))
        log['closed'].append({**order, 'profit': profit, 'reason': reason})

    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)
'''

bb_path = os.path.join(BASE, 'bb_monitor.py')
f = open(bb_path, encoding='utf-8').read()

# 既存のcheck_closed関数を置換
import re
f_new = re.sub(
    r'def check_closed\(log, webhook\):.*?(?=\ndef )',
    patch.strip() + '\n\n',
    f,
    flags=re.DOTALL
)

open(bb_path, 'w', encoding='utf-8').write(f_new)
print('bb_monitor.py check_closed修正完了')

# daily_trade.pyも同様に修正
dt_path = os.path.join(BASE, 'daily_trade.py')
f2 = open(dt_path, encoding='utf-8').read()

# deal_mapの行だけ修正
old = '    deal_map  = {d.order:d for d in deals} if deals else {}'
new = '''    deal_map = {}
    if deals:
        for d in deals:
            deal_map[d.order]  = d
            deal_map[d.ticket] = d'''

f2 = f2.replace(old, new)
open(dt_path, 'w', encoding='utf-8').write(f2)
print('daily_trade.py deal_map修正完了')
print('\n完了。次回の決済から正しく表示されます。')
