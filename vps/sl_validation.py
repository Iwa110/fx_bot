import yfinance as yf
import pandas as pd
import numpy as np

# --- Data fetch ---
df = yf.download('EURGBP=X', period='2y', interval='1d', auto_adjust=True, progress=False)
df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
df = df[['High', 'Low', 'Close']].dropna()

# --- ATR14 ---
high = df['High']
low  = df['Low']
prev_close = df['Close'].shift(1)

tr = pd.concat([
    high - low,
    (high - prev_close).abs(),
    (low  - prev_close).abs()
], axis=1).max(axis=1)

atr = tr.ewm(span=14, adjust=False).mean()
atr = atr.dropna()

# --- Parameters ---
sl_dist  = 0.0055
tp_mult  = 1.5
sl_mult  = 4.0

atr_mean   = atr.mean()
atr_median = atr.median()
atr_std    = atr.std()
atr_p25    = atr.quantile(0.25)
atr_p75    = atr.quantile(0.75)
atr_p90    = atr.quantile(0.90)

sl_vs_mean   = sl_dist / atr_mean
sl_vs_median = sl_dist / atr_median

tp_dist  = sl_dist * (tp_mult / sl_mult)  # ATRbase: tp=ATR*1.5, sl=ATR*4.0
# RR = tp_dist / sl_dist
rr = tp_mult / sl_mult

# SL reach rate: daily range > sl_dist
daily_range = high - low
sl_reach_rate = (daily_range > sl_dist).mean() * 100

# --- Output ---
print('=' * 50)
print('EURGBP Daily ATR14 Analysis')
print('=' * 50)
print(f'Bars used         : {len(atr)}')
print()
print('[ATR14 Distribution]')
print(f'  Mean            : {atr_mean:.5f}')
print(f'  Std             : {atr_std:.5f}')
print(f'  P25             : {atr_p25:.5f}')
print(f'  Median (P50)    : {atr_median:.5f}')
print(f'  P75             : {atr_p75:.5f}')
print(f'  P90             : {atr_p90:.5f}')
print()
print('[SL Distance Evaluation]')
print(f'  sl_dist         : {sl_dist:.4f}')
print(f'  sl / ATR_mean   : {sl_vs_mean:.3f}x')
print(f'  sl / ATR_median : {sl_vs_median:.3f}x')
print(f'  sl_mult param   : {sl_mult:.1f}x  (ATR-based SL = ATR * {sl_mult})')
print()
print('[RR Ratio]')
print(f'  tp_mult         : {tp_mult}')
print(f'  sl_mult         : {sl_mult}')
print(f'  RR (tp/sl mult) : {rr:.3f}  ({tp_mult}/{sl_mult})')
print()
print('[SL Reach Rate]')
print(f'  sl_dist         : {sl_dist:.4f}')
print(f'  Daily range > sl: {sl_reach_rate:.1f}%')
print('=' * 50)