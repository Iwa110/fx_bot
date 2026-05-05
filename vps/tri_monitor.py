"""
TRI戦略専用モニター（5分毎実行）
- EUR/GBP三角裁定の乖離を素早く検知・発注
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ENV_PATH    = os.path.join(BASE_DIR, '.env')
RESULT_PATH = os.path.join(BASE_DIR, 'fx_v2_result.json')
LOG_PATH    = os.path.join(BASE_DIR, 'trade_log.json')

LOT      = 0.1
MAX_POS  = 1
DEMO_MODE = True

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
           headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
    except Exception as e:
        print(f"Discord送信エラー: {e}")

def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date':str(date.today()),'initial_balance':0,
            'orders':[],'closed':[],'daily_loss_stopped':False}

def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def count_tri():
    positions = mt5.positions_get()
    if not positions: return 0
    return sum(1 for p in positions if 'TRI' in p.comment)

def is_duplicate(symbol, log):
    return any(o['strategy']=='TRI' and o['symbol']==symbol for o in log['orders'])

def check_daily_loss(log):
    if log['daily_loss_stopped']: return False
    info    = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0: return True
    return (initial - info.equity) / initial < 0.05

import heartbeat_check as hb
import safe_monitor as sm

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK','')

    if not mt5.initialize():
        print(f"MT5接続失敗: {mt5.last_error()}")
        return
    hb.record_heartbeat('tri_monitor')

    info = mt5.account_info()
    if DEMO_MODE and 'demo' not in info.server.lower():
        mt5.shutdown(); return

    log = load_log()
    if log['date'] != str(date.today()):
        log = {'date':str(date.today()),'initial_balance':info.balance,
               'orders':[],'closed':[],'daily_loss_stopped':False}
        save_log(log)
    if log['initial_balance'] == 0:
        log['initial_balance'] = info.balance
        save_log(log)

    if not check_daily_loss(log) or count_tri() >= MAX_POS or is_duplicate('EURGBP', log):
        mt5.shutdown(); return

    # パラメーター読み込み
    with open(RESULT_PATH, encoding='utf-8') as f:
        params = json.load(f)['best_params']

    # TRIシグナル判定
    eurusd = mt5.symbol_info_tick('EURUSD')
    gbpusd = mt5.symbol_info_tick('GBPUSD')
    eurgbp = mt5.symbol_info_tick('EURGBP')
    if not (eurusd and gbpusd and eurgbp):
        mt5.shutdown(); return

    mid_eu = (eurusd.bid + eurusd.ask) / 2
    mid_gu = (gbpusd.bid + gbpusd.ask) / 2
    mid_eg = (eurgbp.bid + eurgbp.ask) / 2
    theory = mid_eu / mid_gu
    spread = mid_eg - theory
    tri_entry = params.get('tri_entry', 0.0007)  # 22pips→7pipsに変更（取引頻度改善）

    if abs(spread) < tri_entry:
        print(f"[{datetime.now().strftime('%H:%M')}] TRI乖離={spread:.5f}（閾値{tri_entry}未満）")
        mt5.shutdown(); return

    direction  = 'sell' if spread > 0 else 'buy'
    order_type = mt5.ORDER_TYPE_SELL if direction=='sell' else mt5.ORDER_TYPE_BUY
    entry      = eurgbp.bid if direction=='sell' else eurgbp.ask
    tp_dist    = params.get('tri_exit', 0.0007)
    sl_dist    = params.get('tri_stop', 0.0055)
    tp = round(entry - tp_dist if direction=='sell' else entry + tp_dist, 5)
    sl = round(entry + sl_dist if direction=='sell' else entry - sl_dist, 5)

    result = mt5.order_send({
        'action': mt5.TRADE_ACTION_DEAL, 'symbol': 'EURGBP',
        'volume': LOT, 'type': order_type, 'price': entry,
        'tp': tp, 'sl': sl, 'deviation': 20, 'magic': 20240108,
        'comment': 'FXBot_TRI', 'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    })

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        dir_jp = '売り' if direction=='sell' else '買い'
        send_discord(
            f"【FX Bot TRI】{now}\n🟢 **三角裁定 発注完了**\n\n"
            f"EUR/GBP {dir_jp}\nエントリー: {entry}\nTP: {tp} / SL: {sl}\n"
            f"乖離: {spread:.5f}（理論値との差）", webhook
        )
        log['orders'].append({'ticket':result.order,'strategy':'TRI',
            'symbol':'EURGBP','direction':dir_jp,'entry':entry,'tp':tp,'sl':sl,'time':now})
        save_log(log)
        print(f"TRI発注成功: {dir_jp} @ {entry}")
    else:
        print(f"TRI発注失敗: {result.retcode} / {result.comment}")

    mt5.shutdown()

if __name__ == '__main__':
    main()
