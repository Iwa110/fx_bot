"""
日次戦略スクリプト（毎朝7時1回実行）
- MOM_JPY（USD/JPY）・MOM_GBJ（GBP/JPY）・CORR・STR
- 日足データを使用するため1日1回で十分
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ENV_PATH    = os.path.join(BASE_DIR, '.env')
RESULT_PATH = os.path.join(BASE_DIR, 'fx_v2_result.json')
LOG_PATH    = os.path.join(BASE_DIR, 'trade_log.json')

DEMO_MODE = True
MAX_TOTAL = 6

STRATEGY_CONFIG = {
    'MOM_JPY': {'lot':0.1,'max_pos':1},
    'MOM_GBJ': {'lot':0.1,'max_pos':1},
    'CORR':    {'lot':0.1,'max_pos':1},
    'STR':     {'lot':0.05,'max_pos':1},
}
MOM_JPY_P = {'mom_period':10,'mom_th':0.01,'filter_th':0.005,'tp_pips':80, 'sl_pips':30,'pip':0.01}
MOM_GBJ_P = {'mom_period':10,'mom_th':0.01,'filter_th':0.005,'tp_pips':120,'sl_pips':30,'pip':0.01}
CORR_P    = {'corr_window':60,'z_entry':2.5,'z_exit':0.3,'sl_pct':0.02,'rr':1.5}
STR_P     = {'lookback':5,'min_spread':0.015,'hold_period':5}
STR_TICKERS = {
    'EURUSD':('EUR','USD'),'GBPUSD':('GBP','USD'),'AUDUSD':('AUD','USD'),
    'USDJPY':('USD','JPY'),'EURGBP':('EUR','GBP'),'USDCAD':('USD','CAD'),
    'USDCHF':('USD','CHF'),'NZDUSD':('NZD','USD'),'EURJPY':('EUR','JPY'),'GBPJPY':('GBP','JPY'),
}
TRADE_PAIRS = ['EURUSD','GBPUSD','AUDUSD','USDJPY','EURGBP',
               'USDCAD','USDCHF','NZDUSD','EURJPY','GBPJPY']

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

def get_price(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return None
    return {'bid':tick.bid,'ask':tick.ask,'mid':(tick.bid+tick.ask)/2}

def get_positions():
    p = mt5.positions_get()
    return list(p) if p else []

def count_by(strategy):
    return sum(1 for p in get_positions() if strategy in p.comment)

def count_total():
    return len(get_positions())

def is_dup(strategy, symbol, log):
    return any(o['strategy']==strategy and o['symbol']==symbol for o in log['orders'])

def check_daily_loss(log, webhook):
    if log['daily_loss_stopped']: return False
    info    = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0: return True
    loss_pct = (initial - info.equity) / initial
    if loss_pct >= 0.05:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send_discord(f"【FX Bot】{now}\n⛔ 損失上限到達・本日停止", webhook)
        log['daily_loss_stopped'] = True
        save_log(log)
        return False
    return True

def check_closed(log, webhook):
    if not log['orders']: return
    current = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders'] if o['ticket'] not in current]
    if not newly_closed: return
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.order:d for d in deals} if deals else {}
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit>=0 else '❌'
        reason = '利確' if profit>=0 else '損切'
        send_discord(
            f"【FX Bot】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\n戦略: {order['strategy']}",
            webhook
        )
        log['closed'].append({**order,'profit':profit,'reason':reason})
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

def place_order(symbol, direction, lot, tp_dist, sl_dist, strategy, reason, log, webhook):
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return False
    order_type = mt5.ORDER_TYPE_BUY if direction=='buy' else mt5.ORDER_TYPE_SELL
    entry = tick.ask if direction=='buy' else tick.bid
    tp    = round(entry+tp_dist if direction=='buy' else entry-tp_dist, 5)
    sl    = round(entry-sl_dist if direction=='buy' else entry+sl_dist, 5)
    result = mt5.order_send({
        'action':mt5.TRADE_ACTION_DEAL,'symbol':symbol,'volume':lot,
        'type':order_type,'price':entry,'tp':tp,'sl':sl,'deviation':20,
        'magic':20240101,'comment':f"FXBot_{strategy}",
        'type_time':mt5.ORDER_TIME_GTC,'type_filling':mt5.ORDER_FILLING_FOK,
    })
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        now    = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction=='buy' else '売り'
        info   = mt5.account_info()
        send_discord(
            f"【FX Bot】{now}\n🟢 **自動発注完了**\n\n"
            f"戦略: {strategy}\n通貨ペア: {symbol}\n方向: {dir_jp}\n"
            f"エントリー: {entry}\nTP: {tp} / SL: {sl}\nロット: {lot}\n"
            f"理由: {reason}\n\n残高: {info.balance:,.0f}円", webhook
        )
        log['orders'].append({'ticket':result.order,'strategy':strategy,
            'symbol':symbol,'direction':dir_jp,'entry':entry,'tp':tp,'sl':sl,'time':now})
        save_log(log)
        print(f"発注成功: {strategy} {symbol} {dir_jp}")
        return True
    print(f"発注失敗: {result.retcode} / {result.comment}")
    return False

# ── シグナル ──────────────────────────────────
def check_mom(symbol, filter_symbol, p):
    rates  = mt5.copy_rates_from_pos(symbol,        mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    frates = mt5.copy_rates_from_pos(filter_symbol, mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    if rates is None or frates is None or len(rates)<=p['mom_period']:
        return None
    mom  = (rates[-1]['close']  - rates[-p['mom_period']-1]['close'])  / rates[-p['mom_period']-1]['close']
    fmom = (frates[-1]['close'] - frates[-p['mom_period']-1]['close']) / frates[-p['mom_period']-1]['close']
    if mom>p['mom_th'] and fmom>p['filter_th']:   return 'buy'
    if mom<-p['mom_th'] and fmom<-p['filter_th']: return 'sell'
    return None

def check_corr():
    win       = CORR_P['corr_window']
    aud_rates = mt5.copy_rates_from_pos('AUDUSD', mt5.TIMEFRAME_D1, 0, win+5)
    nzd_rates = mt5.copy_rates_from_pos('NZDUSD', mt5.TIMEFRAME_D1, 0, win+5)
    if aud_rates is None or nzd_rates is None or len(aud_rates)<win: return None
    aud    = [r['close'] for r in aud_rates]
    nzd    = [r['close'] for r in nzd_rates]
    ratios = [a/n for a,n in zip(aud,nzd)]
    mean   = sum(ratios)/len(ratios)
    std    = (sum((r-mean)**2 for r in ratios)/len(ratios))**0.5
    if std==0: return None
    z = (ratios[-1]-mean)/std
    if abs(z)<CORR_P['z_entry']: return None
    return ('sell' if z>0 else 'buy', z)

def check_str():
    lb     = STR_P['lookback']
    scores = {}
    for sym, (base, quote) in STR_TICKERS.items():
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, lb+5)
        if rates is None or len(rates)<lb+1: continue
        closes = [r['close'] for r in rates]
        ret = (closes[-1]-closes[-lb])/closes[-lb]
        scores[base]  = scores.get(base,0)+ret
        scores[quote] = scores.get(quote,0)-ret
    if not scores: return None
    sc       = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    strongest= sc[0][0]; weakest=sc[-1][0]
    spread   = sc[0][1]-sc[-1][1]
    if spread<STR_P['min_spread']: return None
    best_pair=None; best_score=-99
    for sym in TRADE_PAIRS:
        if sym not in STR_TICKERS: continue
        base,quote = STR_TICKERS[sym]
        score=0
        if base==strongest:  score+=scores.get(strongest,0)
        if quote==weakest:   score+=abs(scores.get(weakest,0))
        if base==weakest:    score-=abs(scores.get(weakest,0))
        if quote==strongest: score-=scores.get(strongest,0)
        if score>best_score: best_score=score; best_pair=sym
    if not best_pair or best_score<=0: return None
    base,quote = STR_TICKERS[best_pair]
    direction  = 'buy' if scores.get(base,0)>scores.get(quote,0) else 'sell'
    atr_rates  = mt5.copy_rates_from_pos(best_pair, mt5.TIMEFRAME_D1, 0, 15)
    if atr_rates is None: return None
    trs = [max(atr_rates[i]['high']-atr_rates[i]['low'],
               abs(atr_rates[i]['high']-atr_rates[i-1]['close']),
               abs(atr_rates[i]['low'] -atr_rates[i-1]['close']))
           for i in range(1,len(atr_rates))]
    atr = sum(trs[-14:])/14 if len(trs)>=14 else sum(trs)/len(trs)
    return (best_pair, direction, atr*STR_P['hold_period']*0.5, atr*1.5,
            f"最強:{strongest} 最弱:{weakest}")

# ── メイン ────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%H:%M')}] 日次戦略実行開始")
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK','')

    if not mt5.initialize():
        print(f"MT5接続失敗"); return

    info = mt5.account_info()
    if DEMO_MODE and 'demo' not in info.server.lower():
        mt5.shutdown(); return

    log = load_log()
    today = str(date.today())
    if log['date'] != today:
        log = {'date':today,'initial_balance':info.balance,
               'orders':[],'closed':[],'daily_loss_stopped':False}
        save_log(log)
    if log['initial_balance']==0:
        log['initial_balance']=info.balance; save_log(log)

    check_closed(log, webhook)

    if not check_daily_loss(log, webhook):
        mt5.shutdown(); return

    executed = 0

    # MOM_JPY（USD/JPY）
    if count_by('MOM_JPY')<1 and not is_dup('MOM_JPY','USDJPY',log) and count_total()<MAX_TOTAL:
        d = check_mom('USDJPY','EURJPY', MOM_JPY_P)
        if d:
            tp = MOM_JPY_P['tp_pips']*MOM_JPY_P['pip']
            sl = MOM_JPY_P['sl_pips']*MOM_JPY_P['pip']
            if place_order('USDJPY',d,0.1,tp,sl,'MOM_JPY','USD/JPYモメンタム',log,webhook):
                executed+=1

    # MOM_GBJ（GBP/JPY）
    if count_by('MOM_GBJ')<1 and not is_dup('MOM_GBJ','GBPJPY',log) and count_total()<MAX_TOTAL:
        d = check_mom('GBPJPY','USDJPY', MOM_GBJ_P)
        if d:
            tp = MOM_GBJ_P['tp_pips']*MOM_GBJ_P['pip']
            sl = MOM_GBJ_P['sl_pips']*MOM_GBJ_P['pip']
            if place_order('GBPJPY',d,0.1,tp,sl,'MOM_GBJ','GBP/JPYモメンタム',log,webhook):
                executed+=1

    # CORR（AUD/NZD）
    if count_by('CORR')<1 and not is_dup('CORR','AUDUSD',log) and count_total()<MAX_TOTAL:
        corr = check_corr()
        if corr:
            d, z   = corr
            price  = get_price('AUDUSD')
            if price:
                entry   = price['ask'] if d=='buy' else price['bid']
                sl_dist = entry*CORR_P['sl_pct']
                tp_dist = sl_dist*CORR_P['rr']
                if place_order('AUDUSD',d,0.1,tp_dist,sl_dist,'CORR',
                               f"AUD/NZD Zスコア={z:.2f}",log,webhook):
                    executed+=1

    # STR（通貨強弱）
    if count_by('STR')<1 and count_total()<MAX_TOTAL:
        str_sig = check_str()
        if str_sig:
            sym, d, tp_dist, sl_dist, reason = str_sig
            if not is_dup('STR',sym,log):
                if place_order(sym,d,0.05,tp_dist,sl_dist,'STR',reason,log,webhook):
                    executed+=1

    print(f"日次戦略完了: 発注{executed}件")
    mt5.shutdown()

if __name__ == '__main__':
    main()
