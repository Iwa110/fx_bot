"""
FX 自動発注システム v2
- 15分毎にシグナルチェック・発注
- 日4回サマリーをDiscord送信（7/12/16/21時）
- 利確・損切・損失上限到達時にリアルタイム通知
- 目標年利30%（資金20万円・0.2ロット・最大4ポジション）
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

# ── 設定 ────────────────────────────────────
ENV_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fx_v2_result.json')
LOG_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_log.json')

MAX_POSITIONS  = 4       # 最大同時ポジション数
MAX_LOSS_PCT   = 0.05    # 1日最大損失率（3%）
LOT_SIZE       = 0.2     # 発注ロット数
DEMO_MODE      = True    # Trueの間はデモ口座のみ発注可
SUMMARY_HOURS  = {7, 12, 16, 21}  # サマリー通知時間

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
        print("Webhook未設定")
        return
    data = json.dumps({'content': message}).encode('utf-8')
    req = urllib.request.Request(
        webhook, data=data,
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )
    try:
        ctx = ssl._create_unverified_context()
        urllib.request.urlopen(req, context=ctx)
        print("Discord通知送信完了")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

# ── MT5接続 ───────────────────────────────────
def connect_mt5():
    if not mt5.initialize():
        raise RuntimeError(f"MT5接続失敗: {mt5.last_error()}")
    info = mt5.account_info()
    print(f"MT5接続成功: {info.company} / 残高:{info.balance}")
    if DEMO_MODE and 'demo' not in info.server.lower():
        raise RuntimeError("DEMO_MODEがTrueですが本番口座が検出されました。停止します。")
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

def count_positions():
    return len(get_positions())

# ── ログ管理 ──────────────────────────────────
def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {
        'date': str(date.today()),
        'initial_balance': 0,
        'orders': [],
        'closed': [],
        'daily_loss_stopped': False
    }

def save_log(log: dict):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def reset_log_if_new_day(log: dict, balance: float) -> dict:
    today = str(date.today())
    if log['date'] != today:
        log = {
            'date': today,
            'initial_balance': balance,
            'orders': [],
            'closed': [],
            'daily_loss_stopped': False
        }
        save_log(log)
    if log['initial_balance'] == 0:
        log['initial_balance'] = balance
        save_log(log)
    return log

# ── 1日損失チェック ───────────────────────────
def check_daily_loss(log: dict, webhook: str) -> bool:
    if log['daily_loss_stopped']:
        return False
    info = mt5.account_info()
    initial = log['initial_balance']
    if initial == 0:
        return True
    loss_pct = (initial - info.equity) / initial
    if loss_pct >= MAX_LOSS_PCT:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        msg = f"""【FX Bot】{now}
⛔ **損失上限到達・本日の取引停止**

初期残高: {initial:,.0f}円
現在資産: {info.equity:,.0f}円
損失率: {loss_pct*100:.2f}% （上限{MAX_LOSS_PCT*100:.0f}%）

本日の取引を全て停止しました。"""
        send_discord(msg, webhook)
        log['daily_loss_stopped'] = True
        save_log(log)
        return False
    return True

# ── 決済済みポジションチェック ────────────────
def check_closed_positions(log: dict, webhook: str):
    """前回実行時のポジションが決済されていたら通知"""
    if not log['orders']:
        return

    current_tickets = {p.ticket for p in get_positions()}
    newly_closed = []

    for order in log['orders']:
        if order['ticket'] not in current_tickets:
            newly_closed.append(order)

    if not newly_closed:
        return

    # 取引履歴から損益取得
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals = mt5.history_deals_get(from_date, datetime.now())
    deal_map = {}
    if deals:
        for deal in deals:
            deal_map[deal.order] = deal

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        ticket = order['ticket']
        deal = deal_map.get(ticket)
        if deal:
            profit = deal.profit
            emoji = '✅' if profit >= 0 else '❌'
            reason = '利確' if profit >= 0 else '損切'
            msg = f"""【FX Bot】{now}
{emoji} **{reason}確定**

通貨ペア: {order['symbol']}
方向: {order['direction']}
損益: {'+' if profit >= 0 else ''}{profit:,.0f}円
戦略: {order['strategy']}"""
            send_discord(msg, webhook)
            log['closed'].append({**order, 'profit': profit, 'reason': reason})

    # 決済済みをordersから削除
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders'] = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

# ── シグナル判定 ──────────────────────────────
def check_signals(params: dict) -> list:
    signals = []

    # TRI: EUR/GBP三角裁定
    eurusd = get_price('EURUSD')
    gbpusd = get_price('GBPUSD')
    eurgbp = get_price('EURGBP')

    if eurusd and gbpusd and eurgbp:
        theory = eurusd['mid'] / gbpusd['mid']
        spread = eurgbp['mid'] - theory
        if abs(spread) >= params.get('tri_entry', 0.0022):
            direction = 'sell' if spread > 0 else 'buy'
            signals.append({
                'strategy': 'TRI',
                'symbol': 'EURGBP',
                'direction': direction,
                'entry': eurgbp['ask'] if direction == 'buy' else eurgbp['bid'],
                'tp_dist': params.get('tri_exit', 0.0007),
                'sl_dist': params.get('tri_stop', 0.0055),
                'reason': f"三角裁定乖離={spread:.5f}"
            })

    # MOM: GBPモメンタム
    gbp_rates = mt5.copy_rates_from_pos('GBPUSD', mt5.TIMEFRAME_D1, 0, 11)
    eur_rates = mt5.copy_rates_from_pos('EURUSD', mt5.TIMEFRAME_D1, 0, 11)
    if gbp_rates is not None and eur_rates is not None and len(gbp_rates) >= 11:
        gbp_mom = (gbp_rates[-1]['close'] - gbp_rates[-11]['close']) / gbp_rates[-11]['close']
        eur_mom = (eur_rates[-1]['close'] - eur_rates[-11]['close']) / eur_rates[-11]['close']
        mom_th = params.get('mom_th', 0.01)
        eur_th = params.get('mom_eur_th', 0.005)
        if gbp_mom > mom_th and eur_mom > eur_th:
            direction = 'buy'
        elif gbp_mom < -mom_th and eur_mom < -eur_th:
            direction = 'sell'
        else:
            direction = None
        if direction and gbpusd:
            price = gbpusd['ask'] if direction == 'buy' else gbpusd['bid']
            signals.append({
                'strategy': 'MOM',
                'symbol': 'GBPUSD',
                'direction': direction,
                'entry': price,
                'tp_dist': price * params.get('mom_tp_pct', 0.027),
                'sl_dist': price * params.get('mom_sl_pct', 0.013),
                'reason': f"GBPモメンタム={gbp_mom*100:.2f}%"
            })

    return signals

# ── 重複チェック ──────────────────────────────
def is_duplicate(signal: dict, log: dict) -> bool:
    """同じ戦略・通貨ペアのポジションが既にあれば重複とみなす"""
    for order in log['orders']:
        if order['strategy'] == signal['strategy'] and order['symbol'] == signal['symbol']:
            return True
    return False

# ── 発注 ─────────────────────────────────────
def place_order(signal: dict, log: dict, webhook: str) -> bool:
    symbol    = signal['symbol']
    direction = signal['direction']
    entry     = signal['entry']
    tp_dist   = signal['tp_dist']
    sl_dist   = signal['sl_dist']

    order_type = mt5.ORDER_TYPE_BUY if direction == 'buy' else mt5.ORDER_TYPE_SELL
    tp = round(entry + tp_dist if direction == 'buy' else entry - tp_dist, 5)
    sl = round(entry - sl_dist if direction == 'buy' else entry + sl_dist, 5)

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       LOT_SIZE,
        'type':         order_type,
        'price':        entry,
        'tp':           tp,
        'sl':           sl,
        'deviation':    20,
        'magic':        20240101,
        'comment':      f"FXBot_{signal['strategy']}",
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"発注成功: {symbol} {direction} @ {entry}")
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        dir_jp = '買い' if direction == 'buy' else '売り'
        info = mt5.account_info()

        # 発注通知
        msg = f"""【FX Bot】{now}
🟢 **自動発注完了**

戦略: {signal['strategy']}
通貨ペア: {symbol}
方向: {dir_jp}
エントリー: {entry}
利確(TP): {tp}
損切(SL): {sl}
ロット: {LOT_SIZE}
理由: {signal['reason']}

残高: {info.balance:,.0f}円
現在ポジション: {count_positions()}/{MAX_POSITIONS}"""
        send_discord(msg, webhook)

        # ログ記録
        log['orders'].append({
            'ticket': result.order,
            'strategy': signal['strategy'],
            'symbol': symbol,
            'direction': dir_jp,
            'entry': entry,
            'tp': tp,
            'sl': sl,
            'time': now
        })
        save_log(log)
        return True
    else:
        print(f"発注失敗: {result.retcode} / {result.comment}")
        return False

# ── サマリー通知 ──────────────────────────────
def send_summary(log: dict, webhook: str):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    info = mt5.account_info()
    initial = log['initial_balance']
    pnl_today = info.equity - initial
    positions = get_positions()

    pos_text = ''
    for p in positions:
        dir_jp = '買い' if p.type == 0 else '売り'
        pos_text += f"\n  {p.symbol} {dir_jp} {p.volume}lot 損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"

    closed_text = ''
    total_closed_pnl = sum(c.get('profit', 0) for c in log['closed'])
    if log['closed']:
        closed_text = f"\n本日決済済み: {len(log['closed'])}回 合計{'+' if total_closed_pnl>=0 else ''}{total_closed_pnl:,.0f}円"

    msg = f"""【FX Bot】{now} 日次サマリー

**■ 口座状況**
残高: {info.balance:,.0f}円
資産: {info.equity:,.0f}円
本日損益: {'+' if pnl_today>=0 else ''}{pnl_today:,.0f}円{closed_text}

**■ 保有ポジション（{len(positions)}/{MAX_POSITIONS}）**{pos_text if pos_text else chr(10)+'  なし'}

**■ 本日の発注**
新規発注: {len(log['orders'])}件
損失上限: {'⛔ 停止中' if log['daily_loss_stopped'] else '✅ 正常'}"""

    send_discord(msg, webhook)

# ── メイン ────────────────────────────────────
def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')
    now     = datetime.now()

    # MT5接続
    info = connect_mt5()

    # ログ初期化
    log = load_log()
    log = reset_log_if_new_day(log, info.balance)

    # 決済済みポジションの通知チェック
    check_closed_positions(log, webhook)

    # サマリー通知（7/12/16/21時の実行時）
    if now.hour in SUMMARY_HOURS:
        send_summary(log, webhook)

    # 損失上限チェック
    if not check_daily_loss(log, webhook):
        mt5.shutdown()
        return

    # 最大ポジション数チェック
    if count_positions() >= MAX_POSITIONS:
        print(f"最大ポジション数({MAX_POSITIONS})に達しています。スキップします。")
        mt5.shutdown()
        return

    # パラメーター読み込み
    with open(RESULT_PATH, encoding='utf-8') as f:
        result = json.load(f)
    params = result['best_params']

    # シグナル検知・発注
    signals = check_signals(params)
    executed = 0
    for sig in signals:
        if count_positions() >= MAX_POSITIONS:
            break
        if is_duplicate(sig, log):
            print(f"重複スキップ: {sig['strategy']} {sig['symbol']}")
            continue
        if place_order(sig, log, webhook):
            executed += 1

    if not signals:
        print(f"[{now.strftime('%H:%M')}] シグナルなし")

    mt5.shutdown()

if __name__ == '__main__':
    main()
