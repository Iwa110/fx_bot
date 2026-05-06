"""bb_monitor.pyのcalc_bb_signalにバンドの1本シフトを追加"""
import os, subprocess

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')
f       = open(bb_path, encoding='utf-8').read()

old = """    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]
    ma  = sum(closes[-period:]) / period
    std = (sum((c - ma) ** 2 for c in closes[-period:]) / period) ** 0.5
    if std == 0:
        return None
    upper = ma + sigma * std
    lower = ma - sigma * std"""

new = """    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]
    # 先読み対策：バンドを1本シフト（前足の確定値でバンド計算）
    ma  = sum(closes[-(period+1):-1]) / period
    std = (sum((c - ma) ** 2 for c in closes[-(period+1):-1]) / period) ** 0.5
    if std == 0:
        return None
    upper = ma + sigma * std
    lower = ma - sigma * std"""

if old in f:
    f = f.replace(old, new)
    open(bb_path, 'w', encoding='utf-8').write(f)
    print('bb_monitor.py: バンド1本シフト追加完了')
else:
    print('パターンが見つかりません')
    idx = f.find('ma  = sum(closes')
    print(repr(f[idx-50:idx+200]))

r = subprocess.run(['python', '-m', 'py_compile', bb_path],
                   capture_output=True, text=True)
print('構文チェック: ' + ('OK' if r.returncode == 0 else r.stderr))
