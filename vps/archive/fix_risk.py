"""VPS上で実行するリスク管理モジュール修正スクリプト"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'

# ── risk_manager.py 修正 ──────────────────────
rm_path = os.path.join(BASE, 'risk_manager.py')
f = open(rm_path, encoding='utf-8').read()

# ATR_FLOORを通貨タイプ別に分割
f = f.replace(
    """ATR_FLOOR = {
    'TRI':     0.0003,   # EUR/GBP 日足
    'MOM_JPY': 0.30,     # USD/JPY 日足（約30pips）
    'MOM_GBJ': 0.50,     # GBP/JPY 日足（約50pips）
    'CORR':    0.0003,   # AUD/USD 日足
    'STR':     0.0003,   # 各種 日足
    'BB':      0.10,     # 5分足（JPYペア：約10pips相当）
}""",
    """ATR_FLOOR_JPY    = 0.10    # JPYペア5分足（約10pips）
ATR_FLOOR_NONJPY = 0.0005  # 非JPYペア5分足（約5pips）
ATR_FLOOR_DAY = {
    'TRI':     0.0003,
    'MOM_JPY': 0.30,
    'MOM_GBJ': 0.50,
    'CORR':    0.0003,
    'STR':     0.0003,
}"""
)

# calc_tp_slのシグネチャにis_jpy追加
f = f.replace(
    'def calc_tp_sl(atr: float, strategy: str) -> tuple:',
    'def calc_tp_sl(atr: float, strategy: str, is_jpy: bool = False) -> tuple:'
)

# ATR下限適用部分を修正
f = f.replace(
    """    # ATR下限適用（5分足の過小値による過大ロットを防止）
    key     = strategy.split('_')[0] if '_' in strategy else strategy
    atr     = max(atr, ATR_FLOOR.get(key, 0.0003))""",
    """    # ATR下限適用（5分足の過小値による過大ロットを防止）
    key = strategy.split('_')[0] if '_' in strategy else strategy
    if key == 'BB':
        floor = ATR_FLOOR_JPY if is_jpy else ATR_FLOOR_NONJPY
    else:
        floor = ATR_FLOOR_DAY.get(key, 0.0003)
    atr = max(atr, floor)"""
)

open(rm_path, 'w', encoding='utf-8').write(f)
print('risk_manager.py 修正完了')

# ── bb_monitor.py 修正 ────────────────────────
bb_path = os.path.join(BASE, 'bb_monitor.py')
f = open(bb_path, encoding='utf-8').read()

f = f.replace(
    "    _, sl_dist = rm.calc_tp_sl(atr, 'BB')",
    "    is_jpy_pair = BB_PAIRS[symbol]['is_jpy']\n    _, sl_dist = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy_pair)"
)

open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py 修正完了')

# ── 動作確認 ──────────────────────────────────
import sys
sys.path.insert(0, BASE)
import importlib
import risk_manager as rm
importlib.reload(rm)

tp, sl = rm.calc_tp_sl(0.00017, 'BB', is_jpy=False)
print(f'EUR/GBP(非JPY) ATR=0.00017 → SL:{sl:.5f} TP:{tp:.5f}')

tp, sl = rm.calc_tp_sl(0.05, 'BB', is_jpy=True)
print(f'GBP/JPY(JPY)   ATR=0.05   → SL:{sl:.5f} TP:{tp:.5f}')

tp, sl = rm.calc_tp_sl(0.00017, 'BB', is_jpy=False)
lot = rm.calc_lot(1_000_000, sl, 'EURGBP')
print(f'EUR/GBP ロット: {lot}')

tp, sl = rm.calc_tp_sl(0.05, 'BB', is_jpy=True)
lot = rm.calc_lot(1_000_000, sl, 'GBPJPY')
print(f'GBP/JPY ロット: {lot}')
