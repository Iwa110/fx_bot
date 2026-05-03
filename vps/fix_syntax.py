"""bb_monitor.pyのSyntaxError修正"""
import os

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')

f = open(bb_path, encoding='utf-8').read()

# 問題のある行を確認・修正
# f-stringの中に改行が含まれている可能性
lines = f.split('\n')
for i, line in enumerate(lines, 1):
    if 120 <= i <= 125:
        print(f'{i}: {repr(line)}')
