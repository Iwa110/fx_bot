"""トレーリングSL動作調査"""
import MetaTrader5 as mt5
import json, os
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()

print("=" * 60)
print("【現在のポジション SL状況】")
print("=" * 60)
positions = mt5.positions_get()
if positions:
    for p in positions:
        if 'BB_' not in p.comment and 'MOM' not in p.comment and 'STR' not in p.comment:
            continue
        is_jpy    = 'JPY' in p.symbol
        pip       = 0.01 if is_jpy else 0.0001
        direction = 1 if p.type == 0 else -1
        entry     = p.price_open
        tick      = mt5.symbol_info_tick(p.symbol)
        current   = (tick.bid + tick.ask) / 2 if tick else entry
        profit_pip = (current - entry) * direction / pip

        # ATR取得
        rates  = mt5.copy_rates_from_pos(p.symbol, mt5.TIMEFRAME_M5, 0, 20)
        atr    = 0.0
        atr_pip = 0.0
        if rates is not None and len(rates) >= 2:
            closes = [r['close'] for r in rates]
            highs  = [r['high']  for r in rates]
            lows   = [r['low']   for r in rates]
            trs    = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
                         abs(lows[i]-closes[i-1])) for i in range(1, len(rates))]
            atr     = sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)
            atr_pip = atr / pip

        spread     = (tick.ask - tick.bid) / pip if tick else 0
        sl_dist_pip = abs(p.sl - entry) / pip if p.sl else 0

        stage1_dist = (spread * 2 + 0.14) * pip
        stage2_dist = atr * 0.5
        profit_dist = (current - entry) * direction
        stage1_ok   = profit_dist >= stage1_dist
        stage2_ok   = profit_dist >= stage2_dist

        print(f"\n{p.comment.replace('FXBot_','')} {p.symbol}")
        print(f"  方向: {'買い' if direction==1 else '売り'} entry:{entry:.5f}")
        print(f"  現在価格: {current:.5f}  現在利益: {profit_pip:+.1f}pips")
        print(f"  現在SL: {p.sl:.5f}  SL距離: {sl_dist_pip:.1f}pips")
        print(f"  ATR: {atr_pip:.1f}pips  スプレッド: {spread:.1f}pips")
        s1_msg = '✅達成' if stage1_ok else f"❌未達（あと{(stage1_dist-profit_dist)/pip:.1f}pips）"
        s2_msg = '✅達成' if stage2_ok else f"❌未達（あと{(stage2_dist-profit_dist)/pip:.1f}pips）"
        print(f"  第1段階: {s1_msg}")
        print(f"  第2段階: {s2_msg}")
        if stage2_ok and atr > 0:
            ideal_sl   = current - atr * 1.5 * direction
            improvement = (ideal_sl - p.sl) * direction
            print(f"  理想SL: {ideal_sl:.5f}  改善幅: {improvement/pip:+.1f}pips")
            min_update = atr * 0.1
            if improvement > min_update:
                print(f"  → ⚠️ 更新されるべき！されていない場合はバグ")
            else:
                print(f"  → 最小更新幅未満のため更新なし（正常）")
else:
    print("ポジションなし")

print(f"\n{'=' * 60}")
print("【ログのSL記録（直近5件）】")
print("=" * 60)
log = json.load(open(LOG_PATH, encoding='utf-8'))
for o in log['orders'][-5:]:
    print(f"  {o.get('strategy',''):<14} {o.get('symbol',''):<8} "
          f"entry:{o.get('entry',0):.5f} SL:{o.get('sl',0):.5f}")

mt5.shutdown()
