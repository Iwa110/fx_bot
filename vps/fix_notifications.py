"""
通知修正パッチ
1. trail_monitor.py：SL更新のDiscord通知を削除
2. bb_monitor.py：利確/損切り通知を削除（サマリーに統合）
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ── trail_monitor.py：Discord通知削除 ────────
trail_path = os.path.join(BASE, 'trail_monitor.py')
f = open(trail_path, encoding='utf-8').read()

old = '''        if result.retcode == mt5.TRADE_RETCODE_DONE:
            sl_move    = abs(new_sl - current_sl) / pip
            profit_pip = profit_dist / pip
            now        = datetime.now().strftime('%H:%M:%S')
            msg = (f"📈 **トレーリングSL更新**\\n"
                   f"通貨ペア: {symbol}\\n"
                   f"SL: {current_sl:.5f} → {new_sl:.5f} "
                   f"(+{sl_move:.1f}pips)\\n"
                   f"現在利益: +{profit_pip:.1f}pips")
            send_discord(msg, webhook)
            print(f"[{now}] トレーリングSL更新: {symbol} "
                  f"{current_sl:.5f}→{new_sl:.5f} ({sl_move:.1f}pips)")'''

new = '''        if result.retcode == mt5.TRADE_RETCODE_DONE:
            sl_move    = abs(new_sl - current_sl) / pip
            profit_pip = profit_dist / pip
            now        = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] トレーリングSL更新: {symbol} "
                  f"{current_sl:.5f}→{new_sl:.5f} ({sl_move:.1f}pips)")'''

if old in f:
    f = f.replace(old, new)
    open(trail_path, 'w', encoding='utf-8').write(f)
    print('trail_monitor.py: SL更新通知削除完了')
else:
    print('trail_monitor.py: パターンなし（既に修正済みの可能性）')

# ── bb_monitor.py：利確/損切り通知削除 ────────
bb_path = os.path.join(BASE, 'bb_monitor.py')
f2 = open(bb_path, encoding='utf-8').read()

old2 = '''        msg  = "【FX Bot BB】" + now + "\\n"
        msg += emoji + " **" + reason + "確定**\\n"
        msg += "通貨ペア: " + order['symbol'] + "\\n"
        msg += "損益: " + ('+' if profit >= 0 else '') + "{:,.0f}円\\n".format(profit)
        msg += "ロット: " + str(lot)
        send_discord(msg, webhook)'''

new2 = '''        # 利確/損切り通知はサマリーに統合（個別通知なし）
        print("決済: " + order['symbol'] + " " + reason + " " + str(profit) + "円")'''

if old2 in f2:
    f2 = f2.replace(old2, new2)
    open(bb_path, 'w', encoding='utf-8').write(f2)
    print('bb_monitor.py: 利確/損切り通知削除完了')
else:
    print('bb_monitor.py: パターンなし')

# ── 構文チェック ──────────────────────────────
import subprocess
for path, name in [(trail_path, 'trail_monitor.py'), (bb_path, 'bb_monitor.py')]:
    r = subprocess.run(['python', '-m', 'py_compile', path],
                       capture_output=True, text=True)
    print(f'構文チェック {name}: ' + ('OK' if r.returncode == 0 else r.stderr))

print('\n完了。次回サマリー（7/12/16/21時）にBB決済結果がまとめて届きます。')
