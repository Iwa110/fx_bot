"""
日次戦略スクリプト v3.5（毎朝7時 + 夕方19時実行）
修正点 v3.4→v3.5:
  [CFG] MOM_CONFIG キー名変更: use_ema200→use_ema200_filter, monday_mult→monday_th_mult
  [BT]  MOM_GBJ monday_th_mult: 1.0→1.5 (BT最優PF=1.4458, n=45)
  [FEAT] 夕方再評価モード追加(19時): ポジションなしペアのみ再チェック
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
import json, ssl, urllib.request
from datetime import datetime, date
import risk_manager as rm
import heartbeat_check as hb
import safe_monitor as sm

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
ENV_PATH     = os.path.join(BASE_DIR, '.env')
RESULT_PATH  = os.path.join(BASE_DIR, 'fx_v2_result.json')
LOG_PATH     = os.path.join(BASE_DIR, 'trade_log.json')       # 取引記録JSON
DAILY_LOG    = os.path.join(BASE_DIR, 'daily_log.txt')        # テキストログ（新規追加）

DEMO_MODE     = True
MAX_TOTAL     = 9
SUMMARY_HOURS = {7, 12, 16, 21}

CORR_P = {'corr_window': 60, 'z_entry': 2.0, 'z_exit': 0.0, 'hold_period': 5}  # corr_bt最優 PF=1.924
STR_P  = {'lookback': 10, 'min_spread': 0.015, 'hold_period': 5}  # BT最適: lb=10がPF=1.749(lb=14→10)
STR_TICKERS = {
    'EURUSD': ('EUR', 'USD'), 'GBPUSD': ('GBP', 'USD'), 'AUDUSD': ('AUD', 'USD'),
    'USDJPY': ('USD', 'JPY'), 'EURGBP': ('EUR', 'GBP'), 'USDCAD': ('USD', 'CAD'),
    'USDCHF': ('USD', 'CHF'), 'NZDUSD': ('NZD', 'USD'), 'EURJPY': ('EUR', 'JPY'),
    'GBPJPY': ('GBP', 'JPY'),
}
TRADE_PAIRS = ['EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'EURGBP',
               'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY']

MOM_CONFIG = {
    # BT結果: PF=1.571 n=58  period=10 mom_th=0.015 filter_th=0.005 ema200=False mon=1.5
    'MOM_JPY': {'symbol': 'USDJPY', 'filter_symbol': 'EURJPY',  'is_jpy': True,  'period': 10, 'mom_th': 0.015, 'filter_th': 0.005, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    # BT結果: PF=1.446 n=45  period=7  mom_th=0.015 filter_th=0.002 ema200=False mon=1.5
    'MOM_GBJ': {'symbol': 'GBPJPY', 'filter_symbol': 'USDJPY',  'is_jpy': True,  'period':  7, 'mom_th': 0.015, 'filter_th': 0.002, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    # BT結果: PF=1.150 n=53  period=14 mom_th=0.007 filter_th=0.005 ema200=False mon=1.5 (PF<1.2注意)
    'MOM_ENZ': {'symbol': 'EURNZD', 'filter_symbol': 'EURUSD',  'is_jpy': False, 'period': 14, 'mom_th': 0.007, 'filter_th': 0.005, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    # BT結果: PF=4.109 n=11  period=7  mom_th=0.015 filter_th=0.002 ema200=True  mon=1.5 (n少注意)
    'MOM_ECA': {'symbol': 'EURCAD', 'filter_symbol': 'USDCAD',  'is_jpy': False, 'period':  7, 'mom_th': 0.015, 'filter_th': 0.002, 'use_ema200_filter': True,  'monday_th_mult': 1.5},
    # BT結果: PF=1.427 n=56  period=10 mom_th=0.007 filter_th=0.002 ema200=True  mon=1.0
    'MOM_GBU': {'symbol': 'GBPUSD', 'filter_symbol': 'EURUSD',  'is_jpy': False, 'period': 10, 'mom_th': 0.007, 'filter_th': 0.002, 'use_ema200_filter': True,  'monday_th_mult': 1.0},
}
MAGIC_MAP = {
    'MOM_JPY': 20240101,
    'MOM_GBJ': 20240102,
    'CORR':    20240103,
    'STR':     20240104,
    'MOM_ENZ': 20240105,
    'MOM_ECA': 20240106,
    'MOM_GBU': 20240107,
    'TRI':     20240108,
}
# ══════════════════════════════════════════
# [FIX BUG1] ログ関数（daily_log.txtに出力）
# ══════════════════════════════════════════
def log_print(msg):
    """テキストログ出力（print + ファイル書き込み）"""
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(DAILY_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception as e:
        print(f'[WARN] ログ書き込み失敗: {e}')

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
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
        log_print(f'Discord送信エラー: {e}')

def load_trade_log():
    """[FIX BUG1] 旧load_log()。変数名衝突回避のためtrade_logを返す"""
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}

def save_trade_log(trade_log):
    """[FIX BUG1] 旧save_log()"""
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(trade_log, f, ensure_ascii=False, indent=2)

def get_price(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None
    return {'bid': tick.bid, 'ask': tick.ask, 'mid': (tick.bid + tick.ask) / 2}

def get_positions():
    p = mt5.positions_get()
    return list(p) if p else []

def count_by(strategy):
    return sum(1 for p in get_positions() if strategy in p.comment)

def count_total():
    return len(get_positions())

def is_dup(strategy, symbol, trade_log):
    return any(
        o['strategy'] == strategy and o['symbol'] == symbol
        for o in trade_log['orders']
    )

def check_daily_loss(trade_log, webhook):
    if trade_log['daily_loss_stopped']:
        return False
    info    = mt5.account_info()
    initial = trade_log['initial_balance']
    if initial == 0:
        return True
    if (initial - info.equity) / initial >= 0.05:
        send_discord(
            f"【FX Bot】{datetime.now().strftime('%Y-%m-%d %H:%M')}\n⛔ 損失上限到達・停止",
            webhook
        )
        trade_log['daily_loss_stopped'] = True
        save_trade_log(trade_log)
        return False
    return True

def get_atr(symbol, length=14):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, length + 5)
    if rates is None or len(rates) < length + 1:
        return None
    trs = [
        max(rates[i]['high'] - rates[i]['low'],
            abs(rates[i]['high'] - rates[i - 1]['close']),
            abs(rates[i]['low']  - rates[i - 1]['close']))
        for i in range(1, len(rates))
    ]
    return sum(trs[-length:]) / length

def get_ema(symbol, period=200):
    """[STR_FIX P2] D1 EMA計算（EWM相当: alpha=2/(period+1)）"""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, period + 5)
    if rates is None or len(rates) < period:
        return None
    closes = [r['close'] for r in rates]
    alpha = 2.0 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = alpha * c + (1 - alpha) * ema
    return ema

def check_closed(trade_log, webhook):
    if not trade_log['orders']:
        return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in trade_log['orders'] if o['ticket'] not in current]
    # [STR_FIX P1] newly_closed空でもhold_period判定のため処理継続
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {}
    if deals:
        for d in deals:
            deal_map[d.order]  = d
            deal_map[d.ticket] = d
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        send_discord(
            f"【FX Bot】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit >= 0 else ''}{profit:,.0f}円\n"
            f"戦略: {order['strategy']}\nロット: {order.get('lot', '不明')}",
            webhook
        )
        sl_dist = abs(order['entry'] - order['sl'])   if 'sl'    in order and 'entry' in order else 0
        tp_dist = abs(order['entry'] - order['tp'])   if 'tp'    in order and 'entry' in order else 0
        rm.record_trade(order['strategy'], profit, sl_dist, tp_dist, order.get('entry', 0))
        trade_log['closed'].append({**order, 'profit': profit, 'reason': reason})
    closed_tickets      = {o['ticket'] for o in newly_closed}
    trade_log['orders'] = [o for o in trade_log['orders'] if o['ticket'] not in closed_tickets]

    # [STR_FIX P1] STRのhold_period経過ポジションを強制クローズ
    hold_days     = STR_P['hold_period']
    now_dt        = datetime.now()
    positions_map = {p.ticket: p for p in get_positions()}
    for order in list(trade_log['orders']):
        if order.get('strategy') != 'STR':
            continue
        try:
            entry_dt = datetime.strptime(order['time'], '%Y-%m-%d %H:%M')
        except (ValueError, KeyError):
            continue
        elapsed = (now_dt - entry_dt).days
        if elapsed < hold_days:
            continue
        pos = positions_map.get(order['ticket'])
        if not pos:
            continue
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            log_print(f'[STR_FIX P1] hold_exit: ティック取得失敗 {pos.symbol}')
            continue
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        res = mt5.order_send({
            'action':       mt5.TRADE_ACTION_DEAL,
            'symbol':       pos.symbol,
            'volume':       pos.volume,
            'type':         close_type,
            'position':     pos.ticket,
            'price':        price,
            'deviation':    20,
            'magic':        MAGIC_MAP['STR'],
            'comment':      'FXBot_STR_hold_exit',
            'type_time':    mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        })
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log_print(f'[STR_FIX P1] hold_exit: {pos.symbol} {elapsed}日経過 → クローズ完了')
            send_discord(
                f'【FX Bot】{now_dt.strftime("%Y-%m-%d %H:%M")}\n'
                f'⏱ STR hold_period({hold_days}日)到達: {pos.symbol} クローズ',
                webhook
            )
        else:
            retcode = res.retcode if res else 'None'
            log_print(f'[STR_FIX P1] hold_exit: {pos.symbol} クローズ失敗 retcode={retcode}')

    # CORR: Zスコア回帰決済 + hold_period 強制クローズ
    hold_days_corr = CORR_P['hold_period']
    for order in list(trade_log['orders']):
        if order.get('strategy') != 'CORR':
            continue
        try:
            entry_dt = datetime.strptime(order['time'], '%Y-%m-%d %H:%M')
        except (ValueError, KeyError):
            continue
        pos = positions_map.get(order['ticket'])
        if not pos:
            continue
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            log_print(f'[CORR] ティック取得失敗 {pos.symbol}')
            continue

        elapsed      = (now_dt - entry_dt).days
        should_close = False
        close_reason = ''

        if elapsed >= hold_days_corr:
            should_close = True
            close_reason = f'hold_period({hold_days_corr}日)到達 ({elapsed}日経過)'
        else:
            z = _calc_corr_z()
            if z is not None and abs(z) <= CORR_P['z_exit']:
                should_close = True
                close_reason = f'Zスコア回帰 z={z:+.2f}(<=±{CORR_P["z_exit"]})'

        if not should_close:
            continue

        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        res = mt5.order_send({
            'action':       mt5.TRADE_ACTION_DEAL,
            'symbol':       pos.symbol,
            'volume':       pos.volume,
            'type':         close_type,
            'position':     pos.ticket,
            'price':        price,
            'deviation':    20,
            'magic':        MAGIC_MAP['CORR'],
            'comment':      'FXBot_CORR_exit',
            'type_time':    mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        })
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log_print(f'[CORR] exit: {pos.symbol} {close_reason} → クローズ完了')
            send_discord(
                f'【FX Bot】{now_dt.strftime("%Y-%m-%d %H:%M")}\n'
                f'⏱ CORR {close_reason}: {pos.symbol} クローズ',
                webhook
            )
        else:
            retcode = res.retcode if res else 'None'
            log_print(f'[CORR] exit: {pos.symbol} クローズ失敗 retcode={retcode}')

    save_trade_log(trade_log)

def place_order(symbol, direction, lot, tp_dist, sl_dist,
                strategy, reason, trade_log, webhook):
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
        'magic':        MAGIC_MAP.get(strategy, 20240101),
        'comment':      'FXBot_' + strategy,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,   # OANDA対応: IOC維持
    })
    if result is None:
        log_print(f'発注失敗（resultがNone）: {strategy} {symbol}')
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        now    = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction == 'buy' else '売り'
        info   = mt5.account_info()
        stats  = rm.load_stats().get(strategy, {})
        kelly_note = (
            f"\n現在Kelly: {stats.get('kelly', 0):.3f}"
            if stats.get('total', 0) >= 20
            else f"\n取引記録: {stats.get('total', 0)}/20回（Kelly未算出）"
        )
        send_discord(
            f"【FX Bot】{now}\n🟢 **自動発注完了**\n\n"
            f"戦略: {strategy}\n通貨ペア: {symbol}\n方向: {dir_jp}\n"
            f"エントリー: {entry}\nTP: {tp} / SL: {sl}\n"
            f"ロット: {lot}（残高×1.5%リスク）\n"
            f"理由: {reason}{kelly_note}\n\n"
            f"残高: {info.balance:,.0f}円",
            webhook
        )
        trade_log['orders'].append({
            'ticket': result.order, 'strategy': strategy, 'symbol': symbol,
            'direction': dir_jp, 'entry': entry, 'tp': tp, 'sl': sl,
            'lot': lot, 'time': now
        })
        save_trade_log(trade_log)
        log_print(f'発注成功: {strategy} {symbol} {dir_jp} {lot}lot')
        return True
    log_print(f'発注失敗: {strategy} {symbol} retcode={result.retcode} / {result.comment}')
    return False

# ══════════════════════════════════════════
# シグナル関数
# ══════════════════════════════════════════
def check_mom_unified(cfg, strategy_name=''):
    symbol        = cfg['symbol']
    filter_symbol = cfg['filter_symbol']
    period        = cfg['period']
    mom_th        = cfg['mom_th']
    filter_th     = cfg['filter_th']
    use_ema200_filter = cfg.get('use_ema200_filter', False)
    monday_th_mult    = cfg.get('monday_th_mult', 1.0)

    rates  = mt5.copy_rates_from_pos(symbol,        mt5.TIMEFRAME_D1, 0, period + 2)
    frates = mt5.copy_rates_from_pos(filter_symbol, mt5.TIMEFRAME_D1, 0, period + 2)
    if rates is None or frates is None or len(rates) <= period:
        log_print(f'[DEBUG] {strategy_name} {symbol}: データ取得失敗')
        return None, None

    mom  = (rates[-1]['close']  - rates[-period - 1]['close'])  / rates[-period - 1]['close']
    fmom = (frates[-1]['close'] - frates[-period - 1]['close']) / frates[-period - 1]['close']

    eff_mom_th = mom_th * monday_th_mult if datetime.now().weekday() == 0 else mom_th
    sig = 'BUY' if mom > eff_mom_th and fmom > filter_th else \
          'SELL' if mom < -eff_mom_th and fmom < -filter_th else 'なし'

    log_print(
        f'[DEBUG] {strategy_name} {symbol}/{filter_symbol} '
        f'mom={mom:+.4f}(閾値±{eff_mom_th:.4f}) '
        f'fmom={fmom:+.4f}(閾値±{filter_th}) → {sig}'
    )

    if mom > eff_mom_th and fmom > filter_th:
        direction = 'buy'
    elif mom < -eff_mom_th and fmom < -filter_th:
        direction = 'sell'
    else:
        return None, None

    if use_ema200_filter:
        ema200 = get_ema(symbol, 200)
        if ema200 is not None:
            price = get_price(symbol)
            if price is not None:
                mid = price['mid']
                if direction == 'buy' and mid <= ema200:
                    log_print(f'[DEBUG] {strategy_name} BUY: EMA200除外 ({mid:.5f}<={ema200:.5f})')
                    return None, None
                if direction == 'sell' and mid >= ema200:
                    log_print(f'[DEBUG] {strategy_name} SELL: EMA200除外 ({mid:.5f}>={ema200:.5f})')
                    return None, None
                log_print(f'[DEBUG] {strategy_name} EMA200={ema200:.5f} 現在値={mid:.5f} → OK')

    reason = (f'{symbol}モメンタム mom={mom:+.4f} filter={fmom:+.4f} '
              f'ATR利用 period={period}')
    return direction, reason

def _calc_corr_z():
    """AUDNZDのローリングZスコアを計算して返す（BT準拠: 直近corr_window本を使用）。"""
    win   = CORR_P['corr_window']
    rates = mt5.copy_rates_from_pos('AUDNZD', mt5.TIMEFRAME_D1, 0, win + 5)
    if rates is None or len(rates) < win:
        log_print('[DEBUG] _calc_corr_z AUDNZDデータ取得失敗')
        return None
    closes = [r['close'] for r in rates][-win:]  # 直近win本のみ（BT準拠）
    mean   = sum(closes) / len(closes)
    std    = (sum((c - mean) ** 2 for c in closes) / len(closes)) ** 0.5
    if std == 0:
        log_print('[DEBUG] _calc_corr_z std=0 → スキップ')
        return None
    return (closes[-1] - mean) / std


def check_corr():
    z = _calc_corr_z()
    if z is None:
        return None
    log_print(
        f'[DEBUG] check_corr AUDNZD z={z:+.2f}(閾値±{CORR_P["z_entry"]}) '
        f'→ {"SELL" if z > CORR_P["z_entry"] else "BUY" if z < -CORR_P["z_entry"] else "なし"}'
    )
    if abs(z) < CORR_P['z_entry']:
        return None
    return ('sell' if z > 0 else 'buy'), z

def check_str():
    lb = STR_P['lookback']
    scores = {}
    for sym, (base, quote) in STR_TICKERS.items():
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, lb + 5)
        if rates is None or len(rates) < lb + 1:
            continue
        closes = [r['close'] for r in rates]
        ret = (closes[-1] - closes[-lb]) / closes[-lb]
        scores[base]  = scores.get(base, 0) + ret
        scores[quote] = scores.get(quote, 0) - ret

    if not scores:
        log_print('[DEBUG] check_str: scoresが空（データ取得失敗）')
        return None

    sc       = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    strongest = sc[0][0]
    weakest   = sc[-1][0]
    spread    = sc[0][1] - sc[-1][1]

    log_print(
        f'[DEBUG] check_str 最強={strongest}({sc[0][1]:+.4f}) '
        f'最弱={weakest}({sc[-1][1]:+.4f}) '
        f'spread={spread:.4f}(閾値{STR_P["min_spread"]})'
    )

    if spread < STR_P['min_spread']:
        return None

    best_pair  = None
    best_score = -99
    for sym in TRADE_PAIRS:
        if sym not in STR_TICKERS:
            continue
        base, quote = STR_TICKERS[sym]
        score = 0
        if base  == strongest: score += scores.get(strongest, 0)
        if quote == weakest:   score += abs(scores.get(weakest, 0))
        if base  == weakest:   score -= abs(scores.get(weakest, 0))
        if quote == strongest: score -= scores.get(strongest, 0)
        if score > best_score:
            best_score = score
            best_pair  = sym

    # [STR_FIX P4] スコア正規化: 最強・最弱の絶対値合計で除算
    norm_denom = abs(sc[0][1]) + abs(sc[-1][1])
    if norm_denom > 0 and best_score > 0:
        best_score = best_score / norm_denom
    log_print(f'[STR_FIX P4] 正規化後score={best_score:.4f} denom={norm_denom:.4f}')

    if not best_pair or best_score <= 0:
        log_print('[DEBUG] check_str: 有効なペアなし')
        return None

    base, quote = STR_TICKERS[best_pair]
    direction   = 'buy' if scores.get(base, 0) > scores.get(quote, 0) else 'sell'

    # [STR_FIX P2] HTFトレンドフィルター（D1 EMA200）
    ema200 = get_ema(best_pair, 200)
    if ema200 is None:
        log_print(f'[STR_FIX P2] {best_pair}: EMA200取得失敗 → フィルタースキップ')
    else:
        current_price = get_price(best_pair)
        if current_price is None:
            log_print(f'[STR_FIX P2] {best_pair}: 現在価格取得失敗 → フィルタースキップ')
        else:
            mid = current_price['mid']
            if direction == 'buy' and mid <= ema200:
                log_print(f'[STR_FIX P2] {best_pair} BUY: 現在値{mid:.5f} <= EMA200({ema200:.5f}) → フィルター除外')
                return None
            if direction == 'sell' and mid >= ema200:
                log_print(f'[STR_FIX P2] {best_pair} SELL: 現在値{mid:.5f} >= EMA200({ema200:.5f}) → フィルター除外')
                return None
            log_print(f'[STR_FIX P2] {best_pair} {direction.upper()}: EMA200={ema200:.5f} 現在値={mid:.5f} → OK')

    log_print(f'[DEBUG] check_str: {best_pair} {direction.upper()} score={best_score:.4f}')
    return best_pair, direction, f'最強:{strongest} 最弱:{weakest}'

# ══════════════════════════════════════════
# TRI（EUR/GBP三角裁定）シグナル関数
# ══════════════════════════════════════════
TRI_P = {
    # BT結果: PF=3.272 n=113 entry_th=0.0007 tp_ratio=0.7 sl_th=0.002
    'entry_th': 0.0007,
    'tp_ratio': 0.7,
    'sl_th':    0.002,
}
def check_tri():
    """
    EUR/GBP三角裁定シグナル
    理論値: EURUSD / GBPUSD
    乖離 = 実勢EURGBP - 理論EURGBP
    乖離 > +entry_th → EURGBPが割高 → sell EURGBP
    乖離 < -entry_th → EURGBPが割安 → buy  EURGBP
    """
    eu = get_price('EURUSD')
    gu = get_price('GBPUSD')
    eg = get_price('EURGBP')
    if eu is None or gu is None or eg is None:
        log_print('[DEBUG] check_tri: 価格取得失敗')
        return None

    theory  = eu['mid'] / gu['mid']
    actual  = eg['mid']
    diff    = actual - theory

    log_print(
        f'[DEBUG] check_tri EURUSD={eu["mid"]:.5f} GBPUSD={gu["mid"]:.5f} '
        f'理論EURGBP={theory:.5f} 実勢EURGBP={actual:.5f} '
        f'乖離={diff:+.5f}(閾値±{TRI_P["entry_th"]}) '
        f'→ {"SELL" if diff >= TRI_P["entry_th"] else "BUY" if diff <= -TRI_P["entry_th"] else "なし"}'
    )

    if diff >= TRI_P['entry_th']:
        return 'sell', diff, theory
    if diff <= -TRI_P['entry_th']:
        return 'buy',  diff, theory
    return None
# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    hour = datetime.now().hour
    is_evening = (19 <= hour < 20)
    if is_evening:
        log_print('===== 夕方再評価モード =====')
    log_print(f'===== 日次戦略v3.5 実行開始 =====')

    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')

    # MT5初期化
    if not mt5.initialize(
        login=int(config.get('OANDA_LOGIN', 0)),
        password=config.get('OANDA_PASSWORD', ''),
        server=config.get('OANDA_SERVER', '')
    ):
        # [FIX BUG1] log → log_print
        log_print('MT5初期化失敗: ' + str(mt5.last_error()))
        return

    info = mt5.account_info()
    if info is None:
        log_print('MT5口座情報取得失敗')
        mt5.shutdown()
        return

    log_print(f'MT5接続成功: {info.server} / 残高:{info.balance:,.0f}円')

    hb.record_heartbeat('daily_trade')

    if DEMO_MODE and 'demo' not in info.server.lower():
        log_print('警告: DEMO_MODE=TrueですがライブサーバーへのMT5接続が検出されました。終了します。')
        mt5.shutdown()
        return

    # [FIX BUG1] log変数 → trade_log変数
    trade_log = load_trade_log()
    today     = str(date.today())
    if trade_log['date'] != today:
        trade_log = {'date': today, 'initial_balance': info.balance,
                     'orders': [], 'closed': [], 'daily_loss_stopped': False}
        save_trade_log(trade_log)
    if trade_log['initial_balance'] == 0:
        trade_log['initial_balance'] = info.balance
        save_trade_log(trade_log)

    if not sm.check_safe_mode(webhook, trade_log.get('initial_balance', 0)):
        mt5.shutdown()
        return

    check_closed(trade_log, webhook)
    if not check_daily_loss(trade_log, webhook):
        mt5.shutdown()
        return

    balance  = info.balance
    executed = 0

    log_print(f'現在ポジション数: {count_total()}/{MAX_TOTAL}')

    # ── MOM戦略（全5ペア統合ループ）──────────────────────────────────
    for strategy_name, cfg in MOM_CONFIG.items():
        symbol = cfg['symbol']
        log_print(f'--- {strategy_name} チェック開始 ---')
        if count_by(strategy_name) >= 1:
            log_print(f'[DEBUG] {strategy_name}: 既存ポジションあり → スキップ')
            continue
        if is_dup(strategy_name, symbol, trade_log):
            log_print(f'[DEBUG] {strategy_name}: ログ重複 → スキップ')
            continue
        if count_total() >= MAX_TOTAL:
            log_print(f'MAX_TOTAL({MAX_TOTAL})到達 → MOMスキップ')
            break

        d, reason = check_mom_unified(cfg, strategy_name)
        if d is None:
            log_print(f'[DEBUG] {strategy_name}: シグナルなし')
            continue

        atr = get_atr(symbol)
        if atr is None:
            log_print(f'[DEBUG] {strategy_name}: ATR取得失敗 → スキップ')
            continue

        tp_dist, sl_dist = rm.calc_tp_sl(atr, strategy_name)
        lot = rm.calc_lot(balance, sl_dist, symbol)
        if place_order(symbol, d, lot, tp_dist, sl_dist,
                       strategy_name, reason, trade_log, webhook):
            executed += 1

    # ── CORR（AUDNZD）──────────────────────────────────────────────
    # [FIX BUG2] 発注ペアをAUDUSD→AUDNZDに修正
    log_print('--- CORR チェック開始 ---')
    if count_by('CORR') < 1 and not is_dup('CORR', 'AUDNZD', trade_log) \
            and count_total() < MAX_TOTAL:
        corr = check_corr()
        if corr:
            d, z = corr
            atr  = get_atr('AUDNZD')   # [FIX] AUDUSDからAUDNZDに変更
            if atr:
                tp_dist, sl_dist = rm.calc_tp_sl(atr, 'CORR')
                lot = rm.calc_lot(balance, sl_dist, 'AUDNZD')
                if place_order('AUDNZD', d, lot, tp_dist, sl_dist, 'CORR',
                               f'AUD/NZD Zスコア={z:.2f}', trade_log, webhook):
                    executed += 1
            else:
                log_print('[DEBUG] CORR: ATR取得失敗')
        else:
            log_print('[DEBUG] CORR: シグナルなし')
    else:
        log_print(f'[DEBUG] CORR: スキップ（既存ポジ={count_by("CORR")} 合計={count_total()}）')

    # ── STR（通貨強弱）─────────────────────────────────────────────
    log_print('--- STR チェック開始 ---')
    if count_by('STR') < 1 and count_total() < MAX_TOTAL:
        str_sig = check_str()
        if str_sig:
            sym, d, reason = str_sig
            if not is_dup('STR', sym, trade_log):
                atr = get_atr(sym)
                if atr:
                    tp_dist, sl_dist = rm.calc_tp_sl(atr, 'STR')
                    lot = rm.calc_lot(balance, sl_dist, sym)
                    if place_order(sym, d, lot, tp_dist, sl_dist, 'STR',
                                   reason, trade_log, webhook):
                        executed += 1
                else:
                    log_print(f'[DEBUG] STR {sym}: ATR取得失敗')
            else:
                log_print(f'[DEBUG] STR: 重複スキップ {sym}')
        else:
            log_print('[DEBUG] STR: シグナルなし')
    else:
        log_print(f'[DEBUG] STR: スキップ（既存ポジ={count_by("STR")} 合計={count_total()}）')

    # ── TRI（EUR/GBP三角裁定）──────────────────────────────────────
    log_print('--- TRI チェック開始 ---')
    if count_by('TRI') < 1 and not is_dup('TRI', 'EURGBP', trade_log) \
            and count_total() < MAX_TOTAL:
        tri = check_tri()
        if tri:
            d, diff, theory = tri
            sl_dist = TRI_P['sl_th']
            tp_dist = abs(diff) * TRI_P['tp_ratio']
            if tp_dist <= 0:
                log_print(f'[DEBUG] TRI: tp_dist={tp_dist:.5f}<=0 → スキップ')
            else:
                lot = rm.calc_lot(balance, sl_dist, 'EURGBP')
                reason = (
                    f'EURGBP三角裁定 乖離={diff:+.5f} '
                    f'理論値={theory:.5f} 実勢={get_price("EURGBP")["mid"]:.5f}'
                )
                if place_order('EURGBP', d, lot, tp_dist, sl_dist, 'TRI',
                               reason, trade_log, webhook):
                    executed += 1
        else:
            log_print('[DEBUG] TRI: シグナルなし')
    else:
        log_print(f'[DEBUG] TRI: スキップ（既存ポジ={count_by("TRI")} 合計={count_total()}）')
    # ── ケリー基準サマリー（週1回月曜日のみ）──────────────────────
    if datetime.now().weekday() == 0:
        rm.print_stats_summary()

    log_print(f'===== 日次戦略v3.5完了: 発注{executed}件 =====')
    mt5.shutdown()

if __name__ == '__main__':
    main()