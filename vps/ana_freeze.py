"""
USDCAD sigma freeze analyzer
Usage: python analyze_usdcad_sigma.py
"""
import re
from datetime import datetime
from collections import Counter

LOG_FILE = r'C:\Users\Administrator\fx_bot\vps\bb_log.txt'
TARGET_SYMBOL = 'USDCAD'
TIME_START = '16:02'
TIME_END = '21:12'

log_files = [LOG_FILE]

print(f'=== USDCAD sigma freeze analysis ===')
print(f'Log file: {LOG_FILE}')
print()

rows = []
try:
    with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if TARGET_SYMBOL not in line:
                continue
            m_time = re.search(r'(\d{2}:\d{2}(:\d{2})?)', line)
            if not m_time:
                continue
            t = m_time.group(1)[:5]
            if not (TIME_START <= t <= TIME_END):
                continue
            rows.append((t, line.rstrip(), 'bb_log.txt'))
except Exception as e:
    print(f'[ERROR] {e}')

print(f'--- 該当行: {len(rows)}件 ({TIME_START}~{TIME_END}) ---')
print()

# sigma値を抽出
sigma_pattern = re.compile(r'sigma[=: ]+([+-]?\d+\.\d+)', re.IGNORECASE)
sigma_rows = []
for t, line, fname in rows:
    m = sigma_pattern.search(line)
    if m:
        sigma_rows.append((t, float(m.group(1)), line, fname))

print(f'--- sigma値検出: {len(sigma_rows)}件 ---')
for t, sv, line, fname in sigma_rows:
    print(f'  [{t}] sigma={sv:+.4f}  | {fname}')
    print(f'       {line[:120]}')
    print()

# フリーズ判定（同一sigma値の連続）
if sigma_rows:
    print('--- フリーズ判定 ---')
    prev_sigma = None
    freeze_count = 0
    max_freeze = 0
    freeze_start = None
    for t, sv, line, fname in sigma_rows:
        if prev_sigma is not None and abs(sv - prev_sigma) < 0.0001:
            freeze_count += 1
            if freeze_count > max_freeze:
                max_freeze = freeze_count
                freeze_end = t
        else:
            freeze_count = 0
            freeze_start = t
        prev_sigma = sv
    print(f'  最大連続同一sigma: {max_freeze}サイクル')
    if max_freeze >= 2:
        print(f'  => フリーズ確定: {freeze_start} ~ {freeze_end}')
    else:
        print(f'  => フリーズなし（正常更新）')
    print()

# MT5接続断チェック：tick timestamp停止
print('--- tick/timestamp関連行 ---')
tick_pattern = re.compile(r'(tick|timestamp|symbol_info|last_error|connection)', re.IGNORECASE)
for t, line, fname in rows:
    if tick_pattern.search(line):
        print(f'  [{t}] {line[:140]}')

print()
print('--- エラー/WARNING行 ---')
err_pattern = re.compile(r'(error|warn|except|fail|timeout|disconnect)', re.IGNORECASE)
for t, line, fname in rows:
    if err_pattern.search(line):
        print(f'  [{t}] {line[:140]}')

print()
print('=== 完了 ===')