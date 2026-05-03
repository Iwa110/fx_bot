import os

path = r'C:\Users\Administrator\fx_bot\vps\bb_monitor.py'
f = open(path, encoding='utf-8').read()

f = f.replace('MAX_TOTAL_POS = 14', 'MAX_TOTAL_POS = 13')

# EURGBP行を削除（複数パターンに対応）
lines = f.split('\n')
lines = [l for l in lines if 'EURGBP' not in l]
f = '\n'.join(lines)

open(path, 'w', encoding='utf-8').write(f)
print('修正完了')

import importlib, sys
sys.path.insert(0, r'C:\Users\Administrator\fx_bot\vps')
import bb_monitor
importlib.reload(bb_monitor)
print('MAX_TOTAL_POS:', bb_monitor.MAX_TOTAL_POS)
print('BB_PAIRS:', list(bb_monitor.BB_PAIRS.keys()))
