"""
ATR下限を実ATRに合わせて修正
バックテストで使用した実ATRを下限として設定
"""
import os

BASE = r'C:\Users\Administrator\fx_bot\vps'
rm_path = os.path.join(BASE, 'risk_manager.py')

f = open(rm_path, encoding='utf-8').read()

# 実測ATR（バックテスト60日データより）
f = f.replace(
    """ATR_FLOOR_JPY    = 0.10    # JPYペア5分足（約10pips）
ATR_FLOOR_NONJPY = 0.0005  # 非JPYペア5分足（約5pips）""",
    """ATR_FLOOR_JPY    = 0.020   # JPYペア5分足（約2pips・実ATR基準）
ATR_FLOOR_NONJPY = 0.00010 # 非JPYペア5分足（約1pip・実ATR基準）"""
)

open(rm_path, 'w', encoding='utf-8').write(f)
print('ATR下限修正完了')

# 確認
import sys
sys.path.insert(0, BASE)
import importlib
import risk_manager as rm
importlib.reload(rm)

print('\n■ 修正後のTP/SL確認（実ATRで計算）')
test_cases = [
    ('GBPJPY', 0.060, True),
    ('USDJPY', 0.030, True),
    ('EURJPY', 0.045, True),
    ('AUDJPY', 0.035, True),
    ('EURUSD', 0.00025, False),
    ('GBPUSD', 0.00035, False),
    ('USDCAD', 0.00040, False),
    ('EURGBP', 0.00017, False),
]

for pair, atr_real, is_jpy in test_cases:
    tp, sl = rm.calc_tp_sl(atr_real, 'BB', is_jpy=is_jpy)
    pip    = 0.01 if is_jpy else 0.0001
    sl_pip = sl / pip
    tp_pip = tp / pip
    lot    = rm.calc_lot(1_000_000, sl, pair)
    print(f'  {pair:<8} 実ATR={atr_real:.5f} → '
          f'SL={sl_pip:.1f}pips TP={tp_pip:.1f}pips ロット={lot}')
