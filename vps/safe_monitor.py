"""
セーフモード監視（daily_trade.py・bb_monitor.pyから呼び出し）
MT5接続断・残高閾値割れ・急激なDD時の緊急停止
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

SAFE_BALANCE_RATIO = 0.70  # 初期残高の70%を下回ったら緊急停止
STOP_FLAG_PATH     = os.path.join(BASE, 'emergency_stop.flag')

def send_discord(message, webhook):
    if not webhook: return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(webhook, data=data,
           headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
    except: pass

def is_emergency_stopped():
    return os.path.exists(STOP_FLAG_PATH)

def set_emergency_stop():
    with open(STOP_FLAG_PATH, 'w') as f:
        f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

def clear_emergency_stop():
    if os.path.exists(STOP_FLAG_PATH):
        os.remove(STOP_FLAG_PATH)

def close_all_positions(webhook):
    positions = mt5.positions_get()
    if not positions: return 0
    closed = 0
    for p in positions:
        tick  = mt5.symbol_info_tick(p.symbol)
        price = tick.bid if p.type == 0 else tick.ask
        close_type = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
        result = mt5.order_send({
            'action':       mt5.TRADE_ACTION_DEAL,
            'symbol':       p.symbol,
            'volume':       p.volume,
            'type':         close_type,
            'position':     p.ticket,
            'price':        price,
            'deviation':    20,
            'magic':        20240101,
            'comment':      'FXBot_SafeMode',
            'type_time':    mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_FOK,
        })
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
    return closed

def check_safe_mode(webhook, initial_balance):
    """
    セーフモード条件チェック
    Returns: True=通常稼働継続 / False=停止
    """
    if is_emergency_stopped():
        return False

    if not mt5.initialize():
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send_discord(f"【FX Bot】{now}\n🔴 MT5接続断を検知", webhook)
        return False

    info = mt5.account_info()

    # 残高チェック：初期残高の70%を下回ったら緊急停止
    if initial_balance > 0:
        ratio = info.balance / initial_balance
        if ratio < SAFE_BALANCE_RATIO:
            now    = datetime.now().strftime('%Y-%m-%d %H:%M')
            closed = close_all_positions(webhook)
            set_emergency_stop()
            send_discord(
                f"【FX Bot】{now}\n🚨 **緊急停止（セーフモード）**\n\n"
                f"残高: {info.balance:,.0f}円\n"
                f"初期残高比: {ratio*100:.1f}%（閾値{SAFE_BALANCE_RATIO*100:.0f}%）\n"
                f"全ポジション決済: {closed}件\n\n"
                f"再開するにはemergency_stop.flagを削除してください",
                webhook
            )
            return False

    return True
