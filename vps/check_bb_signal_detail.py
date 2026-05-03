"""calc_bb_signalの詳細デバッグ"""
import MetaTrader5 as mt5
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm

BB_PARAMS = {'bb_period': 20, 'bb_sigma': 1.5, 'exit_sigma': 1.0, 'sl_atr': 3.0, 'rr': 1.0}

mt5.initialize()

for symbol, is_jpy in [('EURUSD', False), ('GBPUSD', False), ('USDJPY', True)]:
    print(f"\n=== {symbol} ===")
    period      = BB_PARAMS['bb_period']
    sigma       = BB_PARAMS['bb_sigma']
    bars_needed = period + 15

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars_needed)
    print(f"  bars_needed:{bars_needed} 取得:{len(rates) if rates is not None else None}")

    if rates is None or len(rates) < period + 1:
        print(f"  → データ不足でNone返却")
        continue

    closes = [r['close'] for r in rates]
    highs  = [r['high']  for r in rates]
    lows   = [r['low']   for r in rates]

    ma  = sum(closes[-period:]) / period
    std = (sum((c - ma)**2 for c in closes[-period:]) / period) ** 0.5
    print(f"  std:{std:.7f}")

    if std == 0:
        print(f"  → std=0でNone返却")
        continue

    upper   = ma + sigma * std
    lower   = ma - sigma * std
    trs     = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
                   abs(lows[i]-closes[i-1])) for i in range(1, len(rates))]
    atr     = sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)
    current = closes[-1]

    pip     = 0.01 if is_jpy else 0.0001
    _, sl_dist = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)
    tp_dist    = sl_dist * BB_PARAMS['rr']

    print(f"  current:{current:.5f} upper:{upper:.5f} lower:{lower:.5f}")
    print(f"  ATR:{atr/pip:.1f}pips SL:{sl_dist/pip:.1f}pips TP:{tp_dist/pip:.1f}pips")

    if current <= lower:
        print(f"  → BUYシグナル ✅")
    elif current >= upper:
        print(f"  → SELLシグナル ✅")
    else:
        print(f"  → シグナルなし（バンド内）")

    # TP/SL値が正常か
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        entry = tick.ask if current <= lower else tick.bid
        tp    = entry + tp_dist if current <= lower else entry - tp_dist
        sl    = entry - sl_dist if current <= lower else entry + sl_dist
        print(f"  発注予定: entry:{entry:.5f} TP:{tp:.5f} SL:{sl:.5f}")

mt5.shutdown()
