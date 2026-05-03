"""
保有中BBポジションのTP/SLを現在ATRベースで修正
現在価格との位置関係を確認してから設定
"""
import MetaTrader5 as mt5, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm
import importlib
importlib.reload(rm)

mt5.initialize()
positions = mt5.positions_get()

if not positions:
    print('ポジションなし')
    mt5.shutdown()
    exit()

for p in positions:
    if 'BB_' not in p.comment:
        continue

    symbol    = p.symbol
    is_jpy    = 'JPY' in symbol
    direction = 1 if p.type == 0 else -1  # 0=BUY, 1=SELL
    entry     = p.price_open
    tick      = mt5.symbol_info_tick(symbol)
    current   = (tick.bid + tick.ask) / 2 if tick else entry

    # 現在のATRを取得
    atr_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
    closes    = [r['close'] for r in atr_rates]
    highs     = [r['high']  for r in atr_rates]
    lows      = [r['low']   for r in atr_rates]
    trs       = [max(highs[i]-lows[i],
                     abs(highs[i]-closes[i-1]),
                     abs(lows[i]-closes[i-1]))
                 for i in range(1, len(atr_rates))]
    atr = sum(trs[-14:]) / 14

    tp_dist, sl_dist = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)

    # 現在価格から設定（entryではなく現在価格基準）
    new_tp = round(current + tp_dist * direction, 5)
    new_sl = round(current - sl_dist * direction, 5)

    pip = 0.01 if is_jpy else 0.0001
    print(f"{symbol}: entry={entry} current={current:.5f}")
    print(f"  ATR={atr:.5f} TP幅={tp_dist/pip:.1f}pips SL幅={sl_dist/pip:.1f}pips")
    print(f"  新TP={new_tp} 新SL={new_sl}")

    # BUYの場合：TP > current > SL
    # SELLの場合：SL > current > TP
    valid = True
    if direction == 1:  # BUY
        if not (new_tp > current > new_sl):
            print(f"  ⚠️ TP/SL位置が無効: TP={new_tp} current={current:.5f} SL={new_sl}")
            valid = False
    else:  # SELL
        if not (new_sl > current > new_tp):
            print(f"  ⚠️ TP/SL位置が無効: SL={new_sl} current={current:.5f} TP={new_tp}")
            valid = False

    if valid:
        result = mt5.order_send({
            'action':   mt5.TRADE_ACTION_SLTP,
            'symbol':   symbol,
            'position': p.ticket,
            'tp':       new_tp,
            'sl':       new_sl,
        })
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  ✅ 修正完了")
        else:
            print(f"  ❌ 失敗: {result.retcode} {result.comment}")

mt5.shutdown()
