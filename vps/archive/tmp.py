# vps/analyze_usdcad_htf.py
import re

log_file = r'C:\Users\Administrator\fx_bot\vps\bb_log.txt'
sigmas = []
with open(log_file, encoding='utf-8') as f:
    for line in f:
        if 'USDCAD' not in line or 'HTFスキップ' not in line:
            continue
        m = re.search(r'sigma_pos=([-+]?\d+\.\d+)', line)
        if not m:
            # 別パターン: σ=+1.23
            m = re.search(r'[=（]([+-]?\d+\.\d+)）', line)
        if m:
            sigmas.append(float(m.group(1)))

if sigmas:
    import statistics
    print(f'件数: {len(sigmas)}')
    print(f'平均σ: {statistics.mean(sigmas):+.3f}')
    print(f'中央値: {statistics.median(sigmas):+.3f}')
    # 分布
    for thr in [1.0, 1.2, 1.5, 2.0]:
        rescued = sum(1 for s in sigmas if abs(s) <= thr)
        print(f'  threshold={thr}: {rescued}件救済 ({rescued/len(sigmas)*100:.1f}%)')
else:
    print('σ値をHTFスキップ行から抽出できず→ログフォーマット確認要')
    # サンプル表示
    with open(log_file, encoding='utf-8') as f:
        for i, line in enumerate(f):
            if 'USDCAD' in line and 'HTFスキップ' in line:
                print(line.rstrip())
                if i > 3: break