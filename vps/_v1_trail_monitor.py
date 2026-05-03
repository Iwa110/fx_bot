"""
トレーリングストップ常駐モニター
- 30秒毎にBBポジションのSLを更新
- MT5接続を維持したままループ
- VPS起動時にタスクスケジューラで1回だけ起動
"""
import MetaTrader5 as mt5
import json, os, ssl, time, urllib.request
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trade_log.json')

TRAIL_INTERVAL      = 30    # 更新間隔（秒）
TRAIL_ACTIVATE_MULT = 0.5   # ATR×0.5以上の利益で発動（早めに追随）
TRAIL_DISTANCE_MULT = 1.5   # 現在価格からATR×1.5をSLに設定
DEMO_MODE           = True

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
        try:
            with open(LOG_PATH, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}

def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def get_atr(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
    if rates is None or len(rates) < 2:
        return None
    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]
    trs    = [max(highs[i]-lows[i],
                  abs(highs[i]-closes[i-1]),
                  abs(lows[i] -closes[i-1]))
              for i in range(1, len(rates))]
    return sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)

def update_trailing_stops(webhook):
    positions = mt5.positions_get()
    if not positions:
        return 0

    log     = load_log()
    updated = 0

    for p in positions:
        if 'BB_' not in p.comment:
            continue

        symbol    = p.symbol
        is_jpy    = 'JPY' in symbol
        direction = 1 if p.type == 0 else -1
        entry     = p.price_open
        pip       = 0.01 if is_jpy else 0.0001

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            continue
        current = tick.bid if direction == 1 else tick.ask

        atr = get_atr(symbol)
        if not atr:
            continue

        # 発動条件：利益がATR×TRAIL_ACTIVATE_MULT以上
        profit_dist   = (current - entry) * direction
        activate_dist = atr * TRAIL_ACTIVATE_MULT

        if profit_dist < activate_dist:
            continue

        # 新しいSL = 現在価格 - ATR×TRAIL_DISTANCE_MULT
        trail_dist = atr * TRAIL_DISTANCE_MULT
        new_sl     = round(current - trail_dist * direction, 5)

        # 現在のSLより有利な場合のみ更新
        current_sl = p.sl
        if direction == 1:
            if new_sl <= current_sl:
                continue
        else:
            if new_sl >= current_sl:
                continue

        result = mt5.order_send({
            'action':   mt5.TRADE_ACTION_SLTP,
            'symbol':   symbol,
            'position': p.ticket,
            'tp':       p.tp,
            'sl':       new_sl,
        })

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            sl_move    = abs(new_sl - current_sl) / pip
            profit_pip = profit_dist / pip
            now        = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] トレーリングSL更新: {symbol} "
                  f"{current_sl:.5f}→{new_sl:.5f} ({sl_move:.1f}pips)")

            # ログのSLも更新
            for o in log['orders']:
                if o['ticket'] == p.ticket:
                    o['sl'] = new_sl
            save_log(log)
            updated += 1
        else:
            print(f"SL更新失敗: {symbol} {result.retcode} {result.comment}")

    return updated

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')

    print("=" * 50)
    print("トレーリングストップ常駐モニター 起動")
    print(f"更新間隔: {TRAIL_INTERVAL}秒")
    print(f"発動条件: ATR×{TRAIL_ACTIVATE_MULT}以上の利益")
    print(f"SL距離:   現在価格からATR×{TRAIL_DISTANCE_MULT}")
    print("=" * 50)

    # MT5接続（常駐のため一度だけ）
    if not mt5.initialize():
        print("MT5接続失敗")
        return

    info = mt5.account_info()
    print(f"MT5接続成功: {info.company} / 残高:{info.balance:,.0f}円")

    if DEMO_MODE and 'demo' not in info.server.lower():
        print("DEMO_MODEがTrueですが本番口座が検出されました")
        mt5.shutdown()
        return

    import heartbeat_check as hb
    loop_count = 0
    while True:
        try:
            loop_count += 1
            hb.record_heartbeat('trail_monitor')
            now = datetime.now().strftime('%H:%M:%S')

            # 週末はスキップ（土日）
            if datetime.now().weekday() >= 5:
                time.sleep(TRAIL_INTERVAL)
                continue

            updated = update_trailing_stops(webhook)

            # 10分毎に稼働確認ログ
            if loop_count % 20 == 0:
                positions = mt5.positions_get()
                bb_count  = sum(1 for p in positions if 'BB_' in p.comment) if positions else 0
                print(f"[{now}] 稼働中 | BBポジション:{bb_count}件 | "
                      f"ループ:{loop_count}回")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] エラー: {e}")
            # MT5接続が切れた場合は再接続
            try:
                mt5.shutdown()
                time.sleep(5)
                mt5.initialize()
                print("MT5再接続完了")
            except:
                pass

        time.sleep(TRAIL_INTERVAL)

if __name__ == '__main__':
    main()
