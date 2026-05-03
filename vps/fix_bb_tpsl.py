"""
BBバンド TP/SL最適値反映パッチ
- risk_manager.py: BB乗数をTP×5.0・SL×3.0に更新
- bb_monitor.py: exit_sigmaを1.0に更新
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ── risk_manager.py 更新 ──────────────────────
rm_path = os.path.join(BASE, 'risk_manager.py')
f = open(rm_path, encoding='utf-8').read()

f = f.replace(
    "'BB':      {'tp': 3.0, 'sl': 2.0},   # 逆張り・標準",
    "'BB':      {'tp': 5.0, 'sl': 3.0},   # 逆張り・最適化済み"
)

open(rm_path, 'w', encoding='utf-8').write(f)
print('risk_manager.py 更新完了（BB: TP×5.0 / SL×3.0）')

# ── bb_monitor.py 更新 ────────────────────────
bb_path = os.path.join(BASE, 'bb_monitor.py')
f = open(bb_path, encoding='utf-8').read()

f = f.replace(
    "BB_PARAMS = {'bb_period':10,'bb_sigma':1.5,'exit_sigma':0.5,'sl_atr':2.0,'rr':1.5}",
    "BB_PARAMS = {'bb_period':10,'bb_sigma':1.5,'exit_sigma':1.0,'sl_atr':3.0,'rr':1.67}"
)

open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py 更新完了（exit_sigma=1.0 / sl_atr=3.0 / rr=1.67）')

# ── 確認 ──────────────────────────────────────
import sys
sys.path.insert(0, BASE)
import importlib
import risk_manager as rm
importlib.reload(rm)

print('\n■ 更新後のTP/SL確認')
for pair, is_jpy in [('GBPJPY', True), ('EURUSD', False), ('EURGBP', False)]:
    atr_sample = 0.05 if is_jpy else 0.00030
    tp, sl = rm.calc_tp_sl(atr_sample, 'BB', is_jpy=is_jpy)
    lot    = rm.calc_lot(1_000_000, sl, pair)
    print(f'  {pair}: ATR={atr_sample} → SL={sl:.5f} TP={tp:.5f} ロット={lot}')

print('\n完了。bb_monitor.pyを再起動してください。')
