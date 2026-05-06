"""
対策1：ATR下限を撤廃（実ATRをそのまま使用）
対策3：TP乗数を5.0→2.5に下げ（早めに利確）
"""
import os, subprocess

BASE    = r'C:\Users\Administrator\fx_bot\vps'
rm_path = os.path.join(BASE, 'risk_manager.py')
bb_path = os.path.join(BASE, 'bb_monitor.py')

# ── risk_manager.py：ATR下限を撤廃 ────────────
f = open(rm_path, encoding='utf-8').read()

f = f.replace(
    'ATR_FLOOR_JPY    = 0.020   # JPYペア5分足（約2pips・実ATR基準）\n'
    'ATR_FLOOR_NONJPY = 0.00010 # 非JPYペア5分足（約1pip・実ATR基準）',
    'ATR_FLOOR_JPY    = 0.005   # JPYペア5分足（ほぼ無効化・実ATR優先）\n'
    'ATR_FLOOR_NONJPY = 0.00002 # 非JPYペア5分足（ほぼ無効化・実ATR優先）'
)

# BB乗数：TP×5.0→2.5に変更
f = f.replace(
    "'BB':      {'tp': 5.0, 'sl': 3.0},   # 逆張り・最適化済み",
    "'BB':      {'tp': 2.5, 'sl': 3.0},   # 逆張り・TP短縮（実運用調整）"
)

open(rm_path, 'w', encoding='utf-8').write(f)
print('risk_manager.py 更新完了')
print('  ATR下限：JPY 0.020→0.005 / 非JPY 0.00010→0.00002')
print('  BB TP乗数：5.0→2.5')

# ── bb_monitor.py：RR比をTP/SL比に合わせて更新 ──
f2 = open(bb_path, encoding='utf-8').read()

# RR比をTP/SL乗数比に合わせて更新（2.5/3.0=0.83→切上げ0.85）
# ただしTPはrisk_managerのcalc_tp_slが計算するのでBB_PARAMSのrrは参考値のみ
f2 = f2.replace(
    "BB_PARAMS   = {'bb_period': 10, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 1.67}",
    "BB_PARAMS   = {'bb_period': 10, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 0.83}"
)

open(bb_path, 'w', encoding='utf-8').write(f2)
print('bb_monitor.py 更新完了（RR比: 1.67→0.83）')

# ── 構文チェック ──────────────────────────────
for path in [rm_path, bb_path]:
    r = subprocess.run(['python', '-m', 'py_compile', path],
                       capture_output=True, text=True)
    name = os.path.basename(path)
    print(f'構文チェック {name}: ' + ('OK' if r.returncode == 0 else r.stderr))

# ── 変更後のTP/SL確認 ─────────────────────────
import sys
sys.path.insert(0, BASE)
import importlib
import risk_manager as rm
importlib.reload(rm)

print('\n■ 変更後のTP/SL確認（実ATR使用）')
test_cases = [
    ('GBPJPY', 0.060, True),
    ('EURJPY', 0.045, True),
    ('USDJPY', 0.030, True),
    ('AUDJPY', 0.035, True),
    ('EURUSD', 0.00025, False),
    ('GBPUSD', 0.00035, False),
    ('USDCAD', 0.00040, False),
]
print(f"  {'ペア':<8} {'ATR':>8} {'SL幅':>8} {'TP幅':>8} {'SL':>7} {'TP':>7} {'ロット':>6}")
print(f"  {'-'*60}")
for pair, atr, is_jpy in test_cases:
    pip    = 0.01 if is_jpy else 0.0001
    tp, sl = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)
    sl_pip = sl / pip
    tp_pip = tp / pip
    lot    = rm.calc_lot(1_000_000, sl, pair)
    print(f"  {pair:<8} {atr:>8.5f} {sl_pip:>6.1f}pip {tp_pip:>6.1f}pip "
          f"{sl:>7.5f} {tp:>7.5f} {lot:>6.2f}lot")

print('\n変更完了。bb_monitor.pyを再実行してください。')
