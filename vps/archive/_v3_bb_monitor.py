"""
BBバンド逆張りモニター v3（5分毎実行）
- 損益計算：deal履歴 → history_orders → TP/SL差計算の3段階フォールバック
- 採用7ペア：USD/CAD・GBP/JPY・EUR/JPY・USD/JPY・AUD/JPY・EUR/USD・GBP/USD
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date
import risk_manager as rm
import heartbeat_check as hb
import safe_monitor as sm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trade_log.json')

DEMO_MODE     = True
MAX_TOTAL_POS = 13

BB_PAIRS = {
    'USDCAD': {'is_jpy': False, 'max_pos': 1},
    'GBPJPY': {'is_jpy': True,  'max_pos': 1},
    'EURJPY': {'is_jpy': True,  'max_pos': 1},
    'USDJPY': {'is_jpy': True,  'max_pos': 1},
    'AUDJPY': {'is_jpy': True,  'max_pos': 1},
    'EURUSD': {'is_jpy': False, 'max_pos': 1},
    'GBPUSD': {'is_jpy': False, 'max_pos': 1},
}

BB_PARAMS   = {'bb_period': 10, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 0.83}
MOM_OVERLAP = {'GBPJPY': 'MOM_GBJ', 'USDJPY': 'MOM_JPY'}

def load_env():
    config = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip()
    return config

def send_discord(message, webhook):
    if not webhook:
        return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(
        webhook, data=data,
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
    except Exception as e:
        print("Discord送信エラー: " + str(e))

def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}

def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def get_positions():
    p = mt5.positions_get()
    return list(p) if p else []

def count_by_strategy(strategy):
    return sum(1 for p in get_positions() if strategy in p.comment)

def count_total():
    return len(get_positions())

def is_dup(symbol, strategy, log):
    return any(o['symbol'] == symbol and o['strategy'] == strategy
               for o in log['orders'])

def check_daily_loss(log, webhook):
    if log['daily_loss_stopped']:
        return False
    info    = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0:
        return True
    if (initial - info.equity) / initial >= 0.05:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send_discord("【FX Bot BB】" + now + "\n" + "⛔ 損失上限到達・本日停止", webhook)
        log['daily_loss_stopped'] = True
        save_log(log)
        return False
    return True

def get_profit_for_order(order, deal_map):
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
    volume    = lot * 100000

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

    return 0, '決済'

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
    deal_map  = {}
    if deals:
        for d in deals:
            if d.entry == 1:
                deal_map[d.position_id] = d

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        profit, reason = get_profit_for_order(order, deal_map)
        lot   = order.get('lot', '不明')
        emoji = '✅' if profit >= 0 else '❌'

        # 利確/損切り通知はサマリーに統合（個別通知なし）
        print("決済: " + order['symbol'] + " " + reason + " " + str(profit) + "円")

        sl_dist = abs(order.get('entry', 0) - order.get('sl', 0))
        tp_dist = abs(order.get('entry', 0) - order.get('tp', 0))
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist, order.get('entry', 0))
        log['closed'].append({**order, 'profit': profit, 'reason': reason})

    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

def calc_bb_signal(symbol, is_jpy):
    period      = BB_PARAMS['bb_period']
    sigma       = BB_PARAMS['bb_sigma']
    bars_needed = period + 15
    rates       = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars_needed)
    if rates is None or len(rates) < period + 1:
        return None
    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]
    # 先読み対策：バンドを1本シフト（前足の確定値でバンド計算）
    ma  = sum(closes[-(period+1):-1]) / period
    std = (sum((c - ma) ** 2 for c in closes[-(period+1):-1]) / period) ** 0.5
    if std == 0:
        return None
    upper = ma + sigma * std
    lower = ma - sigma * std
    trs   = [max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i] -closes[i-1]))
             for i in range(1, len(rates))]
    atr     = sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)
    _, sl_dist = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)
    tp_dist    = sl_dist * BB_PARAMS['rr']
    current    = closes[-1]
    if current <= lower:
        return {'direction': 'buy',  'sl_dist': sl_dist, 'tp_dist': tp_dist,
                'atr': atr, 'upper': upper, 'lower': lower}
    elif current >= upper:
        return {'direction': 'sell', 'sl_dist': sl_dist, 'tp_dist': tp_dist,
                'atr': atr, 'upper': upper, 'lower': lower}
    return None

def place_order(symbol, sig, log, webhook):
    strategy  = "BB_" + symbol
    direction = sig['direction']
    sl_dist   = sig['sl_dist']
    tp_dist   = sig['tp_dist']
    is_jpy    = BB_PAIRS[symbol]['is_jpy']
    balance   = mt5.account_info().balance
    lot       = rm.get_kelly_lot(strategy, balance, sl_dist, symbol)
    tick      = mt5.symbol_info_tick(symbol)
    if not tick:
        return False
    order_type = mt5.ORDER_TYPE_BUY if direction == 'buy' else mt5.ORDER_TYPE_SELL
    entry = tick.ask if direction == 'buy' else tick.bid
    tp    = round(entry + tp_dist if direction == 'buy' else entry - tp_dist, 5)
    sl    = round(entry - sl_dist if direction == 'buy' else entry + sl_dist, 5)
    result = mt5.order_send({
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        entry,
        'tp':           tp,
        'sl':           sl,
        'deviation':    20,
        'magic':        20240102,
        'comment':      "FXBot_" + strategy,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    })
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        now    = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction == 'buy' else '売り'
        print("BB発注成功: " + symbol + " " + dir_jp + " " + str(lot) + "lot @ " + str(entry))
        log['orders'].append({
            'ticket':    result.order,
            'strategy':  strategy,
            'symbol':    symbol,
            'direction': dir_jp,
            'entry':     entry,
            'tp':        tp,
            'sl':        sl,
            'lot':       lot,
            'time':      now,
        })
        save_log(log)
        return True
    print("BB発注失敗: " + symbol + " " + str(result.retcode))
    return False

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')
    if not mt5.initialize():
        print("MT5接続失敗")
        return
    info = mt5.account_info()
    if DEMO_MODE and 'demo' not in info.server.lower():
        mt5.shutdown()
        return
    log   = load_log()
    today = str(date.today())
    if log['date'] != today:
        log = {'date': today, 'initial_balance': info.balance,
               'orders': [], 'closed': [], 'daily_loss_stopped': False}
        save_log(log)
    if log['initial_balance'] == 0:
        log['initial_balance'] = info.balance
        save_log(log)
    check_closed(log, webhook)
    if not check_daily_loss(log, webhook):
        mt5.shutdown()
        return
    executed = 0
    skipped  = 0
    for symbol, cfg in BB_PAIRS.items():
        strategy = "BB_" + symbol
        if count_by_strategy(strategy) >= cfg['max_pos']:
            skipped += 1
            continue
        if is_dup(symbol, strategy, log):
            skipped += 1
            continue
        if count_total() >= MAX_TOTAL_POS:
            break
        sig = calc_bb_signal(symbol, cfg['is_jpy'])
        if not sig:
            continue
        # JPYクロス合計ロット制限
        if cfg['is_jpy']:
            jpy_lots = sum(p.volume for p in get_positions()
                          if 'JPY' in p.symbol and 'BB_' in p.comment)
            if jpy_lots >= MAX_JPY_LOT:
                print(f"JPYクロス合計ロット上限({MAX_JPY_LOT}lot)到達: {symbol}スキップ")
                skipped += 1
                continue
        if place_order(symbol, sig, log, webhook):
            executed += 1
    now = datetime.now().strftime('%H:%M')
    print("[" + now + "] BB完了: 発注" + str(executed) + "件 スキップ" + str(skipped) + "件 ポジション" + str(count_total()) + "/" + str(MAX_TOTAL_POS))
    mt5.shutdown()

if __name__ == '__main__':
    main()
