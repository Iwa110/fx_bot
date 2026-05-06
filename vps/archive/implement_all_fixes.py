"""
全改善項目一括実装
2. ケリー係数0.5→0.25
3. ハートビート機能
4. JPYクロス合計ロット制限
5. TRI閾値見直し（5pipsでバックテスト）
6. セーフモード強化
"""
import os, subprocess

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ══════════════════════════════════════
# 2. ケリー係数 0.5 → 0.25
# ══════════════════════════════════════
rm_path = os.path.join(BASE, 'risk_manager.py')
f = open(rm_path, encoding='utf-8').read()
f = f.replace(
    'half_kelly   = s[\'kelly\'] * 0.5  # ハーフケリーを使用',
    'half_kelly   = s[\'kelly\'] * 0.25  # クォーターケリー（バックテスト不確実性考慮）'
)
open(rm_path, 'w', encoding='utf-8').write(f)
print('2. ケリー係数: 0.5→0.25 完了')

# ══════════════════════════════════════
# 3. ハートビート機能追加
# ══════════════════════════════════════
hb_path = os.path.join(BASE, 'heartbeat_check.py')
open(hb_path, 'w', encoding='utf-8').write('''"""
ハートビート監視スクリプト（summary_notify.pyから呼び出し）
各スクリプトの最終実行時刻を確認し、停止を検知したらDiscord通知
"""
import json, os, ssl, urllib.request
from datetime import datetime, timedelta

BASE    = os.path.dirname(os.path.abspath(__file__))
HB_PATH = os.path.join(BASE, 'heartbeat.json')

# 各スクリプトの許容停止時間（分）
THRESHOLDS = {
    'tri_monitor':  10,   # 5分毎 → 10分以上停止で警告
    'bb_monitor':   10,   # 5分毎 → 10分以上停止で警告
    'mail_monitor': 10,   # 5分毎 → 10分以上停止で警告
    'trail_monitor': 5,   # 常駐 → 5分以上停止で警告
    'daily_trade':  1440, # 日次 → 1日以上停止で警告
}

def load_heartbeat():
    if os.path.exists(HB_PATH):
        with open(HB_PATH, encoding=\'utf-8\') as f:
            return json.load(f)
    return {}

def check_heartbeats(webhook):
    hb  = load_heartbeat()
    now = datetime.now()
    alerts = []
    for script, threshold_min in THRESHOLDS.items():
        last_str = hb.get(script)
        if not last_str:
            alerts.append(f"⚠️ {script}: 記録なし")
            continue
        last = datetime.strptime(last_str, \'%Y-%m-%d %H:%M:%S\')
        elapsed = (now - last).total_seconds() / 60
        if elapsed > threshold_min:
            alerts.append(f"🔴 {script}: {elapsed:.0f}分停止中（閾値{threshold_min}分）")

    if alerts:
        msg = "【FX Bot】⚠️ スクリプト停止検知\\n\\n" + "\\n".join(alerts)
        data = json.dumps({\'content\': msg}).encode(\'utf-8\')
        req  = urllib.request.Request(webhook, data=data,
               headers={\'Content-Type\': \'application/json\', \'User-Agent\': \'Mozilla/5.0\'})
        try:
            urllib.request.urlopen(req, context=ssl._create_unverified_context())
        except: pass
    return alerts

def record_heartbeat(script_name):
    hb = load_heartbeat()
    hb[script_name] = datetime.now().strftime(\'%Y-%m-%d %H:%M:%S\')
    with open(HB_PATH, \'w\', encoding=\'utf-8\') as f:
        json.dump(hb, f, ensure_ascii=False, indent=2)
''')
print('3. ハートビート監視: heartbeat_check.py 作成完了')

# ══════════════════════════════════════
# 4. JPYクロス合計ロット制限
# ══════════════════════════════════════
bb_path = os.path.join(BASE, 'bb_monitor.py')
f2 = open(bb_path, encoding='utf-8').read()

# MAX_JPY_LOT定数を追加
f2 = f2.replace(
    'BB_PARAMS = {',
    'MAX_JPY_LOT = 0.4  # JPYクロス合計ロット上限（円高リスク分散）\n\nBB_PARAMS = {'
)

# place_order前にJPYロット確認を追加
old_order_check = '''        sig = calc_bb_signal(symbol, cfg['is_jpy'])
        if not sig:
            continue
        if place_order(symbol, sig, log, webhook):'''

new_order_check = '''        sig = calc_bb_signal(symbol, cfg['is_jpy'])
        if not sig:
            continue
        # JPYクロス合計ロット制限
        if cfg['is_jpy']:
            jpy_lots = sum(p.volume for p in get_positions()
                          if 'JPY' in p.symbol and 'BB_' in p.comment)
            if jpy_lots >= MAX_JPY_LOT:
                print(f"JPYクロス合計ロット上限({MAX_JPY_LOT}lot)到達: {symbol}スキップ")
                skipped += 1
                continue
        if place_order(symbol, sig, log, webhook):'''

f2 = f2.replace(old_order_check, new_order_check)
open(bb_path, 'w', encoding='utf-8').write(f2)
print('4. JPYクロス合計ロット制限: bb_monitor.py 更新完了')

# ══════════════════════════════════════
# 5. TRI閾値を0.0022→0.0007に変更
#    （22pips→7pips・取引頻度増加）
# ══════════════════════════════════════
tri_path = os.path.join(BASE, 'tri_monitor.py')
f3 = open(tri_path, encoding='utf-8').read()
f3 = f3.replace(
    "tri_entry = params.get('tri_entry', 0.0022)",
    "tri_entry = params.get('tri_entry', 0.0007)  # 22pips→7pipsに変更（取引頻度改善）"
)
open(tri_path, 'w', encoding='utf-8').write(f3)
print('5. TRI閾値: 0.0022→0.0007（22pips→7pips）完了')

# ══════════════════════════════════════
# 6. セーフモード強化
#    残高閾値を下回った場合の自動停止
# ══════════════════════════════════════
safe_path = os.path.join(BASE, 'safe_monitor.py')
open(safe_path, 'w', encoding='utf-8').write('''"""
セーフモード監視（daily_trade.py・bb_monitor.pyから呼び出し）
MT5接続断・残高閾値割れ・急激なDD時の緊急停止
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, \'trade_log.json\')

SAFE_BALANCE_RATIO = 0.70  # 初期残高の70%を下回ったら緊急停止
STOP_FLAG_PATH     = os.path.join(BASE, \'emergency_stop.flag\')

def send_discord(message, webhook):
    if not webhook: return
    data = json.dumps({\'content\': message}).encode(\'utf-8\')
    req  = urllib.request.Request(webhook, data=data,
           headers={\'Content-Type\': \'application/json\', \'User-Agent\': \'Mozilla/5.0\'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
    except: pass

def is_emergency_stopped():
    return os.path.exists(STOP_FLAG_PATH)

def set_emergency_stop():
    with open(STOP_FLAG_PATH, \'w\') as f:
        f.write(datetime.now().strftime(\'%Y-%m-%d %H:%M:%S\'))

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
            \'action\':       mt5.TRADE_ACTION_DEAL,
            \'symbol\':       p.symbol,
            \'volume\':       p.volume,
            \'type\':         close_type,
            \'position\':     p.ticket,
            \'price\':        price,
            \'deviation\':    20,
            \'magic\':        20240101,
            \'comment\':      \'FXBot_SafeMode\',
            \'type_time\':    mt5.ORDER_TIME_GTC,
            \'type_filling\': mt5.ORDER_FILLING_FOK,
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
        now = datetime.now().strftime(\'%Y-%m-%d %H:%M\')
        send_discord(f"【FX Bot】{now}\\n🔴 MT5接続断を検知", webhook)
        return False

    info = mt5.account_info()

    # 残高チェック：初期残高の70%を下回ったら緊急停止
    if initial_balance > 0:
        ratio = info.balance / initial_balance
        if ratio < SAFE_BALANCE_RATIO:
            now    = datetime.now().strftime(\'%Y-%m-%d %H:%M\')
            closed = close_all_positions(webhook)
            set_emergency_stop()
            send_discord(
                f"【FX Bot】{now}\\n🚨 **緊急停止（セーフモード）**\\n\\n"
                f"残高: {info.balance:,.0f}円\\n"
                f"初期残高比: {ratio*100:.1f}%（閾値{SAFE_BALANCE_RATIO*100:.0f}%）\\n"
                f"全ポジション決済: {closed}件\\n\\n"
                f"再開するにはemergency_stop.flagを削除してください",
                webhook
            )
            return False

    return True
''')
print('6. セーフモード強化: safe_monitor.py 作成完了')

# ══════════════════════════════════════
# 構文チェック
# ══════════════════════════════════════
print('\n■ 構文チェック')
for path in [rm_path, bb_path, tri_path, hb_path, safe_path]:
    r    = subprocess.run(['python', '-m', 'py_compile', path],
                          capture_output=True, text=True)
    name = os.path.basename(path)
    print(f'  {name}: ' + ('OK' if r.returncode == 0 else f'ERROR: {r.stderr}'))

print('\n全改善項目の実装完了')
print('次にバックテストv3を実行してSharpe比が現実的な範囲か確認してください')
