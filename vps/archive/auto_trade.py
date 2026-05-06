"""
FX 自動発注システム v5
- 戦略：TRI・MOM_USDJPY・MOM_GBPJPY・CORR・STR
- MOM戦略をGBP/USD → USD/JPY + GBP/JPYに変更
- 最大ポジション：6（各戦略1つ）
- 資金配分：TRI/MOM/CORR 0.1ロット・STR 0.05ロット
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

# ── 設定 ────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ENV_PATH    = os.path.join(BASE_DIR, '.env')
RESULT_PATH = os.path.join(BASE_DIR, 'fx_v2_result.json')
LOG_PATH    = os.path.join(BASE_DIR, 'trade_log.json')

# 戦略別設定
STRATEGY_CONFIG = {
    'TRI':      {'lot': 0.1,  'max_pos': 1},
    'MOM_JPY':  {'lot': 0.1,  'max_pos': 1},  # USD/JPY
    'MOM_GBJ':  {'lot': 0.1,  'max_pos': 1},  # GBP/JPY
    'CORR':     {'lot': 0.1,  'max_pos': 1},
    'STR':      {'lot': 0.05, 'max_pos': 1},
}
MAX_TOTAL_POS = 6
MAX_LOSS_PCT  = 0.05
DEMO_MODE     = True
SUMMARY_HOURS = {7, 12, 16, 21}

# MOM最適パラメーター（バックテスト済み）
MOM_USDJPY_PARAMS = {
    'mom_period': 10, 'mom_th': 0.01, 'filter_th': 0.005,
    'tp_pips': 80, 'sl_pips': 30, 'pip': 0.01,
}
MOM_GBPJPY_PARAMS = {
    'mom_period': 10, 'mom_th': 0.01, 'filter_th': 0.005,
    'tp_pips': 120, 'sl_pips': 30, 'pip': 0.01,
}

# CORR最適パラメーター
CORR_PARAMS = {
    'corr_window': 60, 'z_entry': 2.5, 'z_exit': 0.3,
    'sl_pct': 0.02, 'rr': 1.5,
}

# STRパラメーター
STRENGTH_PARAMS = {'lookback': 5, 'hold_period': 5, 'min_spread': 0.015}
STRENGTH_TICKERS = {
    'EURUSD':('EUR','USD'), 'GBPUSD':('GBP','USD'),
    'AUDUSD':('AUD','USD'), 'USDJPY':('USD','JPY'),
    'EURGBP':('EUR','GBP'), 'USDCAD':('USD','CAD'),
    'USDCHF':('USD','CHF'), 'NZDUSD':('NZD','USD'),
    'EURJPY':('EUR','JPY'), 'GBPJPY':('GBP','JPY'),
}
TRADE_PAIRS = ['EURUSD','GBPUSD','AUDUSD','USDJPY','EURGBP',
               'USDCAD','USDCHF','NZDUSD','EURJPY','GBPJPY']

# ── .env読み込み ──────────────────────────────
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

# ── Discord通知 ───────────────────────────────
def send_discord(message: str, webhook: str):
    if not webhook:
        return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(
        webhook, data=data,
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )
    try:
        ctx = ssl._create_unverified_context()
        urllib.request.urlopen(req, context=ctx)
        print("Discord通知完了")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

# ── MT5接続 ───────────────────────────────────
def connect_mt5():
    if not mt5.initialize():
        raise RuntimeError(f"MT5接続失敗: {mt5.last_error()}")
    info = mt5.account_info()
    print(f"MT5接続成功: {info.company} / 残高:{info.balance}")
    if DEMO_MODE and 'demo' not in info.server.lower():
        raise RuntimeError("DEMO_MODEがTrueですが本番口座が検出されました。")
    return info

# ── 価格取得 ──────────────────────────────────
def get_price(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return {'bid': tick.bid, 'ask': tick.ask, 'mid': (tick.bid + tick.ask) / 2}

# ── ポジション管理 ────────────────────────────
def get_positions():
    positions = mt5.positions_get()
    return list(positions) if positions else []

def count_by_strategy(strategy: str) -> int:
    return sum(1 for p in get_positions() if strategy in p.comment)

def count_total() -> int:
    return len(get_positions())

# ── ログ管理 ──────────────────────────────────
def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}

def save_log(log: dict):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def reset_log_if_new_day(log: dict, balance: float) -> dict:
    today = str(date.today())
    if log['date'] != today:
        log = {'date': today, 'initial_balance': balance,
               'orders': [], 'closed': [], 'daily_loss_stopped': False}
        save_log(log)
    if log['initial_balance'] == 0:
        log['initial_balance'] = balance
        save_log(log)
    return log

# ── 損失チェック ──────────────────────────────
def check_daily_loss(log: dict, webhook: str) -> bool:
    if log['daily_loss_stopped']:
        return False
    info    = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0:
        return True
    loss_pct = (initial - info.equity) / initial
    if loss_pct >= MAX_LOSS_PCT:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        send_discord(
            f"【FX Bot】{now}\n⛔ **損失上限({MAX_LOSS_PCT*100:.0f}%)到達・本日停止**\n"
            f"損失額: {(initial-info.equity):,.0f}円", webhook
        )
        log['daily_loss_stopped'] = True
        save_log(log)
        return False
    return True

# ── 決済済みポジション通知 ────────────────────
def check_closed_positions(log: dict, webhook: str):
    if not log['orders']:
        return
    current_tickets = {p.ticket for p in get_positions()}
    newly_closed    = [o for o in log['orders'] if o['ticket'] not in current_tickets]
    if not newly_closed:
        return

    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.order: d for d in deals} if deals else {}
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')

    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        send_discord(
            f"【FX Bot】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\n戦略: {order['strategy']}",
            webhook
        )
        log['closed'].append({**order, 'profit': profit, 'reason': reason})

    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

# ── 重複チェック ──────────────────────────────
def is_duplicate(strategy: str, symbol: str, log: dict) -> bool:
    return any(o['strategy'] == strategy and o['symbol'] == symbol
               for o in log['orders'])

# ── 発注共通処理 ──────────────────────────────
def place_order(symbol, direction, lot, tp_dist, sl_dist,
                strategy, reason, log, webhook) -> bool:
    tick = mt5.symbol_info_tick(symbol)
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
        'magic':        20240101,
        'comment':      f"FXBot_{strategy}",
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    })

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        now    = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction == 'buy' else '売り'
        info   = mt5.account_info()
        send_discord(
            f"【FX Bot】{now}\n🟢 **自動発注完了**\n\n"
            f"戦略: {strategy}\n通貨ペア: {symbol}\n方向: {dir_jp}\n"
            f"エントリー: {entry}\nTP: {tp} / SL: {sl}\nロット: {lot}\n"
            f"理由: {reason}\n\n"
            f"残高: {info.balance:,.0f}円\n"
            f"ポジション: {count_total()}/{MAX_TOTAL_POS}",
            webhook
        )
        log['orders'].append({
            'ticket': result.order, 'strategy': strategy,
            'symbol': symbol, 'direction': dir_jp,
            'entry': entry, 'tp': tp, 'sl': sl, 'time': now
        })
        save_log(log)
        print(f"発注成功: {strategy} {symbol} {dir_jp}")
        return True
    else:
        print(f"発注失敗: {result.retcode} / {result.comment}")
        return False

# ── TRI戦略シグナル ───────────────────────────
def check_tri_signal(params: dict) -> list:
    eurusd = get_price('EURUSD')
    gbpusd = get_price('GBPUSD')
    eurgbp = get_price('EURGBP')
    if not (eurusd and gbpusd and eurgbp):
        return []
    theory = eurusd['mid'] / gbpusd['mid']
    spread = eurgbp['mid'] - theory
    if abs(spread) < params.get('tri_entry', 0.0022):
        return []
    direction = 'sell' if spread > 0 else 'buy'
    return [{'strategy':'TRI','symbol':'EURGBP','direction':direction,
             'lot':STRATEGY_CONFIG['TRI']['lot'],
             'tp_dist':params.get('tri_exit',0.0007),
             'sl_dist':params.get('tri_stop',0.0055),
             'reason':f"三角裁定乖離={spread:.5f}"}]

# ── MOM_JPY シグナル（USD/JPY）────────────────
def check_mom_jpy_signal() -> list:
    p         = MOM_USDJPY_PARAMS
    usdjpy    = get_price('USDJPY')
    jpy_rates = mt5.copy_rates_from_pos('USDJPY', mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    eur_rates = mt5.copy_rates_from_pos('EURJPY', mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    if not (usdjpy and jpy_rates is not None and eur_rates is not None
            and len(jpy_rates) > p['mom_period']):
        return []

    mom  = (jpy_rates[-1]['close'] - jpy_rates[-p['mom_period']-1]['close']) / jpy_rates[-p['mom_period']-1]['close']
    fmom = (eur_rates[-1]['close'] - eur_rates[-p['mom_period']-1]['close']) / eur_rates[-p['mom_period']-1]['close']

    if mom > p['mom_th'] and fmom > p['filter_th']:
        direction = 'buy'
    elif mom < -p['mom_th'] and fmom < -p['filter_th']:
        direction = 'sell'
    else:
        return []

    tp_dist = p['tp_pips'] * p['pip']
    sl_dist = p['sl_pips'] * p['pip']
    return [{'strategy':'MOM_JPY','symbol':'USDJPY','direction':direction,
             'lot':STRATEGY_CONFIG['MOM_JPY']['lot'],
             'tp_dist':tp_dist,'sl_dist':sl_dist,
             'reason':f"USD/JPYモメンタム={mom*100:.2f}%"}]

# ── MOM_GBJ シグナル（GBP/JPY）───────────────
def check_mom_gbj_signal() -> list:
    p         = MOM_GBPJPY_PARAMS
    gbpjpy    = get_price('GBPJPY')
    gbj_rates = mt5.copy_rates_from_pos('GBPJPY', mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    jpy_rates = mt5.copy_rates_from_pos('USDJPY', mt5.TIMEFRAME_D1, 0, p['mom_period']+2)
    if not (gbpjpy and gbj_rates is not None and jpy_rates is not None
            and len(gbj_rates) > p['mom_period']):
        return []

    mom  = (gbj_rates[-1]['close'] - gbj_rates[-p['mom_period']-1]['close']) / gbj_rates[-p['mom_period']-1]['close']
    fmom = (jpy_rates[-1]['close'] - jpy_rates[-p['mom_period']-1]['close']) / jpy_rates[-p['mom_period']-1]['close']

    if mom > p['mom_th'] and fmom > p['filter_th']:
        direction = 'buy'
    elif mom < -p['mom_th'] and fmom < -p['filter_th']:
        direction = 'sell'
    else:
        return []

    tp_dist = p['tp_pips'] * p['pip']
    sl_dist = p['sl_pips'] * p['pip']
    return [{'strategy':'MOM_GBJ','symbol':'GBPJPY','direction':direction,
             'lot':STRATEGY_CONFIG['MOM_GBJ']['lot'],
             'tp_dist':tp_dist,'sl_dist':sl_dist,
             'reason':f"GBP/JPYモメンタム={mom*100:.2f}%"}]

# ── CORR戦略シグナル ──────────────────────────
def check_corr_signal() -> list:
    win     = CORR_PARAMS['corr_window']
    aud_rates = mt5.copy_rates_from_pos('AUDUSD', mt5.TIMEFRAME_D1, 0, win+5)
    nzd_rates = mt5.copy_rates_from_pos('NZDUSD', mt5.TIMEFRAME_D1, 0, win+5)
    if aud_rates is None or nzd_rates is None or len(aud_rates) < win:
        return []

    aud    = [r['close'] for r in aud_rates]
    nzd    = [r['close'] for r in nzd_rates]
    ratios = [a/n for a,n in zip(aud,nzd)]
    mean   = sum(ratios)/len(ratios)
    std    = (sum((r-mean)**2 for r in ratios)/len(ratios))**0.5
    if std == 0:
        return []
    zscore = (ratios[-1]-mean)/std
    if abs(zscore) < CORR_PARAMS['z_entry']:
        return []

    direction = 'sell' if zscore > 0 else 'buy'
    audusd    = get_price('AUDUSD')
    if not audusd:
        return []
    entry   = audusd['ask'] if direction=='buy' else audusd['bid']
    sl_dist = entry * CORR_PARAMS['sl_pct']
    tp_dist = sl_dist * CORR_PARAMS['rr']
    return [{'strategy':'CORR','symbol':'AUDUSD','direction':direction,
             'lot':STRATEGY_CONFIG['CORR']['lot'],
             'tp_dist':tp_dist,'sl_dist':sl_dist,
             'reason':f"AUD/NZD Zスコア={zscore:.2f}"}]

# ── STR戦略シグナル ───────────────────────────
def check_str_signal() -> list:
    lb     = STRENGTH_PARAMS['lookback']
    scores = {}
    for symbol, (base, quote) in STRENGTH_TICKERS.items():
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, lb+5)
        if rates is None or len(rates) < lb+1:
            continue
        closes = [r['close'] for r in rates]
        ret    = (closes[-1]-closes[-lb])/closes[-lb]
        scores[base]  = scores.get(base,0) + ret
        scores[quote] = scores.get(quote,0) - ret

    if not scores:
        return []

    sc       = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    strongest = sc[0][0]; weakest = sc[-1][0]
    spread    = sc[0][1]-sc[-1][1]
    if spread < STRENGTH_PARAMS['min_spread']:
        return []

    best_pair = None; best_score = -99
    for symbol in TRADE_PAIRS:
        if symbol not in STRENGTH_TICKERS:
            continue
        base, quote = STRENGTH_TICKERS[symbol]
        score = 0
        if base==strongest: score += scores.get(strongest,0)
        if quote==weakest:  score += abs(scores.get(weakest,0))
        if base==weakest:   score -= abs(scores.get(weakest,0))
        if quote==strongest:score -= scores.get(strongest,0)
        if score > best_score:
            best_score = score; best_pair = symbol

    if not best_pair or best_score <= 0:
        return []

    base, quote = STRENGTH_TICKERS[best_pair]
    direction   = 'buy' if scores.get(base,0)>scores.get(quote,0) else 'sell'
    price       = get_price(best_pair)
    if not price:
        return []

    atr_rates = mt5.copy_rates_from_pos(best_pair, mt5.TIMEFRAME_D1, 0, 15)
    if atr_rates is None:
        return []
    trs = [max(atr_rates[i]['high']-atr_rates[i]['low'],
               abs(atr_rates[i]['high']-atr_rates[i-1]['close']),
               abs(atr_rates[i]['low'] -atr_rates[i-1]['close']))
           for i in range(1, len(atr_rates))]
    atr_val = sum(trs[-14:])/14 if len(trs)>=14 else sum(trs)/len(trs)

    return [{'strategy':'STR','symbol':best_pair,'direction':direction,
             'lot':STRATEGY_CONFIG['STR']['lot'],
             'tp_dist':atr_val*STRENGTH_PARAMS['hold_period']*0.5,
             'sl_dist':atr_val*1.5,
             'reason':f"最強:{strongest} 最弱:{weakest} スプレッド:{spread:.3f}"}]

# ── サマリー通知 ──────────────────────────────
def send_summary(log: dict, webhook: str):
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    info      = mt5.account_info()
    initial   = log['initial_balance']
    pnl       = info.equity - initial
    positions = get_positions()

    pos_text = ''
    for p in positions:
        strategy = p.comment.replace('FXBot_','')
        dir_jp   = '買い' if p.type==0 else '売り'
        pos_text += f"\n  [{strategy}] {p.symbol} {dir_jp} {p.volume}lot " \
                    f"損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"

    total_closed = sum(c.get('profit',0) for c in log['closed'])
    closed_text  = (f"\n本日決済: {len(log['closed'])}回 "
                    f"合計{'+' if total_closed>=0 else ''}{total_closed:,.0f}円"
                    if log['closed'] else '')

    strategy_lines = '\n'.join(
        f"  {s}: {count_by_strategy(s)}/{cfg['max_pos']}"
        for s, cfg in STRATEGY_CONFIG.items()
    )

    send_discord(
        f"【FX Bot】{now} 日次サマリー\n\n"
        f"**■ 口座状況**\n残高: {info.balance:,.0f}円\n資産: {info.equity:,.0f}円\n"
        f"本日損益: {'+' if pnl>=0 else ''}{pnl:,.0f}円{closed_text}\n\n"
        f"**■ 保有ポジション（{len(positions)}/{MAX_TOTAL_POS}）**"
        f"{pos_text if pos_text else chr(10)+'  なし'}\n\n"
        f"**■ 戦略別ポジション**\n{strategy_lines}\n\n"
        f"取引状態: {'⛔ 停止中' if log['daily_loss_stopped'] else '✅ 稼働中'}",
        webhook
    )

# ── メイン ────────────────────────────────────
def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK','')
    now     = datetime.now()

    info = connect_mt5()
    log  = load_log()
    log  = reset_log_if_new_day(log, info.balance)

    check_closed_positions(log, webhook)

    if now.hour in SUMMARY_HOURS:
        send_summary(log, webhook)

    if not check_daily_loss(log, webhook):
        mt5.shutdown()
        return

    if count_total() >= MAX_TOTAL_POS:
        print(f"合計ポジション上限({MAX_TOTAL_POS})到達。スキップ。")
        mt5.shutdown()
        return

    # パラメーター読み込み
    with open(RESULT_PATH, encoding='utf-8') as f:
        result = json.load(f)
    params = result['best_params']

    # 全戦略シグナル収集
    all_signals = []
    all_signals.extend(check_tri_signal(params))
    all_signals.extend(check_mom_jpy_signal())
    all_signals.extend(check_mom_gbj_signal())
    all_signals.extend(check_corr_signal())
    all_signals.extend(check_str_signal())

    executed = 0
    for sig in all_signals:
        strategy = sig['strategy']
        cfg      = STRATEGY_CONFIG[strategy]
        if count_total() >= MAX_TOTAL_POS:
            break
        if count_by_strategy(strategy) >= cfg['max_pos']:
            continue
        if is_duplicate(strategy, sig['symbol'], log):
            continue
        if place_order(sig['symbol'], sig['direction'], sig['lot'],
                       sig['tp_dist'], sig['sl_dist'],
                       strategy, sig['reason'], log, webhook):
            executed += 1

    if not all_signals:
        print(f"[{now.strftime('%H:%M')}] 全戦略シグナルなし")
    else:
        print(f"[{now.strftime('%H:%M')}] シグナル{len(all_signals)}件 / 発注{executed}件")

    mt5.shutdown()

if __name__ == '__main__':
    main()
