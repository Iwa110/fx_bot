"""
volume計算を修正：lot × 10000 → lot × 100000
bb_monitor.py と risk_manager.py を修正
"""
import os, subprocess

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')
rm_path = os.path.join(BASE, 'risk_manager.py')

# ── bb_monitor.py 修正 ────────────────────────
f = open(bb_path, encoding='utf-8').read()

# volume計算を修正
f = f.replace(
    'volume    = lot * 10000  # 0.1lot=1000通貨',
    'volume    = lot * 100000  # 0.1lot=10000通貨（FX標準）'
)
# バックアップ用パターンも修正
f = f.replace(
    'volume    = lot * 10000',
    'volume    = lot * 100000'
)

open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py 修正完了（volume: lot×10000 → lot×100000）')

# ── risk_manager.py 修正 ──────────────────────
f2 = open(rm_path, encoding='utf-8').read()

# calc_lot内のloss_per_lot計算を修正
f2 = f2.replace(
    'loss_per_lot = sl_dist * 10_000 * (1.0 if is_jpy else USDJPY)',
    'loss_per_lot = sl_dist * 100_000 * (1.0 if is_jpy else USDJPY)'
)
# get_kelly_lot内も修正
f2 = f2.replace(
    'loss_per_lot = sl_dist * 10_000 * (1.0 if is_jpy else USDJPY)',
    'loss_per_lot = sl_dist * 100_000 * (1.0 if is_jpy else USDJPY)'
)

open(rm_path, 'w', encoding='utf-8').write(f2)
print('risk_manager.py 修正完了（loss_per_lot: 10000 → 100000）')

# ── 構文チェック ──────────────────────────────
for path in [bb_path, rm_path]:
    r = subprocess.run(['python', '-m', 'py_compile', path],
                       capture_output=True, text=True)
    name = os.path.basename(path)
    print(f'構文チェック {name}: ' + ('OK' if r.returncode == 0 else r.stderr))

# ── 修正後のロット確認 ────────────────────────
import sys
sys.path.insert(0, BASE)
import importlib, risk_manager as rm
importlib.reload(rm)

print('\n■ 修正後のロット確認（0.2lotでの損益試算）')
cases = [
    ('EURUSD', 0.00075, False, 0.2),
    ('USDJPY', 0.127,   True,  0.2),
    ('USDCAD', 0.00068, False, 0.2),
]
for symbol, sl_dist, is_jpy, lot in cases:
    conv   = 1.0 if is_jpy else 150.0
    volume = lot * 100000
    pnl    = sl_dist * volume * conv
    print(f'  {symbol}: SL={sl_dist} lot={lot} → 損失={pnl:,.0f}円')
    # dealの実績と比較
print('  ※ EUR/USD実績: -2,378円 / USD/JPY実績: -2,560円')
