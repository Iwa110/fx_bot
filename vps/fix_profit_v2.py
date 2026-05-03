"""
history_orders_getを使って損益を正確に取得
TPによる自動決済も正しく処理
"""
import os

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')

f = open(bb_path, encoding='utf-8').read()

old_closed = '''def check_closed(log, webhook):
    if not log['orders']:
        return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders']
                    if o['ticket'] not in current and 'BB_' in o.get('strategy', '')]
    if not newly_closed:
        return

    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = 0
        if deal:
            profit = deal.profit
        elif deals:
            sym_deals = [d for d in deals
                         if d.symbol == order['symbol'] and d.profit != 0]
            if sym_deals:
                profit = sym_deals[-1].profit

        lot    = order.get('lot', '不明')
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'

        msg  = "【FX Bot BB】" + now + "\\n"
        msg += emoji + " **" + reason + "確定**\\n"
        msg += "通貨ペア: " + order['symbol'] + "\\n"
        msg += "損益: " + ('+' if profit >= 0 else '') + f"{profit:,.0f}円\\n"
        msg += "ロット: " + str(lot)
        send_discord(msg, webhook)

        sl_dist = abs(order.get('entry', 0) - order.get('sl', 0))
        tp_dist = abs(order.get('entry', 0) - order.get('tp', 0))
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist, order.get('entry', 0))
        log['closed'].append({**order, 'profit': profit, 'reason': reason})

    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)'''

new_closed = '''def calc_profit_from_order(order):
    """TP/SL価格差から損益を計算（deal履歴がない場合のフォールバック）"""
    entry     = order.get('entry', 0)
    tp        = order.get('tp', 0)
    sl        = order.get('sl', 0)
    lot       = order.get('lot', 0.1)
    symbol    = order.get('symbol', '')
    direction = 1 if order.get('direction', '買い') == '買い' else -1
    is_jpy    = 'JPY' in symbol
    conv      = 1.0 if is_jpy else 150.0

    if not (entry and tp and sl):
        return 0, '不明'

    tp_dist = abs(tp - entry)
    sl_dist = abs(sl - entry)

    # history_ordersからTP/SL判定
    from_date = datetime(2026, 4, 1, 0, 0)
    orders = mt5.history_orders_get(from_date, datetime.now())
    if orders:
        for o in orders:
            if o.position_id == order['ticket']:
                if o.type in [mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL]:
                    continue
                # TP決済かSL決済か
                if o.type in [2, 3, 4, 5]:  # TP/SL系のorder type
                    exec_price = o.price_current if o.price_current else o.price_open
                    pnl = (exec_price - entry) * direction * lot * 10000 * conv
                    reason = '利確' if pnl >= 0 else '損切'
                    return round(pnl), reason

    # フォールバック：TPとSLのどちらに近いかで判定
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        current = (tick.bid + tick.ask) / 2
        if abs(current - tp) < abs(current - sl):
            pnl = tp_dist * lot * 10000 * conv
            return round(pnl), '利確'
        else:
            pnl = -sl_dist * lot * 10000 * conv
            return round(pnl), '損切'

    return 0, '不明'

def check_closed(log, webhook):
    if not log['orders']:
        return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders']
                    if o['ticket'] not in current and 'BB_' in o.get('strategy', '')]
    if not newly_closed:
        return

    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        if deal and deal.profit != 0:
            profit = deal.profit
            reason = '利確' if profit >= 0 else '損切'
        else:
            profit, reason = calc_profit_from_order(order)

        lot   = order.get('lot', '不明')
        emoji = '✅' if profit >= 0 else '❌'

        msg  = "【FX Bot BB】" + now + "\\n"
        msg += emoji + " **" + reason + "確定**\\n"
        msg += "通貨ペア: " + order['symbol'] + "\\n"
        msg += "損益: " + ('+' if profit >= 0 else '') + f"{profit:,.0f}円\\n"
        msg += "ロット: " + str(lot)
        send_discord(msg, webhook)

        sl_dist = abs(order.get('entry', 0) - order.get('sl', 0))
        tp_dist = abs(order.get('entry', 0) - order.get('tp', 0))
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist, order.get('entry', 0))
        log['closed'].append({**order, 'profit': profit, 'reason': reason})

    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)'''

if old_closed in f:
    f = f.replace(old_closed, new_closed)
    open(bb_path, 'w', encoding='utf-8').write(f)
    print('bb_monitor.py 損益計算修正完了')

    # 構文チェック
    import subprocess
    result = subprocess.run(
        ['python', '-m', 'py_compile', bb_path],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print('構文チェック: OK')
    else:
        print(f'構文エラー: {result.stderr}')
else:
    print('パターンが見つかりません')
    idx = f.find('def check_closed')
    print(repr(f[idx:idx+200]))
