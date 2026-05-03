"""bb_monitor.pyのcheck_closed関数を修正"""
import os, re

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')

f = open(bb_path, encoding='utf-8').read()

# check_closed関数全体を正しい形で置換
old_func = re.search(
    r'def check_closed\(log, webhook\):.*?(?=\ndef )',
    f, re.DOTALL
)
if old_func:
    print(f"既存関数発見: {old_func.start()}〜{old_func.end()}")
else:
    print("関数が見つかりません")

new_func = '''def check_closed(log, webhook):
    if not log['orders']: return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders']
                    if o['ticket'] not in current and 'BB_' in o.get('strategy','')]
    if not newly_closed: return
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {}
    if deals:
        for d in deals:
            deal_map[d.order]  = d
            deal_map[d.ticket] = d
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket']) or deal_map.get(order.get('order'))
        profit = 0
        if deal:
            profit = deal.profit
        elif deals:
            symbol_deals = [d for d in deals if d.symbol == order['symbol'] and d.profit != 0]
            if symbol_deals:
                profit = symbol_deals[-1].profit
        lot    = order.get('lot', '不明')
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        msg = f"【FX Bot BB】{now}\\n{emoji} **{reason}確定**\\n"
        msg += f"通貨ペア: {order['symbol']}\\n"
        msg += f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\\n"
        msg += f"ロット: {lot}"
        send_discord(msg, webhook)
        sl_dist = abs(order.get('entry',0) - order.get('sl',0))
        tp_dist = abs(order.get('entry',0) - order.get('tp',0))
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist, order.get('entry',0))
        log['closed'].append({**order, 'profit': profit, 'reason': reason})
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

'''

# 既存のcheck_closed関数を置換
f_new = re.sub(
    r'def check_closed\(log, webhook\):.*?(?=\ndef )',
    new_func,
    f,
    flags=re.DOTALL
)

open(bb_path, 'w', encoding='utf-8').write(f_new)
print('check_closed関数修正完了')

# 構文チェック
import subprocess
result = subprocess.run(
    ['python', '-m', 'py_compile', bb_path],
    capture_output=True, text=True
)
if result.returncode == 0:
    print('構文チェック: ✅ エラーなし')
else:
    print(f'構文エラー: {result.stderr}')
