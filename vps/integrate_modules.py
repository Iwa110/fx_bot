"""
heartbeat_check.py と safe_monitor.py を既存スクリプトに組み込む
"""
import os, subprocess

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ── bb_monitor.py にハートビート記録・セーフモード追加 ────
bb_path = os.path.join(BASE, 'bb_monitor.py')
f = open(bb_path, encoding='utf-8').read()

# importを追加
f = f.replace(
    'import risk_manager as rm',
    'import risk_manager as rm\nimport heartbeat_check as hb\nimport safe_monitor as sm'
)

# main()の冒頭にハートビート記録とセーフモードチェックを追加
f = f.replace(
    '    check_closed(log, webhook)\n\n    # トレーリングストップ更新',
    '    # ハートビート記録\n    hb.record_heartbeat(\'bb_monitor\')\n\n'
    '    # セーフモードチェック\n    if not sm.check_safe_mode(webhook, log.get(\'initial_balance\', 0)):\n'
    '        mt5.shutdown(); return\n\n'
    '    check_closed(log, webhook)\n\n    # トレーリングストップ更新'
)

open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py: ハートビート・セーフモード組み込み完了')

# ── tri_monitor.py にハートビート記録追加 ────────────
tri_path = os.path.join(BASE, 'tri_monitor.py')
f2 = open(tri_path, encoding='utf-8').read()

f2 = f2.replace(
    'def main():',
    'import heartbeat_check as hb\nimport safe_monitor as sm\n\ndef main():'
)
f2 = f2.replace(
    '    if not mt5.initialize():\n        print(f"MT5接続失敗: {mt5.last_error()}")\n        return',
    '    if not mt5.initialize():\n        print(f"MT5接続失敗: {mt5.last_error()}")\n        return\n'
    '    hb.record_heartbeat(\'tri_monitor\')'
)

open(tri_path, 'w', encoding='utf-8').write(f2)
print('tri_monitor.py: ハートビート組み込み完了')

# ── daily_trade.py にハートビート・セーフモード追加 ────
dt_path = os.path.join(BASE, 'daily_trade.py')
f3 = open(dt_path, encoding='utf-8').read()

f3 = f3.replace(
    'import risk_manager as rm',
    'import risk_manager as rm\nimport heartbeat_check as hb\nimport safe_monitor as sm'
)
f3 = f3.replace(
    '    check_closed(log, webhook)\n    if not check_daily_loss(log, webhook):',
    '    hb.record_heartbeat(\'daily_trade\')\n\n'
    '    if not sm.check_safe_mode(webhook, log.get(\'initial_balance\', 0)):\n'
    '        mt5.shutdown(); return\n\n'
    '    check_closed(log, webhook)\n    if not check_daily_loss(log, webhook):'
)

open(dt_path, 'w', encoding='utf-8').write(f3)
print('daily_trade.py: ハートビート・セーフモード組み込み完了')

# ── summary_notify.py にハートビート監視追加 ─────────
sn_path = os.path.join(BASE, 'summary_notify.py')
f4 = open(sn_path, encoding='utf-8').read()

f4 = f4.replace(
    'def main():',
    'import heartbeat_check as hb\n\ndef main():'
)
# サマリー送信後にハートビートチェックを追加
f4 = f4.replace(
    '    mt5.shutdown()',
    '    # ハートビートチェック（停止スクリプトをDiscord通知）\n'
    '    hb.check_heartbeats(webhook)\n\n'
    '    mt5.shutdown()'
)

open(sn_path, 'w', encoding='utf-8').write(f4)
print('summary_notify.py: ハートビート監視組み込み完了')

# ── trail_monitor.py にハートビート記録追加 ──────────
tr_path = os.path.join(BASE, 'trail_monitor.py')
f5 = open(tr_path, encoding='utf-8').read()

f5 = f5.replace(
    '    loop_count = 0\n    while True:',
    '    import heartbeat_check as hb\n    loop_count = 0\n    while True:'
)
f5 = f5.replace(
    '        try:\n            loop_count += 1',
    '        try:\n            loop_count += 1\n            hb.record_heartbeat(\'trail_monitor\')'
)

open(tr_path, 'w', encoding='utf-8').write(f5)
print('trail_monitor.py: ハートビート記録組み込み完了')

# ── mail_monitor.py にハートビート記録追加 ────────────
mm_path = os.path.join(BASE, 'mail_monitor.py')
f6 = open(mm_path, encoding='utf-8').read()

f6 = f6.replace(
    'def main():\n    print(f"[{datetime.now().strftime(\'%H:%M:%S\')}] メール監視開始...")',
    'def main():\n    print(f"[{datetime.now().strftime(\'%H:%M:%S\')}] メール監視開始...")\n'
    '    try:\n        import heartbeat_check as hb\n        hb.record_heartbeat(\'mail_monitor\')\n'
    '    except: pass'
)

open(mm_path, 'w', encoding='utf-8').write(f6)
print('mail_monitor.py: ハートビート記録組み込み完了')

# ── 構文チェック ──────────────────────────────────────
print('\n■ 構文チェック')
for path in [bb_path, tri_path, dt_path, sn_path, tr_path, mm_path]:
    r    = subprocess.run(['python', '-m', 'py_compile', path],
                          capture_output=True, text=True)
    name = os.path.basename(path)
    print(f'  {name}: ' + ('OK' if r.returncode == 0 else f'ERROR:\n{r.stderr}'))

print('\n全モジュール統合完了')
print('次回スクリプト実行からハートビート・セーフモードが有効になります')
