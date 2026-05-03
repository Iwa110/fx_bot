"""
trail_monitor.py と bb_monitor.py に record_heartbeat() 呼び出しを追加するパッチ
VPS上で実行: python patch_heartbeat.py
"""
import os, shutil
from datetime import datetime

BASE = r'C:\Users\Administrator\fx_bot\vps'

def patch_file(path, old, new, label):
    with open(path, encoding='utf-8') as f:
        content = f.read()
    if old not in content:
        print(f'[SKIP] {label}: 対象文字列が見つかりません（既にパッチ済み？）')
        return False
    # バックアップ
    bak = path + '.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy2(path, bak)
    print(f'[BAK]  {label}: バックアップ → {bak}')
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'[OK]   {label}: パッチ適用完了')
    return True

# ── trail_monitor.py ──────────────────────────────────────────────────────────
# importブロックにheartbeat_checkを追加 + print_heartbeat()内でrecord_heartbeat()呼び出し

TRAIL_PATH = os.path.join(BASE, 'trail_monitor.py')

# 1) importに追加
trail_import_old = 'import MetaTrader5 as mt5\nimport json, os, time, urllib.request\nfrom datetime import datetime'
trail_import_new = ('import MetaTrader5 as mt5\n'
                    'import json, os, time, urllib.request\n'
                    'from datetime import datetime\n'
                    'import sys\n'
                    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
                    'from heartbeat_check import record_heartbeat')

# 2) print_heartbeat()の末尾でrecord_heartbeat()を呼ぶ
#    LOG_PATHへのwrite直後のtry/except末尾に追記
trail_hb_old = (
    '    try:\n'
    '        with open(LOG_PATH, \'a\', encoding=\'utf-8\') as f:\n'
    '            f.write(\'[\' + ts + \']\\n\' + output + \'\\n\')\n'
    '    except Exception:\n'
    '        pass\n'
    '\n'
    '# ══════════════════════════════════════════\n'
    '# SL更新実行'
)
trail_hb_new = (
    '    try:\n'
    '        with open(LOG_PATH, \'a\', encoding=\'utf-8\') as f:\n'
    '            f.write(\'[\' + ts + \']\\n\' + output + \'\\n\')\n'
    '    except Exception:\n'
    '        pass\n'
    '    # heartbeat更新\n'
    '    try:\n'
    '        record_heartbeat(\'trail_monitor\')\n'
    '    except Exception:\n'
    '        pass\n'
    '\n'
    '# ══════════════════════════════════════════\n'
    '# SL更新実行'
)

print('=== trail_monitor.py パッチ ===')
patch_file(TRAIL_PATH, trail_import_old, trail_import_new, 'trail_monitor import')
patch_file(TRAIL_PATH, trail_hb_old,     trail_hb_new,     'trail_monitor record_heartbeat')

# ── bb_monitor.py ─────────────────────────────────────────────────────────────
BB_PATH = os.path.join(BASE, 'bb_monitor.py')

# 1) importに追加
bb_import_old = ('import sys, os, json, time\n'
                 'from datetime import datetime, timedelta\n'
                 'import MetaTrader5 as mt5\n'
                 'import pandas as pd\n'
                 'import numpy as np\n'
                 '\n'
                 'sys.path.insert(0, r\'C:\\Users\\Administrator\\fx_bot\\vps\')\n'
                 'import risk_manager as rm')
bb_import_new = ('import sys, os, json, time\n'
                 'from datetime import datetime, timedelta\n'
                 'import MetaTrader5 as mt5\n'
                 'import pandas as pd\n'
                 'import numpy as np\n'
                 '\n'
                 'sys.path.insert(0, r\'C:\\Users\\Administrator\\fx_bot\\vps\')\n'
                 'import risk_manager as rm\n'
                 'from heartbeat_check import record_heartbeat')

# 2) main()末尾 mt5.shutdown() の直前に record_heartbeat() を追加
bb_shutdown_old = ('    log(\'[\' + now + \'] BB v6完了: 発注\' + str(executed) + \'件 \' +\n'
                   '        \'スキップ\' + str(skipped) + \'件 \' +\n'
                   '        \'ポジション\' + str(count_total()) + \'/\' + str(MAX_TOTAL_POS))\n'
                   '\n'
                   '    mt5.shutdown()')
bb_shutdown_new = ('    log(\'[\' + now + \'] BB v6完了: 発注\' + str(executed) + \'件 \' +\n'
                   '        \'スキップ\' + str(skipped) + \'件 \' +\n'
                   '        \'ポジション\' + str(count_total()) + \'/\' + str(MAX_TOTAL_POS))\n'
                   '\n'
                   '    # heartbeat更新\n'
                   '    try:\n'
                   '        record_heartbeat(\'bb_monitor\')\n'
                   '    except Exception:\n'
                   '        pass\n'
                   '\n'
                   '    mt5.shutdown()')

print('\n=== bb_monitor.py パッチ ===')
patch_file(BB_PATH, bb_import_old, bb_import_new, 'bb_monitor import')
patch_file(BB_PATH, bb_shutdown_old, bb_shutdown_new, 'bb_monitor record_heartbeat')

print('\n=== 完了 ===')
print('trail_monitor.py を再起動してください（bb_monitorはタスクスケジューラが自動実行）')