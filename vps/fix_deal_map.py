"""
deal_mapを決済deal(entry=1)のみに絞るパッチ
bb_monitor.py と daily_trade.py を修正
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

for filename in ['bb_monitor.py', 'daily_trade.py']:
    path = os.path.join(BASE, filename)
    f    = open(path, encoding='utf-8').read()

    old = '''    deal_map  = {}
    if deals:
        for d in deals:
            deal_map[d.order]  = d
            deal_map[d.ticket] = d'''

    new = '''    deal_map  = {}
    if deals:
        for d in deals:
            if d.entry == 1:  # 決済dealのみ（entry=0はエントリーdeal）
                deal_map[d.order]  = d
                deal_map[d.ticket] = d'''

    if old in f:
        f = f.replace(old, new)
        open(path, 'w', encoding='utf-8').write(f)
        print(f'{filename}: 修正完了')
    else:
        # 旧パターンも試みる
        old2 = '''    deal_map  = {d.order:d for d in deals} if deals else {}
    deal_map.update({d.ticket:d for d in deals} if deals else {})'''
        new2 = '''    deal_map  = {}
    if deals:
        for d in deals:
            if d.entry == 1:
                deal_map[d.order]  = d
                deal_map[d.ticket] = d'''
        if old2 in f:
            f = f.replace(old2, new2)
            open(path, 'w', encoding='utf-8').write(f)
            print(f'{filename}: 修正完了（旧パターン）')
        else:
            print(f'{filename}: パターン見つからず - 手動確認が必要')

print('\n修正完了。次回決済から正しい損益が表示されます。')
