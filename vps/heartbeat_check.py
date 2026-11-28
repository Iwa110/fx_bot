"""
ハートビート監視スクリプト（summary_notify.pyから呼び出し）
各スクリプトの最終実行時刻を確認し、停止を検知したらDiscord通知
"""
import json, os, ssl, urllib.request
from datetime import datetime, timedelta

BASE    = os.path.dirname(os.path.abspath(__file__))
HB_PATH = os.path.join(BASE, 'heartbeat.json')

# 各スクリプトの許容停止時間（分）
# 監視対象: bb_monitor v17 / trail_monitor v11(SMC_GBPAUD含む) /
#           mail_monitor / daily_trade / stat_arb_monitor /
#           grid_monitor v8(ペア別・ブローカー別) / mr_monitor v2(ペア別・ブローカー別) /
#           news_monitor v1.2(ブローカー別) / sma_squeeze v4.6(ブローカー別) /
#           account_snapshot
# NOTE: 新しい戦略・ペア・ブローカーを追加したら、対応する
#       record_heartbeat() 呼び出しの追加と合わせて必ずここにキーを追加すること。
# NOTE: grid_monitor/mr_monitorは同じペアでも axiory/exness/oanda_live が別プロセス
#       として並行稼働するため、キーは必ず '<ペア>_<ブローカー>' までブローカー名を
#       含めること（ペア名だけだと、実弾(oanda_live)が落ちてもデモ(axiory/exness)側の
#       heartbeatで上書きされ、実弾の停止だけが検知できなくなる）。
THRESHOLDS = {
    'bb_monitor':        10,   # 5分毎 → 10分以上停止で警告
    'trail_monitor':      5,   # 常駐 → 5分以上停止で警告
    'mail_monitor':      10,   # 5分毎呼び出し → 10分以上停止で警告
    'daily_trade':     1440,   # 日次 → 1日以上停止で警告
    'stat_arb_monitor':  15,   # 10秒毎 → 15分以上停止で警告
    # grid_monitor v8: 60秒毎(LOOP_INTERVAL) → 10分以上停止で警告
    'grid_monitor_AUDCAD_axiory':     10,
    'grid_monitor_AUDCAD_exness':     10,
    'grid_monitor_AUDCAD_oanda_live': 10,  # [実弾]
    'grid_monitor_NZDJPY_axiory':     10,
    'grid_monitor_NZDJPY_exness':     10,
    'grid_monitor_EURGBP_axiory':     10,
    'grid_monitor_EURGBP_exness':     10,
    'grid_monitor_EURGBP_oanda_live': 10,  # [実弾]
    'grid_monitor_AUDNZD_axiory':     10,
    'grid_monitor_AUDNZD_exness':     10,
    'grid_monitor_AUDNZD_oanda_live': 10,  # [実弾]
    'grid_monitor_USDJPY_axiory':     10,
    'grid_monitor_USDJPY_exness':     10,
    'grid_monitor_CADCHF_axiory':     10,
    'grid_monitor_CADCHF_exness':     10,
    'grid_monitor_CADCHF_oanda_live': 10,  # [実弾]
    # mr_monitor v2: 300秒毎(LOOP_INTERVAL) → 15分以上停止で警告 (現状すべてdemo)
    'mr_monitor_AUDCAD_axiory': 15,
    'mr_monitor_AUDCAD_exness': 15,
    'mr_monitor_CADCHF_axiory': 15,
    'mr_monitor_CADCHF_exness': 15,
    'news_monitor_axiory': 10,  # 60秒毎 → 10分以上停止で警告
    'news_monitor_exness': 10,
    'sma_squeeze_axiory':  10,  # 60秒毎 → 10分以上停止で警告
    'sma_squeeze_exness':  10,
    'account_snapshot':    90,  # 1時間毎(Task Scheduler) → 90分以上停止で警告
}

def load_heartbeat():
    if os.path.exists(HB_PATH):
        with open(HB_PATH, encoding='utf-8') as f:
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
        last = datetime.strptime(last_str, '%Y-%m-%d %H:%M:%S')
        elapsed = (now - last).total_seconds() / 60
        if elapsed > threshold_min:
            alerts.append(f"🔴 {script}: {elapsed:.0f}分停止中（閾値{threshold_min}分）")

    if alerts:
        msg = "【FX Bot】⚠️ スクリプト停止検知\n\n" + "\n".join(alerts)
        data = json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(webhook, data=data,
               headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        try:
            urllib.request.urlopen(req, context=ssl._create_unverified_context())
        except: pass
    return alerts

def record_heartbeat(script_name):
    hb = load_heartbeat()
    hb[script_name] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(HB_PATH, 'w', encoding='utf-8') as f:
        json.dump(hb, f, ensure_ascii=False, indent=2)
