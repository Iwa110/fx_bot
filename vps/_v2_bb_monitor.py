"""
BBバンド逆張り専用モニター（5分毎実行）
- 採用8ペア：USD/CAD・GBP/JPY・EUR/JPY・USD/JPY・AUD/JPY・EUR/USD・GBP/USD・EUR/GBP
- スプレッド込みパラメーター適用済み
- 既存MOMと同ペアの場合は合計ロットを管理
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trade_log.json')

DEMO_MODE = True
LOT       = 0.1

# 採用8ペア設定
BB_PAIRS = {
    'USDCAD': {'is_jpy': False, 'pip': 0.0001, 'max_pos': 1},
    'GBPJPY': {'is_jpy': True,  'pip': 0.01,   'max_pos': 1},
    'EURJPY': {'is_jpy': True,  'pip': 0.01,   'max_pos': 1},
    'USDJPY': {'is_jpy': True,  'pip': 0.01,   'max_pos': 1},
    'AUDJPY': {'is_jpy': True,  'pip': 0.01,   'max_pos': 1},
    'EURUSD': {'is_jpy': False, 'pip': 0.0001, 'max_pos': 1},
    'GBPUSD': {'is_jpy': False, 'pip': 0.0001, 'max_pos': 1},
}

# 最適パラメーター（バックテスト済み）
BB_PARAMS = {
    'bb_period':  10,
    'bb_sigma':   1.5,
    'exit_sigma': 0.5,
    'sl_atr':     2.0,
    'rr':         1.5,
}

# MOMと同ペアのペア（合計ロット管理用）
MOM_OVERLAP = {'GBPJPY': 'MOM_GBJ', 'USDJPY': 'MOM_JPY'}

MAX_TOTAL_POS = 13  # 既存6 + BB8

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
    if not webhook: return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(webhook, data=data,
           headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
    except Exception as e:
        print(f"Discord送信エラー: {e}")

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

def count_by_symbol_strategy(symbol, strategy):
    return sum(1 for p in get_positions()
               if p.symbol == symbol and strategy in p.comment)

def count_total():
    return len(get_positions())

def is_dup(symbol, strategy, log):
    return any(o['symbol'] == symbol and o['strategy'] == strategy
               for o in log['orders'])

def check_daily_loss(log, webhook):
    if log['daily_loss_stopped']: return False
    info    = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0: return True
    loss_pct = (initial - info.equity) / initial
    if loss_pct >= 0.05:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send_discord(f"【FX Bot BB】{now}\n⛔ 損失上限到達・本日停止", webhook)
        log['daily_loss_stopped'] = True
        save_log(log)
        return False
    return True

def check_closed(log, webhook):
    if not log['orders']: return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders']
                    if o['ticket'] not in current and 'BB' in o.get('strategy','')]
    if not newly_closed: return
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        send_discord(
            f"【FX Bot BB】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\n戦略: {order['strategy']}",
            webhook
        )
        log['closed'].append({**order, 'profit': profit, 'reason': reason})
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

def calc_bb_signal(symbol, params):
    """BBシグナル計算（5分足）"""
    period   = params['bb_period']
    sigma    = params['bb_sigma']
    sl_atr   = params['sl_atr']
    rr       = params['rr']
    bars_needed = period + 15

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars_needed)
    if rates is None or len(rates) < period + 1:
        return None

    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]

    # ボリンジャーバンド計算
    ma  = sum(closes[-period:]) / period
    std = (sum((c - ma)**2 for c in closes[-period:]) / period) ** 0.5
    upper = ma + sigma * std
    lower = ma - sigma * std

    # ATR計算
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i-1]),
               abs(lows[i] -closes[i-1]))
           for i in range(1, len(rates))]
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)

    current = closes[-1]
    sl_dist = atr * sl_atr
    tp_dist = sl_dist * rr

    if current <= lower:
        return {'direction': 'buy',  'sl_dist': sl_dist, 'tp_dist': tp_dist,
                'ma': ma, 'std': std, 'upper': upper, 'lower': lower}
    elif current >= upper:
        return {'direction': 'sell', 'sl_dist': sl_dist, 'tp_dist': tp_dist,
                'ma': ma, 'std': std, 'upper': upper, 'lower': lower}
    return None

def place_order(symbol, sig, is_jpy, log, webhook):
    direction = sig['direction']
    sl_dist   = sig['sl_dist']
    tp_dist   = sig['tp_dist']
    strategy  = f"BB_{symbol}"

    tick = mt5.symbol_info_tick(symbol)
    if not tick: return False

    order_type = mt5.ORDER_TYPE_BUY if direction == 'buy' else mt5.ORDER_TYPE_SELL
    entry = tick.ask if direction == 'buy' else tick.bid
    tp    = round(entry + tp_dist if direction == 'buy' else entry - tp_dist, 5)
    sl    = round(entry - sl_dist if direction == 'buy' else entry + sl_dist, 5)

    result = mt5.order_send({
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       LOT,
        'type':         order_type,
        'price':        entry,
        'tp':           tp,
        'sl':           sl,
        'deviation':    20,
        'magic':        20240102,
        'comment':      f"FXBot_{strategy}",
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    })

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        now    = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction == 'buy' else '売り'
        info   = mt5.account_info()

        # MOM重複ペアは通知に明記
        overlap_note = ''
        if symbol in MOM_OVERLAP:
            mom_pos = count_by_symbol_strategy(symbol, MOM_OVERLAP[symbol])
            if mom_pos > 0:
                overlap_note = f"\n⚠️ {MOM_OVERLAP[symbol]}と同ペア保有中"

        send_discord(
            f"【FX Bot BB】{now}\n🟢 **BBバンド逆張り 発注**\n\n"
            f"通貨ペア: {symbol}\n方向: {dir_jp}\n"
            f"エントリー: {entry}\nTP: {tp} / SL: {sl}\n"
            f"BB上限: {sig['upper']:.5f} / 下限: {sig['lower']:.5f}{overlap_note}\n\n"
            f"残高: {info.balance:,.0f}円 / "
            f"ポジション: {count_total()}/{MAX_TOTAL_POS}",
            webhook
        )
        log['orders'].append({
            'ticket': result.order, 'strategy': strategy,
            'symbol': symbol, 'direction': dir_jp,
            'entry': entry, 'tp': tp, 'sl': sl, 'time': now
        })
        save_log(log)
        print(f"BB発注成功: {symbol} {dir_jp} @ {entry}")
        return True
    else:
        print(f"BB発注失敗: {symbol} {result.retcode} / {result.comment}")
        return False

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')

    if not mt5.initialize():
        print(f"MT5接続失敗: {mt5.last_error()}")
        return

    info = mt5.account_info()
    if DEMO_MODE and 'demo' not in info.server.lower():
        mt5.shutdown(); return

    log = load_log()
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
        mt5.shutdown(); return

    executed = 0
    skipped  = 0

    for symbol, cfg in BB_PAIRS.items():
        # 既にBBポジションあり or 重複チェック
        if count_by_strategy(f"BB_{symbol}") >= cfg['max_pos']:
            skipped += 1; continue
        if is_dup(symbol, f"BB_{symbol}", log):
            skipped += 1; continue
        if count_total() >= MAX_TOTAL_POS:
            break

        sig = calc_bb_signal(symbol, BB_PARAMS)
        if not sig:
            continue

        if place_order(symbol, sig, cfg['is_jpy'], log, webhook):
            executed += 1

    now = datetime.now().strftime('%H:%M')
    print(f"[{now}] BB監視完了: 発注{executed}件 / スキップ{skipped}件 / "
          f"ポジション{count_total()}/{MAX_TOTAL_POS}")

    mt5.shutdown()

if __name__ == '__main__':
    main()
