"""
損益計算のロット誤りを修正
bb_monitor.pyのget_profit_for_order関数を修正
"""
import os, re

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')

f = open(bb_path, encoding='utf-8').read()

# 現在の誤った計算
# pnl = tp_dist * lot * 10000 * conv  ← lot=0.1 × 10000は誤り
# 正しくは：0.1ロット=1,000通貨なので lot * 10000 = 1000通貨
# ただしMT5のprofitは直接円で返ってくるため
# フォールバック計算は不要→MT5のhistory_ordersから直接price差を使う

old_func = '''def get_profit_for_order(order, deal_map):
    """
    3段階で損益を取得：
    1. deal履歴（position_id照合）
    2. history_ordersのTP/SL決済price
    3. TP/SL距離からの直接計算
    """
    ticket    = order['ticket']
    symbol    = order.get('symbol', '')
    entry     = order.get('entry', 0)
    tp        = order.get('tp', 0)
    sl        = order.get('sl', 0)
    lot       = order.get('lot', 0.1)
    direction = 1 if order.get('direction', '買い') == '買い' else -1
    is_jpy    = 'JPY' in symbol
    conv      = 1.0 if is_jpy else 150.0

    # 1. deal履歴から取得
    deal = deal_map.get(ticket)
    if deal and deal.profit != 0:
        return deal.profit, '利確' if deal.profit >= 0 else '損切'

    # 2. history_ordersから取得
    from_dt = datetime(2026, 4, 1, 0, 0)
    h_orders = mt5.history_orders_get(from_dt, datetime.now())
    if h_orders:
        for ho in h_orders:
            if ho.position_id == ticket and ho.state == 2:  # ORDER_STATE_FILLED
                exec_price = ho.price_current if ho.price_current else ho.price_open
                if exec_price and entry:
                    pnl = (exec_price - entry) * direction * lot * 10000 * conv
                    return round(pnl), '利確' if pnl >= 0 else '損切'

    # 3. TP/SL距離から計算（フォールバック）
    if entry and tp and sl:
        tp_dist = abs(tp - entry)
        sl_dist = abs(sl - entry)
        tick    = mt5.symbol_info_tick(symbol)
        if tick:
            current = (tick.bid + tick.ask) / 2
            if abs(current - tp) < abs(current - sl):
                pnl = tp_dist * lot * 10000 * conv
                return round(pnl), '利確'
            else:
                pnl = -sl_dist * lot * 10000 * conv
                return round(pnl), '損切'

    return 0, '決済\''''

new_func = '''def get_profit_for_order(order, deal_map):
    """
    3段階で損益を取得：
    1. deal履歴（position_id照合）→ MT5が計算した正確な値
    2. history_ordersの決済price → 価格差×通貨量で計算
    3. TP/SL距離からの直接計算（フォールバック）
    """
    ticket    = order['ticket']
    symbol    = order.get('symbol', '')
    entry     = order.get('entry', 0)
    tp        = order.get('tp', 0)
    sl        = order.get('sl', 0)
    lot       = order.get('lot', 0.1)
    direction = 1 if order.get('direction', '買い') == '買い' else -1
    is_jpy    = 'JPY' in symbol
    conv      = 1.0 if is_jpy else 150.0
    # 通貨量：0.1ロット=1,000通貨
    volume    = lot * 10000

    # 1. deal履歴から取得（MT5計算値・最も正確）
    deal = deal_map.get(ticket)
    if deal and deal.profit != 0:
        return deal.profit, '利確' if deal.profit >= 0 else '損切'

    # 2. history_ordersの決済priceから計算
    from_dt  = datetime(2026, 4, 1, 0, 0)
    h_orders = mt5.history_orders_get(from_dt, datetime.now())
    if h_orders:
        for ho in h_orders:
            if ho.position_id == ticket and ho.state == 2:
                exec_price = ho.price_current if ho.price_current else ho.price_open
                if exec_price and entry:
                    # 価格差 × 通貨量 × 円換算
                    pnl = (exec_price - entry) * direction * volume * conv
                    return round(pnl), '利確' if pnl >= 0 else '損切'

    # 3. TP/SL距離から計算（フォールバック）
    if entry and tp and sl:
        tp_dist = abs(tp - entry)
        sl_dist = abs(sl - entry)
        tick    = mt5.symbol_info_tick(symbol)
        if tick:
            current = (tick.bid + tick.ask) / 2
            if abs(current - tp) < abs(current - sl):
                pnl = tp_dist * volume * conv
                return round(pnl), '利確'
            else:
                pnl = -sl_dist * volume * conv
                return round(pnl), '損切'

    return 0, '決済\''''

if old_func in f:
    f = f.replace(old_func, new_func)
    open(bb_path, 'w', encoding='utf-8').write(f)
    print('bb_monitor.py 修正完了')
else:
    print('パターンが見つかりません。現在の関数を確認します:')
    idx = f.find('def get_profit_for_order')
    print(repr(f[idx:idx+500]))

# 構文チェック
import subprocess
result = subprocess.run(
    ['python', '-m', 'py_compile', bb_path],
    capture_output=True, text=True
)
print('構文チェック: ' + ('OK' if result.returncode == 0 else result.stderr))

# 検証：既存のclosedデータで再計算
import MetaTrader5 as mt5, json, sys
sys.path.insert(0, BASE)
import risk_manager as rm

mt5.initialize()
log      = json.load(open(os.path.join(BASE, 'trade_log.json'), encoding='utf-8'))
from_dt  = datetime(2026, 4, 1, 0, 0)
from datetime import datetime
deals    = mt5.history_deals_get(from_dt, datetime.now())
deal_map = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

print('\n=== 修正後の損益検証（直近5件）===')
bb_closed = [c for c in log['closed'] if 'BB_' in c.get('strategy','')]
for c in bb_closed[-5:]:
    deal   = deal_map.get(c['ticket'])
    actual = deal.profit if deal else 'deal履歴なし'
    print(f"  {c['strategy']:<14} 通知:{c['profit']:>+7,.0f}円 MT5:{str(actual):>10}")

mt5.shutdown()
