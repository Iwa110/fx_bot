"""EUR/GBPをBB戦略から除外するパッチ"""
import os

BASE    = r'C:\Users\Administrator\fx_bot\vps'
bb_path = os.path.join(BASE, 'bb_monitor.py')

f = open(bb_path, encoding='utf-8').read()

# BB_PAIRSからEURGBPを削除
f = f.replace(
    "    'EURGBP':{'is_jpy':False,'max_pos':1},\n",
    ""
)

# MAX_TOTAL_POSを14→13に変更
f = f.replace(
    "MAX_TOTAL_POS = 14",
    "MAX_TOTAL_POS = 13"
)

open(bb_path, 'w', encoding='utf-8').write(f)
print('bb_monitor.py: EUR/GBP除外完了（7ペアに変更）')

# summary_notify.pyも更新
sn_path = os.path.join(BASE, 'summary_notify.py')
f2 = open(sn_path, encoding='utf-8').read()

f2 = f2.replace(
    "BB_PAIRS = ['USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD','EURGBP']",
    "BB_PAIRS = ['USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD']"
)
f2 = f2.replace(
    "MAX_TOTAL = 14",
    "MAX_TOTAL = 13"
)

open(sn_path, 'w', encoding='utf-8').write(f2)
print('summary_notify.py: EUR/GBP除外完了')
print('\n採用ペア（7ペア）: USD/CAD・GBP/JPY・EUR/JPY・USD/JPY・AUD/JPY・EUR/USD・GBP/USD')
