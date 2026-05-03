"""
BB戦略の全ペアシグナル状況を確認
エントリー条件を満たしているか・なぜシグナルが出ないかを調査
"""
import MetaTrader5 as mt5
from datetime import datetime

BB_PAIRS = {
    'USDCAD': {'is_jpy': False},
    'GBPJPY': {'is_jpy': True},
    'EURJPY': {'is_jpy': True},
    'USDJPY': {'is_jpy': True},
    'AUDJPY': {'is_jpy': True},
    'EURUSD': {'is_jpy': False},
    'GBPUSD': {'is_jpy': False},
}

BB_PERIOD = 20
BB_SIGMA  = 1.5

mt5.initialize()

print("=" * 70)
print("【BB戦略 全ペア シグナル状況】")
print(f"BB期間:{BB_PERIOD} / σ:{BB_SIGMA} / {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 70)
print(f"{'ペア':<8} {'現在値':>9} {'MA':>9} {'上限':>9} {'下限':>9} "
      f"{'位置':>8} {'σ位置':>7} {'シグナル'}")
print("-" * 70)

for symbol, cfg in BB_PAIRS.items():
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, BB_PERIOD + 20)
    if rates is None or len(rates) < BB_PERIOD + 1:
        print(f"{symbol:<8} データ取得失敗")
        continue

    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]

    # 1本シフト（先読み対策）
    ma  = sum(closes[-(BB_PERIOD+1):-1]) / BB_PERIOD
    std = (sum((c - ma)**2 for c in closes[-(BB_PERIOD+1):-1]) / BB_PERIOD) ** 0.5

    if std == 0:
        print(f"{symbol:<8} 標準偏差ゼロ")
        continue

    upper   = ma + BB_SIGMA * std
    lower   = ma - BB_SIGMA * std
    current = closes[-1]

    # 現在値のσ位置
    sigma_pos = (current - ma) / std

    # ATR
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1])) for i in range(1, len(rates))]
    atr = sum(trs[-14:]) / 14
    pip = 0.01 if cfg['is_jpy'] else 0.0001

    # バンド幅
    band_width = (upper - lower) / pip

    # 現在の位置
    if current <= lower:
        position = "▼下限以下"
        signal   = "🟢 BUYシグナル"
    elif current >= upper:
        position = "▲上限以上"
        signal   = "🔴 SELLシグナル"
    elif sigma_pos < -0.8:
        position = "↓下寄り"
        signal   = "待機中"
    elif sigma_pos > 0.8:
        position = "↑上寄り"
        signal   = "待機中"
    else:
        position = "→中央付近"
        signal   = "待機中"

    # 現在ポジション保有中か
    positions = mt5.positions_get(symbol=symbol)
    has_pos   = positions is not None and len(positions) > 0
    if has_pos:
        signal = "保有中（スキップ）"

    print(f"{symbol:<8} {current:>9.5f} {ma:>9.5f} {upper:>9.5f} {lower:>9.5f} "
          f"{position:>8} {sigma_pos:>+6.2f}σ {signal}")

# 現在のBBポジション確認
print(f"\n{'=' * 70}")
print("【現在のBBポジション】")
print("=" * 70)
all_positions = mt5.positions_get()
bb_positions  = [p for p in all_positions if 'BB_' in p.comment] if all_positions else []

if bb_positions:
    for p in bb_positions:
        pip     = 0.01 if 'JPY' in p.symbol else 0.0001
        dir_jp  = '買い' if p.type == 0 else '売り'
        print(f"  {p.comment.replace('FXBot_','')} {p.symbol} {dir_jp} "
              f"{p.volume}lot entry:{p.price_open:.5f} "
              f"profit:{p.profit:+,.0f}円")
else:
    print("  なし")

mt5.shutdown()
