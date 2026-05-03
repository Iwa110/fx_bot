"""BB発注通知を無効化・サマリーに統合するパッチスクリプト"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ── bb_monitor.py：発注時のDiscord通知を削除 ──
bb_path = os.path.join(BASE, 'bb_monitor.py')
f = open(bb_path, encoding='utf-8').read()

# send_discord呼び出し部分をログ出力のみに変更
old = """        send_discord(
            f"【FX Bot BB】{now}\\n🟢 **BBバンド逆張り 発注**\\n\\n"
            f"通貨ペア: {symbol}\\n方向: {dir_jp}\\n"
            f"エントリー: {entry}\\nTP: {tp} / SL: {sl}\\n"
            f"ロット: {lot}（ATRベース）\\n"
            f"ATR: {sig['atr']:.5f}{kelly_note}{overlap_note}",
            webhook
        )"""
new = """        # 発注通知はサマリーに統合（個別通知なし）
        print(f"BB発注: {symbol} {dir_jp} {lot}lot @ {entry} TP:{tp} SL:{sl}")"""

f = f.replace(old, new)
open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py 通知削除完了')

# ── summary_notify.py：BB戦略の発注履歴をサマリーに追加 ──
summary_path = os.path.join(BASE, 'summary_notify.py')
f = open(summary_path, encoding='utf-8').read()

# BB戦略を戦略設定に追加
old_config = """STRATEGY_CONFIG = {
    'TRI':    {'max_pos':1},
    'MOM_JPY':{'max_pos':1},
    'MOM_GBJ':{'max_pos':1},
    'CORR':   {'max_pos':1},
    'STR':    {'max_pos':1},
}
MAX_TOTAL = 6"""

new_config = """STRATEGY_CONFIG = {
    'TRI':    {'max_pos':1},
    'MOM_JPY':{'max_pos':1},
    'MOM_GBJ':{'max_pos':1},
    'CORR':   {'max_pos':1},
    'STR':    {'max_pos':1},
}
BB_PAIRS = ['USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD','EURGBP']
MAX_TOTAL = 14"""

f = f.replace(old_config, new_config)

# サマリーメッセージにBBセクションを追加
old_msg = """    send_discord(
        f"【FX Bot】{now} 日次サマリー\\n\\n"
        f"**■ 口座状況**\\n残高: {info.balance:,.0f}円\\n資産: {info.equity:,.0f}円\\n"
        f"本日損益: {'+' if pnl>=0 else ''}{pnl:,.0f}円{closed_text}\\n\\n"
        f"**■ 保有ポジション（{len(positions)}/{MAX_TOTAL}）**"
        f"{pos_text if pos_text else chr(10)+'  なし'}\\n\\n"
        f"**■ 戦略別ポジション**\\n{strategy_lines}\\n\\n"
        f"取引状態: {'⛔ 停止中' if log['daily_loss_stopped'] else '✅ 稼働中'}",
        webhook
    )"""

new_msg = """    # BB戦略ポジション集計
    bb_pos_text = ''
    bb_count = 0
    for p in positions:
        if 'BB_' in p.comment:
            dir_jp = '買い' if p.type==0 else '売り'
            bb_pos_text += f"\\n  {p.symbol} {dir_jp} {p.volume}lot " \
                           f"損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"
            bb_count += 1

    # 本日のBB発注履歴
    bb_today = [o for o in log.get('orders',[]) if 'BB_' in o.get('strategy','')]
    bb_closed_today = [c for c in log.get('closed',[]) if 'BB_' in c.get('strategy','')]
    bb_closed_pnl = sum(c.get('profit',0) for c in bb_closed_today)

    bb_summary = f"\\n  保有: {bb_count}件{bb_pos_text}"
    if bb_closed_today:
        bb_summary += f"\\n  本日決済: {len(bb_closed_today)}件 " \
                      f"合計{'+' if bb_closed_pnl>=0 else ''}{bb_closed_pnl:,.0f}円"

    send_discord(
        f"【FX Bot】{now} 日次サマリー\\n\\n"
        f"**■ 口座状況**\\n残高: {info.balance:,.0f}円\\n資産: {info.equity:,.0f}円\\n"
        f"本日損益: {'+' if pnl>=0 else ''}{pnl:,.0f}円{closed_text}\\n\\n"
        f"**■ 保有ポジション（{len(positions)}/{MAX_TOTAL}）**"
        f"{pos_text if pos_text else chr(10)+'  なし'}\\n\\n"
        f"**■ 戦略別ポジション**\\n{strategy_lines}\\n\\n"
        f"**■ BB逆張り（{bb_count}/{len(BB_PAIRS)}）**{bb_summary}\\n\\n"
        f"取引状態: {'⛔ 停止中' if log['daily_loss_stopped'] else '✅ 稼働中'}",
        webhook
    )"""

f = f.replace(old_msg, new_msg)
open(summary_path, 'w', encoding='utf-8').write(f)
print('summary_notify.py BB統合完了')
print('\n動作確認: python bb_monitor.py')
