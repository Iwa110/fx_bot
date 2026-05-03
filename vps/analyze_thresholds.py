import re
from collections import defaultdict

LOG_FILE = r'C:\Users\Administrator\fx_bot\vps\daily_log.txt'

mom_vals = defaultdict(list)
fmom_vals = defaultdict(list)
z_vals = []
tri_vals = []

pat_mom = re.compile(r'\[DEBUG\].*?([A-Z]{6}).*?mom=([-\d.]+).*?fmom=([-\d.]+)', re.I)
pat_z   = re.compile(r'\[DEBUG\] check_corr \w+ z=([+\-\d.]+)', re.I)
pat_tri = re.compile(r'\[DEBUG\] check_tri.*?乖離=([+\-\d.]+)', re.I)

with open(LOG_FILE, encoding='utf-8', errors='replace') as f:
    for line in f:
        if '[DEBUG]' not in line:
            continue
        m = pat_mom.search(line)
        if m:
            pair, mom, fmom = m.group(1), float(m.group(2)), float(m.group(3))
            mom_vals[pair].append(abs(mom))
            fmom_vals[pair].append(abs(fmom))
        m2 = pat_z.search(line)
        if m2:
            z_vals.append(abs(float(m2.group(1))))
        m3 = pat_tri.search(line)
        if m3:
            tri_vals.append(abs(float(m3.group(1))))

def stats(lst):
    if not lst: return 'N/A'
    lst = sorted(lst)
    n = len(lst)
    return f'n={n} min={lst[0]:.5f} med={lst[n//2]:.5f} p75={lst[int(n*0.75)]:.5f} max={lst[-1]:.5f}'

print('=== MOM / FMOM per pair ===')
for p in sorted(mom_vals):
    print(f'  {p}: mom  {stats(mom_vals[p])}')
    print(f'  {p}: fmom {stats(fmom_vals[p])}')
print('\n=== CORR z_score ===')
print(f'  {stats(z_vals)}')
print('\n=== TRI deviation (乖離) ===')
print(f'  {stats(tri_vals)}')
