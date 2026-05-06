"""
トレーリングストップ常駐モニター v2
- 2段階トレーリングSL
  第1段階：スプレッド+手数料回収時点でブレークイーブンSL
  第2段階：ATR×0.5以上の利益でトレーリングSL
- スプレッド動的取得（リアルタイムbid/ask差）
- 最小更新幅設定（ATR×0.1以上でのみ更新）
- MOM・STR戦略にも第2段階を適用
- CORR・TRI・TRIは対象外
"""
import MetaTrader5 as mt5
import json, os, ssl, time, urllib.request
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trade_log.json')

TRAIL_INTERVAL = 30         # 更新間隔（秒）
MIN_UPDATE_MULT = 0.1       # 最小更新幅（ATR×0.1以上でのみ更新）
COMMISSION_PIPS = 0.14      # 往復手数料（pips換算・ブレード口座）
DEMO_MODE       = True

# 戦略別トレーリング設定
TRAIL_CONFIG = {
    'BB_':     {'stage1': True,  'stage2': True,
                'activate': 0.5, 'distance': 1.5},  # BB逆張り
    'MOM_JPY': {'stage1': False, 'stage2': True,
                'activate': 0.8, 'distance': 2.0},  # USD/JPY MOM
    'MOM_GBJ': {'stage1': False, 'stage2': True,
                'activate': 0.8, 'distance': 2.0},  # GBP/JPY MOM
    'STR':     {'stage1': False, 'stage2': True,
                'activate': 1.0, 'distance': 2.0},  # 通貨強弱
    # CORR・TRI はトレーリング対象外
}

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

def load_log():
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}

def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def get_atr(symbol, timeframe=mt5.TIMEFRAME_M5, length=14):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, length + 5)
    if rates is None or len(rates) < 2:
        return None
    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]
    trs    = [max(highs[i]-lows[i],
                  abs(highs[i]-closes[i-1]),
                  abs(lows[i] -closes[i-1]))
              for i in range(1, len(rates))]
    return sum(trs[-length:]) / length if len(trs) >= length else sum(trs) / len(trs)

def get_realtime_spread(symbol):
    """リアルタイムスプレッドを動的取得"""
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return 0
    return tick.ask - tick.bid

def get_trail_config(comment):
    """ポジションのコメントから戦略設定を取得"""
    comment = comment.replace('FXBot_', '')
    for key, cfg in TRAIL_CONFIG.items():
        if comment.startswith(key):
            return cfg
    return None

def update_trailing_stops():
    positions = mt5.positions_get()
    if not positions:
        return 0

    log     = load_log()
    updated = 0

    for p in positions:
        comment = p.comment.replace('FXBot_', '')
        cfg     = get_trail_config(p.comment)
        if not cfg:
            continue  # CORR・TRIはスキップ

        symbol    = p.symbol
        is_jpy    = 'JPY' in symbol
        direction = 1 if p.type == 0 else -1
        entry     = p.price_open
        pip       = 0.01 if is_jpy else 0.0001

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            continue
        current    = tick.bid if direction == 1 else tick.ask
        spread     = get_realtime_spread(symbol)         # 動的スプレッド
        commission = COMMISSION_PIPS * pip               # 手数料

        atr = get_atr(symbol)
        if not atr:
            continue

        profit_dist   = (current - entry) * direction
        min_update    = atr * MIN_UPDATE_MULT            # 最小更新幅
        new_sl        = None
        update_reason = ''

        # ── 第1段階：ATR×0.5以上の利益で完全BEラインにSL設定 ──
        if cfg['stage1']:
            if profit_dist >= atr * 0.5:
                be_sl = round(entry, 5)
                sl_improvement = (be_sl - p.sl) * direction
                if sl_improvement > min_update:
                    new_sl        = be_sl
                    update_reason = "第1段階(BEライン)"
                                    f"≥ATR×0.5 → BEライン設定）")"

        # ── 第2段階：ATR×activate以上でトレーリングSL ──
        if cfg['stage2']:
            activate_dist  = atr * cfg['activate']
            if profit_dist >= activate_dist:
                trail_dist = atr * cfg['distance']
                trail_sl   = round(current - trail_dist * direction, 5)

                # 現在のSLより有利 かつ 最小更新幅以上の場合のみ更新
                current_sl    = p.sl
                sl_improvement = (trail_sl - current_sl) * direction
                if sl_improvement > min_update:
                    # 第1段階より有利な場合のみ上書き
                    if new_sl is None or (trail_sl - new_sl) * direction > 0:
                        new_sl        = trail_sl
                        update_reason = (f"第2段階（利益={profit_dist/pip:.1f}pips"
                                        f"≥ATR×{cfg['activate']}）")

        if new_sl is None:
            continue

        # SL位置の妥当性チェック
        if direction == 1 and new_sl >= current:
            continue
        if direction == -1 and new_sl <= current:
            continue

        result = mt5.order_send({
            'action':   mt5.TRADE_ACTION_SLTP,
            'symbol':   symbol,
            'position': p.ticket,
            'tp':       p.tp,
            'sl':       new_sl,
        })

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            sl_move = abs(new_sl - p.sl) / pip if p.sl != 0 else 0
            now     = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] TrailSL更新: {symbol} {comment} "
                  f"SL {p.sl:.5f}→{new_sl:.5f} "
                  f"(+{sl_move:.1f}pips) [{update_reason}]")

            # ログのSLを更新
            for o in log['orders']:
                if o['ticket'] == p.ticket:
                    o['sl'] = new_sl
            save_log(log)
            updated += 1
        else:
            if result.retcode != 10027:  # 10027=変更なしは無視
                print(f"SL更新失敗: {symbol} {result.retcode} {result.comment}")

    return updated

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')

    print("=" * 55)
    print("トレーリングストップ常駐モニター v2 起動")
    print(f"更新間隔: {TRAIL_INTERVAL}秒")
    print(f"最小更新幅: ATR×{MIN_UPDATE_MULT}")
    print("第1段階: スプレッド+手数料回収でBEライン設定")
    print("第2段階: ATR×0.5以上でトレーリングSL")
    print("対象: BB全ペア / MOM_JPY / MOM_GBJ / STR")
    print("除外: CORR / TRI")
    print("=" * 55)

    if not mt5.initialize():
        print("MT5接続失敗"); return

    info = mt5.account_info()
    print(f"MT5接続成功: {info.company} / 残高:{info.balance:,.0f}円")

    if DEMO_MODE and 'demo' not in info.server.lower():
        print("DEMO_MODEがTrueですが本番口座が検出されました")
        mt5.shutdown(); return

    try:
        import heartbeat_check as hb
    except: hb = None

    loop_count = 0
    while True:
        try:
            loop_count += 1
            if hb:
                hb.record_heartbeat('trail_monitor')

            if datetime.now().weekday() >= 5:
                time.sleep(TRAIL_INTERVAL)
                continue

            updated = update_trailing_stops()

            if loop_count % 20 == 0:
                positions = mt5.positions_get()
                trail_count = 0
                if positions:
                    trail_count = sum(1 for p in positions
                                      if get_trail_config(p.comment))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"稼働中 | トレーリング対象:{trail_count}件 | "
                      f"ループ:{loop_count}回")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] エラー: {e}")
            try:
                mt5.shutdown()
                time.sleep(5)
                mt5.initialize()
                print("MT5再接続完了")
            except: pass

        time.sleep(TRAIL_INTERVAL)

if __name__ == '__main__':
    main()
