"""
BB戦略パラメーター改善
1. TP乗数: 2.5 → 3.0
2. BB期間: 10 → 20
3. RR比: 0.83 → 1.0（TP/SL同距離）
"""
import os, subprocess

BASE    = r'C:\Users\Administrator\fx_bot\vps'
rm_path = os.path.join(BASE, 'risk_manager.py')
bb_path = os.path.join(BASE, 'bb_monitor.py')
tr_path = os.path.join(BASE, 'trail_monitor.py')

# ── risk_manager.py：TP乗数修正 ──────────────
f = open(rm_path, encoding='utf-8').read()
f = f.replace(
    "'BB':      {'tp': 2.5, 'sl': 3.0},   # 逆張り・TP短縮（実運用調整）",
    "'BB':      {'tp': 3.0, 'sl': 3.0},   # 逆張り・バックテスト最適値"
)
open(rm_path, 'w', encoding='utf-8').write(f)
print('risk_manager.py: TP乗数 2.5→3.0 完了')

# ── bb_monitor.py：BB期間・RR比修正 ──────────
f2 = open(bb_path, encoding='utf-8').read()
f2 = f2.replace(
    "BB_PARAMS = {'bb_period': 10, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 0.83}",
    "BB_PARAMS = {'bb_period': 20, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 1.0}"
)
open(bb_path, 'w', encoding='utf-8').write(f2)
print('bb_monitor.py: BB期間 10→20 / RR比 0.83→1.0 完了')

# ── trail_monitor.py：発動条件も調整 ─────────
f3 = open(tr_path, encoding='utf-8').read()
f3 = f3.replace(
    'TRAIL_ACTIVATE_MULT = 1.0   # ATR×1.0以上の利益で発動',
    'TRAIL_ACTIVATE_MULT = 0.5   # ATR×0.5以上の利益で発動（早めに追随）'
)
open(tr_path, 'w', encoding='utf-8').write(f3)
print('trail_monitor.py: 発動条件 ATR×1.0→0.5 完了')

# ── 構文チェック ──────────────────────────────
for path in [rm_path, bb_path, tr_path]:
    r = subprocess.run(['python', '-m', 'py_compile', path],
                       capture_output=True, text=True)
    name = os.path.basename(path)
    print(f'構文チェック {name}: ' + ('OK' if r.returncode == 0 else r.stderr))

# ── 変更後の確認 ──────────────────────────────
import sys
sys.path.insert(0, BASE)
import importlib, risk_manager as rm
importlib.reload(rm)

print('\n■ 変更後のTP/SL確認')
print(f"  {'ペア':<8} {'ATR':>8} {'SL幅':>8} {'TP幅':>8}")
print(f"  {'-'*40}")
cases = [
    ('GBPJPY', 0.060, True),
    ('USDJPY', 0.030, True),
    ('EURUSD', 0.00025, False),
    ('USDCAD', 0.00040, False),
]
for pair, atr, is_jpy in cases:
    pip    = 0.01 if is_jpy else 0.0001
    tp, sl = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)
    print(f"  {pair:<8} {atr:>8.5f} {sl/pip:>6.1f}pips {tp/pip:>6.1f}pips")

print('\n変更完了。VPSでbb_monitor.pyを再実行してください。')
