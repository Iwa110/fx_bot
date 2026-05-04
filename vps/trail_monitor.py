# trail_monitor.py v11
# トレーリングストップ監視スクリプト
#
# 【対象戦略】
#   BB_ / MOM_JPY / MOM_GBJ / STR / SMC_GBPAUD
#
# 【TRAIL_CONFIG パラメータ】
#   stage1          : 損益トントン移動（廃止・実質無効）
#   stage2          : 中間利確ステージ
#   stage2_distance : Stage2のSL位置 entry + ATR*XX（戦略別設定、省略時はSTAGE2_LOCK_DEFAULT）
#   stage3_activate : 利益がSLの何倍になったらトレールを有効化するか
#   stage3_distance : トレールするSL幅（最高値/最安値からのATR倍率）
#
# 【変更履歴】
#   v1  初版
#   v2  戦略別TRAIL_CONFIG追加
#   v3  stage2追加（BB_用）
#   v4  SMC_GBPAUD対応準備、ログ改善
#   v5  MOM_GBJ調整(stage3_activate:1.0->0.8, stage3_distance:1.0->0.7)
#       logfバグ修正(338行目)
#   v6  STR調整(stage3_distance:1.0->0.6, stage3_activate:1.0->0.8, stage2追加)
#       MOM_JPY調整(stage3_distance:1.0->0.8)
#       SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)
#   v7  BB_USDJPY個別設定追加(stage3_distance=0.15)
#       get_trail_config()完全一致優先に修正
#   v8  stage2_distanceをTRAIL_CONFIG戦略別パラメータとして追加
#       STAGE2_LOCK廃止→STAGE2_LOCK_DEFAULT(フォールバック用)
#   v9  stage3_activate=1.2 / stage3_distance=0.8 はBB共通設定（BB_）を継承
#       stage2_distance=0.1（0.2→0.1）
#   v10 STR/MOM_JPYペア別設定分離
#       get_trail_config(strategy, symbol)に変更
#       calc_new_sl/update_trailing_stops/print_heartbeat の呼び出し側を修正
#   v11 stage2_distance updated.

import MetaTrader5 as mt5
import json, os, time, urllib.request, sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trail_log.txt')

TRAIL_INTERVAL   = 30     # ループ間隔（秒）
HEARTBEAT_EVERY  = 10     # この回数ループするたびにハートビート出力（30秒×10=5分）
MIN_UPDATE_MULT  = 0.05   # 最小更新幅 ATR×0.05
COMMISSION_PIPS  = 0.14   # 手数料（片道）
DEMO_MODE        = True

# Stage閾値（全戦略共通）
# Stage1（BEライン）は廃止: SL=entryではスプレッド+手数料分の損失が出るため
STAGE1_ACTIVATE      = 999   # 実質無効（廃止）
STAGE2_ACTIVATE      = 0.7   # ATR×XX以上で小利益確定
STAGE2_LOCK_DEFAULT  = 0.2   # Stage2 SL位置のデフォルト: entry + ATR×0.2
                              # TRAIL_CONFIGにstage2_distanceがあればそちらを優先

# 戦略別設定
# stage2_distance: Stage2でSLを置く位置 = entry + ATR * stage2_distance
#   省略時はSTAGE2_LOCK_DEFAULT(0.2)を使用
#   stage2=Falseの戦略には不要
TRAIL_CONFIG = {
    'BB_':        {'stage2': True,  'stage3_activate': 1.2, 'stage3_distance': 0.8,  'stage2_distance': 0.3},
    'BB_GBPJPY': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 1.0},  # PF=1.02 勝率=38.4% N=276 (旧:0.3 -> 新:1.0, +0.70)
    'BB_USDJPY': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 0.7},  # PF=1.268  勝率=48.6% N=138 (旧:0.3 -> 新:0.7, +0.40)
    'BB_EURUSD': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 0.1},  # PF=0.663  勝率=33.7% N=246 (旧:0.1 -> 新:0.1, 変更なし)
    'BB_GBPUSD': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 1.0},  # PF=0.777  勝率=32.1% N=293 (旧:0.3 -> 新:1.0, +0.70)
    'BB_EURJPY': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 0.7},  # PF=1.041  勝率=44.7% N=284 (旧:0.3 -> 新:0.7, +0.40)
    'BB_AUDJPY': {"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, "stage2_distance": 1.0},  # PF=0.647  勝率=29.8% N=292 (旧:0.3 -> 新:1.0, +0.70)

    'MOM_JPY':          {'stage2': False, 'stage3_activate': 1.0, 'stage3_distance': 0.8},
    'MOM_JPY_USDJPY':   {'stage2': False, 'stage3_activate': 1.0, 'stage3_distance': 0.8},  # 個別上書き用
    'MOM_GBJ':          {'stage2': False, 'stage3_activate': 0.8, 'stage3_distance': 0.7},
    'MOM_GBJ_GBPJPY':   {'stage2': False, 'stage3_activate': 0.8, 'stage3_distance': 0.7},  # 個別上書き用
    'STR':              {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_EURUSD':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_GBPUSD':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_AUDUSD':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_USDJPY':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_EURGBP':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_USDCAD':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_USDCHF':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_NZDUSD':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_EURJPY':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'STR_GBPJPY':       {'stage2': True,  'stage3_activate': 0.8, 'stage3_distance': 0.6,  'stage2_distance': 0.2},
    'SMC_GBPAUD':       {'stage2': False, 'stage3_activate': 1.0, 'stage3_distance': 0.7},
}

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
def log(msg):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def load_env():
    env = {}
    try:
        with open(ENV_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

def send_discord(msg, webhook):
    if not webhook:
        return
    try:
        import urllib.request, json as _json, ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl.CERT_NONE
        data = _json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log('Discord送信エラー: ' + str(e))

# ══════════════════════════════════════════
# MT5ヘルパー
# ══════════════════════════════════════════
def get_atr_5m(symbol, period=14):
    """5分足ATRをEMAで計算"""
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, period + 5)
    if bars is None or len(bars) < period:
        return None
    highs  = [b['high']  for b in bars]
    lows   = [b['low']   for b in bars]
    closes = [b['close'] for b in bars]
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]),
        )
        trs.append(tr)
    atr = trs[0]
    k   = 2.0 / (period + 1)
    for tr in trs[1:]:
        atr = tr * k + atr * (1 - k)
    return atr

def get_trail_config(strategy, symbol=None):
    # ① strategy+symbol の完全一致（最優先）
    if symbol:
        key = f'{strategy}_{symbol}'
        if key in TRAIL_CONFIG:
            return TRAIL_CONFIG[key]
    # ② strategy単体の完全一致
    if strategy in TRAIL_CONFIG:
        return TRAIL_CONFIG[strategy]
    # ③ BB_等の前方一致フォールバック（既存BBロジック互換）
    for key, cfg in TRAIL_CONFIG.items():
        if strategy.startswith(key) and len(key) > 3:
            return cfg
    return None

# ══════════════════════════════════════════
# SL計算コア
# ══════════════════════════════════════════
def calc_new_sl(p, atr, pip):
    """
    2段階SLを計算して(new_sl, stage_label)を返す。
    更新不要の場合は(None, '')を返す。

    優先度: Stage3 > Stage2
    （より有利なステージが常に上書き）
    """
    cfg = get_trail_config(p.comment.replace('FXBot_', ''), p.symbol)
    direction  = 1 if p.type == 0 else -1
    entry      = p.price_open
    current_sl = p.sl

    tick = mt5.symbol_info_tick(p.symbol)
    if tick is None:
        return None, ''
    current = tick.bid if direction == 1 else tick.ask

    profit_dist = (current - entry) * direction
    min_update  = atr * MIN_UPDATE_MULT

    new_sl = None
    stage  = ''

    # ── Stage2: ATR×0.7以上で小利益確定 ────────────────
    if cfg['stage2'] and profit_dist >= atr * STAGE2_ACTIVATE:
        s2_dist   = cfg.get('stage2_distance', STAGE2_LOCK_DEFAULT)
        candidate = round(entry + atr * s2_dist * direction, 5)
        improvement = (candidate - current_sl) * direction
        if improvement > min_update:
            if new_sl is None or (candidate - new_sl) * direction > 0:
                new_sl = candidate
                stage  = 'Stage2(小利益:profit>=ATR*' + str(STAGE2_ACTIVATE) + ',dist=ATR*' + str(s2_dist) + ')'

    # ── Stage3: トレーリング ─────────────────────────────
    # SMC_GBAUDはSell専用（directionが-1以外はスキップ）
    if 'SMC_GBPAUD' in p.comment and direction != -1:
        return None, ''
    if profit_dist >= atr * cfg['stage3_activate']:
        candidate   = round(current - atr * cfg['stage3_distance'] * direction, 5)
        improvement = (candidate - current_sl) * direction
        if improvement > min_update:
            if new_sl is None or (candidate - new_sl) * direction > 0:
                new_sl = candidate
                stage  = 'Stage3(Trail:dist=ATR*' + str(cfg['stage3_distance']) + ')'

    if new_sl is None:
        return None, ''

    # 妥当性チェック
    if direction == 1 and new_sl >= current:
        return None, ''
    if direction == -1 and new_sl <= current:
        return None, ''

    return new_sl, stage

# ══════════════════════════════════════════
# ハートビート表示
# ══════════════════════════════════════════
def print_heartbeat(loop_count, total_updated):
    """5分ごとに稼働状況をターミナルとログファイルに出力"""
    positions = mt5.positions_get()
    pos_lines = []

    if positions:
        for p in positions:
            cfg = get_trail_config(p.comment.replace('FXBot_', ''), p.symbol)
            if cfg is None:
                continue

            direction  = 1 if p.type == 0 else -1
            pip        = 0.01 if 'JPY' in p.symbol else 0.0001
            tick       = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                continue
            current    = tick.bid if direction == 1 else tick.ask
            profit_pip = round((current - p.price_open) * direction / pip, 1)
            atr        = get_atr_5m(p.symbol)
            atr_pips   = round(atr / pip, 1) if atr else 0

            # 現在どのStageにいるか
            if atr:
                pd = (current - p.price_open) * direction
                if pd >= atr * cfg['stage3_activate']:
                    stage_now = 'S3'
                elif cfg['stage2'] and pd >= atr * STAGE2_ACTIVATE:
                    stage_now = 'S2'
                else:
                    stage_now = '--'
            else:
                stage_now = '?'

            sl_pips = round((current - p.sl) * direction / pip, 1) if p.sl != 0 else 0

            pos_lines.append(
                '  ' + p.symbol +
                '(' + ('BUY' if direction == 1 else 'SELL') + ')' +
                ' 含み益:' + ('+' if profit_pip >= 0 else '') + str(profit_pip) + 'pips' +
                ' ATR:' + str(atr_pips) + 'pips' +
                ' SLまで:' + str(sl_pips) + 'pips' +
                ' [' + stage_now + '] ' + p.comment
            )

    account   = mt5.account_info()
    balance   = round(account.balance) if account else 0
    equity    = round(account.equity)  if account else 0
    pos_count = len(pos_lines)

    sep  = '=' * 55
    lines = [
        sep,
        '[HB] ' + datetime.now().strftime('%H:%M') +
        ' | ループ:' + str(loop_count) + '回' +
        ' | 累計SL更新:' + str(total_updated) + '件',
        '[HB] 残高:' + str(balance) + '円 / 有効証拠金:' + str(equity) + '円',
        '[HB] トレーリング対象: ' + str(pos_count) + '件',
    ] + pos_lines + [sep]

    output = '\n'.join(lines)
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print('[' + ts + ']\n' + output)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('[' + ts + ']\n' + output + '\n')
    except Exception:
        pass
    try:
        sys.path.insert(0, BASE_DIR)
        import heartbeat_check as hb_mod
        hb_mod.record_heartbeat('trail_monitor')
    except Exception as e:
        log('heartbeat記録エラー: ' + str(e))

# ══════════════════════════════════════════
# SL更新実行
# ══════════════════════════════════════════
def update_trailing_stops():
    """全対象ポジションのSLを更新。更新件数を返す"""
    positions = mt5.positions_get()
    if not positions:
        return 0

    updated = 0
    for p in positions:
        cfg = get_trail_config(p.comment.replace('FXBot_', ''), p.symbol)
        if cfg is None:
            continue

        symbol = p.symbol
        pip    = 0.01 if 'JPY' in symbol else 0.0001

        atr = get_atr_5m(symbol)
        if not atr:
            continue

        new_sl, stage = calc_new_sl(p, atr, pip)
        if new_sl is None:
            continue

        request = {
            'action':   mt5.TRADE_ACTION_SLTP,
            'symbol':   symbol,
            'position': p.ticket,
            'tp':       p.tp,
            'sl':       new_sl,
        }
        result = None
        for attempt in range(3):
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                break
            if attempt < 2:
                log('TrailSL再試行(' + str(attempt + 1) + '): ' + symbol +
                    ' code=' + str(result.retcode if result else 'None'))

        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            direction  = 1 if p.type == 0 else -1
            sl_move    = round(abs(new_sl - p.sl) / pip, 1) if p.sl != 0 else 0
            tick       = mt5.symbol_info_tick(symbol)
            current    = (tick.bid if direction == 1 else tick.ask) if tick else p.price_open
            profit_pip = round((current - p.price_open) * direction / pip, 1)
            log('TrailSL更新: ' + symbol +
                ' SL ' + str(round(p.sl, 5)) + '->' + str(new_sl) +
                ' (move:+' + str(sl_move) + 'pips)' +
                ' 含み益:' + ('+' if profit_pip >= 0 else '') + str(profit_pip) + 'pips' +
                ' [' + stage + ']')
            updated += 1
        elif result is not None and result.retcode not in [10027, 10025]:
            log('SL更新失敗(3回試行): ' + symbol +
                ' code=' + str(result.retcode) +
                ' ' + (result.comment if result.comment else ''))

    return updated

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    print('=' * 55)
    print('トレーリングストップ常駐モニター v11 起動')
    print('更新間隔      : ' + str(TRAIL_INTERVAL) + '秒')
    print('最小更新幅    : ATR*' + str(MIN_UPDATE_MULT))
    print('Stage2        : 利益>=ATR*' + str(STAGE2_ACTIVATE) + ' → SL=entry+ATR*stage2_distance (戦略別)')
    print('Stage3        : 利益>=ATR*activate → SL=現在価格-ATR*distance (トレーリング)')
    print('stage2_distance: TRAIL_CONFIGで戦略別設定、省略時=' + str(STAGE2_LOCK_DEFAULT))
    print('除外戦略      : CORR / TRI')
    print('ハートビート  : ' + str(TRAIL_INTERVAL * HEARTBEAT_EVERY // 60) + '分ごと')
    print('=' * 55)

    if not mt5.initialize(
        login=int(env.get('OANDA_LOGIN', 0)),
        password=env.get('OANDA_PASSWORD', ''),
        server=env.get('OANDA_SERVER', '')
    ):
        log('MT5初期化失敗: ' + str(mt5.last_error()))
        return

    account = mt5.account_info()
    if account is None:
        log('MT5口座情報取得失敗')
        mt5.shutdown()
        return

    log('MT5接続成功: ' + account.company + ' / 残高:' + str(round(account.balance)) + '円')

    if DEMO_MODE and 'demo' not in account.server.lower():
        log('警告: DEMO_MODE=TrueですがライブサーバーへのMT5接続が検出されました。終了します。')
        mt5.shutdown()
        return

    loop_count    = 0
    total_updated = 0

    while True:
        try:
            loop_count += 1

            # SL更新（30秒ごと）
            updated = update_trailing_stops()
            total_updated += updated

            # 5分ごとにハートビート出力
            if loop_count % HEARTBEAT_EVERY == 0:
                print_heartbeat(loop_count, total_updated)

        except Exception as e:
            log('ループエラー: ' + str(e))
            try:
                mt5.shutdown()
                time.sleep(5)
                mt5.initialize()
                log('MT5再接続完了')
            except Exception:
                pass

        time.sleep(TRAIL_INTERVAL)

if __name__ == '__main__':
    main()