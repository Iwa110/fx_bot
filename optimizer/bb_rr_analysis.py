"""BB戦略 実RR分析 / 勝ち負け構造 + TP/SL/trail フロー診断"""
import pandas as pd
import numpy as np
from pathlib import Path

HISTORY_CSV = Path(__file__).parent / 'history.csv'

df = pd.read_csv(HISTORY_CSV)
df['open_time']  = pd.to_datetime(df['open_time'])
df['close_time'] = pd.to_datetime(df['close_time'])
df['hold_min']   = (df['close_time'] - df['open_time']).dt.total_seconds() / 60

bb = df[(df['magic'] == 20250001) &
        (df['symbol'].isin(['GBPJPY', 'USDJPY', 'EURJPY']))].copy()

bb['exit_type'] = bb['comment'].apply(lambda x: 'tp' if '[tp' in str(x) else 'sl_trail')

wins  = bb[bb['profit'] > 0]
loses = bb[bb['profit'] < 0]

print('=== BB戦略 実RR構造分析 ===')
print(f'全トレード: n={len(bb)}  WR={len(wins)/len(bb)*100:.1f}%')
print(f'TP到達: {len(bb[bb["exit_type"]=="tp"])}件 / SL/trail: {len(bb[bb["exit_type"]=="sl_trail"])}件')

avg_w = wins.profit.mean()
avg_l = abs(loses.profit.mean())
real_rr = avg_w / avg_l
print(f'\n実RR = avg_win/avg_loss = {avg_w:.1f}/{avg_l:.1f} = {real_rr:.3f}')
print(f'設計RR = 1.5  → 乖離 = {(1.5 - real_rr)/1.5*100:.1f}%未達')
print(f'PF = WR*RR / (1-WR) = {(len(wins)/len(bb)*real_rr) / (1 - len(wins)/len(bb)):.3f}  (WR×RR={len(wins)/len(bb)*real_rr:.3f})')

# ペア別
print('\n=== ペア別 実RR ===')
for sym in ['GBPJPY', 'USDJPY', 'EURJPY']:
    sub   = bb[bb.symbol == sym]
    w_sub = sub[sub.profit > 0]
    l_sub = sub[sub.profit < 0]
    tp_n  = sub[sub.exit_type == 'tp'].shape[0]
    if len(w_sub) > 0 and len(l_sub) > 0:
        rr = w_sub.profit.mean() / abs(l_sub.profit.mean())
        pf = (w_sub.profit.sum()) / abs(l_sub.profit.sum()) if l_sub.profit.sum() != 0 else 99
        print(f'  {sym}: PF={pf:.3f}  WR={len(w_sub)/len(sub)*100:.1f}%  '
              f'实RR={rr:.3f}  n={len(sub)}  TP到達={tp_n}件')
    else:
        print(f'  {sym}: n={len(sub)} (勝ち/負け不足)')

# 決済種別 × 損益分布
print('\n=== 決済種別 × 損益分布 ===')
tp_trades    = bb[bb.exit_type == 'tp']
trail_win    = bb[(bb.exit_type == 'sl_trail') & (bb.profit > 0)]
trail_loss   = bb[(bb.exit_type == 'sl_trail') & (bb.profit < 0)]
print(f'  TP到達:     n={len(tp_trades):3d}  avg={tp_trades.profit.mean():+.0f}円  avg_hold={tp_trades.hold_min.mean():.0f}分')
print(f'  trail/SL勝: n={len(trail_win):3d}  avg={trail_win.profit.mean():+.0f}円  avg_hold={trail_win.hold_min.mean():.0f}分')
print(f'  trail/SL負: n={len(trail_loss):3d}  avg={trail_loss.profit.mean():+.0f}円  avg_hold={trail_loss.hold_min.mean():.0f}分')

# 勝ちの損益分布
print('\n=== 勝ちの損益分布（trail vs TP到達推定） ===')
p_bins = [0, 200, 500, 1000, 2000, 5000, 99999]
for lo, hi in zip(p_bins, p_bins[1:]):
    subset = wins[(wins.profit >= lo) & (wins.profit < hi)]
    label  = f'{lo}-{hi}円' if hi < 99999 else f'{lo}円+'
    avg_h  = subset.hold_min.mean() if len(subset) > 0 else 0
    print(f'  {label:15s}: {len(subset):3d}件  avg_hold={avg_h:.0f}分')

# 保有時間別平均損益
print('\n=== 勝ちトレードの保有時間分布 ===')
w_bins = [0, 30, 60, 120, 240, 480, 99999]
for lo, hi in zip(w_bins, w_bins[1:]):
    subset = wins[(wins.hold_min >= lo) & (wins.hold_min < hi)]
    avg_p  = subset.profit.mean() if len(subset) > 0 else 0
    label  = f'{lo}-{hi}min' if hi < 99999 else f'{lo}min+'
    print(f'  {label:15s}: {len(subset):3d}件  avg={avg_p:+.0f}円')

# ペア別 実ATR推定（負けトレードからSL逆算）
print('\n=== ペア別 実ATR推定（負けトレードからSL逆算）===')
for sym, sl_mult in [('USDJPY', 3.0), ('GBPJPY', 3.0), ('EURJPY', 2.5)]:
    subset   = loses[loses.symbol == sym]
    if len(subset) == 0:
        print(f'  {sym}: 負けトレードなし')
        continue
    avg_loss = abs(subset.profit.mean())
    # JPYペア: 0.2lot基準 → 0.01pip = 200円/lot × lot
    avg_lot  = subset.lots.mean()
    pip_val  = avg_lot * 100_000 * 0.01  # 0.01pip × lot × 100000通貨
    sl_pips  = avg_loss / pip_val
    atr_est  = sl_pips / sl_mult
    print(f'  {sym}: avg_loss={-avg_loss:.0f}円  avg_lot={avg_lot:.2f}  '
          f'SL≈{sl_pips:.1f}pips  ATR≈{atr_est:.1f}pips(sl_mult={sl_mult})')

# Stage3発動閾値との比較
print('\n=== Stage3発動閾値 vs TP距離の比較 ===')
print('  bb_monitor:   SL = H1_ATR × sl_mult (3.0)  TP = SL × 1.5 = H1_ATR × 4.5')
print('  trail_monitor: Stage3発動 = 5m_ATR × 1.2')
print('  H1 ATR ≈ 5〜8倍 × 5m ATR として:')
for ratio in [5, 6, 8]:
    stage3_as_h1 = 1.2 / ratio
    pct_of_tp = stage3_as_h1 / 4.5 * 100
    print(f'    H1=5m×{ratio}: Stage3発動 = H1_ATR×{stage3_as_h1:.2f} ({pct_of_tp:.0f}% of TP)')
print('  → trail がTP到達前の非常に早い段階で発動 → 小利益刈り取りの原因')

print('\n=== 結論 ===')
print(f'  TP到達率 = {len(tp_trades)/len(bb)*100:.1f}% ({len(tp_trades)}/{len(bb)})')
print(f'  trail/SL勝ち比率 = {len(trail_win)/len(bb)*100:.1f}% ({len(trail_win)}/{len(bb)})')
print(f'  trail/SL勝ちの平均 = {trail_win.profit.mean():.0f}円 (設計TPの大幅未達)')
print(f'  根本原因: H1 ATRベースのTP vs 5m ATRベースのStage3trail → trail発動がTP前数%で起動')
