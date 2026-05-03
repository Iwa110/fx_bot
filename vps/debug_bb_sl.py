"""
BB戦略のSL更新状況を詳細調査
- 過去の決済済みポジションでSL更新があったか
- 第1段階が発動すべきケースで発動していたか
"""
import MetaTrader5 as mt5
import json, os
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'trade_log.json')

mt5.initialize()

log      = json.load(open(LOG_PATH, encoding='utf-8'))
bb_closed = [c for c in log['closed'] if 'BB_' in c.get('strategy','')]

from_date = datetime(2026, 4, 1, 0, 0)
deals     = mt5.history_deals_get(from_date, datetime.now())
deal_map  = {d.position_id: d for d in deals if d.entry == 1} if deals else {}

print("=" * 65)
print("【BB戦略 決済済みポジション SL分析】")
print("=" * 65)
print(f"{'戦略':<14} {'symbol':<8} {'方向':<4} {'entry':>8} "
      f"{'SL':>8} {'SL幅':>6} {'損益':>8} {'第1段階'}")
print("-" * 65)

stage1_missed = 0
total_bb = 0

for c in bb_closed[-20:]:  # 直近20件
    symbol    = c.get('symbol', '')
    entry     = c.get('entry', 0)
    sl        = c.get('sl', 0)
    lot       = c.get('lot', 0.1)
    direction = 1 if c.get('direction','買い') == '買い' else -1
    is_jpy    = 'JPY' in symbol
    pip       = 0.01 if is_jpy else 0.0001
    conv      = 1.0 if is_jpy else 150.0

    deal   = deal_map.get(c['ticket'])
    profit = deal.profit if deal else c.get('profit', 0)

    sl_dist_pip = abs(sl - entry) / pip if sl else 0
    dir_jp = '買' if direction == 1 else '売'

    # 第1段階：SLがentryに近づいているか確認
    # SLがentryより損失側にある = 第1段階未発動
    sl_vs_entry = (sl - entry) * direction  # プラスなら利益側にSLがある
    stage1_status = '✅発動済' if sl_vs_entry > -pip else '❌未発動'
    if sl_vs_entry <= -pip:
        stage1_missed += 1
    total_bb += 1

    print(f"  {c.get('strategy',''):<12} {symbol:<8} {dir_jp:<4} "
          f"{entry:>8.5f} {sl:>8.5f} {sl_dist_pip:>5.1f}p "
          f"{profit:>+8,.0f}円 {stage1_status}")

print(f"\n第1段階未発動率: {stage1_missed}/{total_bb} "
      f"({stage1_missed/total_bb*100:.1f}%)" if total_bb > 0 else "")

# 現在のBBポジションを詳細確認
print(f"\n{'=' * 65}")
print("【現在のBBポジション詳細】")
print("=" * 65)
positions = mt5.positions_get()
bb_positions = [p for p in positions if 'BB_' in p.comment] if positions else []

if bb_positions:
    for p in bb_positions:
        is_jpy    = 'JPY' in p.symbol
        pip       = 0.01 if is_jpy else 0.0001
        direction = 1 if p.type == 0 else -1
        entry     = p.price_open
        tick      = mt5.symbol_info_tick(p.symbol)
        current   = (tick.bid + tick.ask) / 2 if tick else entry
        spread    = (tick.ask - tick.bid) if tick else 0
        profit_pip = (current - entry) * direction / pip
        sl_dist   = abs(p.sl - entry) / pip if p.sl else 0
        sl_vs_entry = (p.sl - entry) * direction / pip

        # ATR（5分足）
        rates = mt5.copy_rates_from_pos(p.symbol, mt5.TIMEFRAME_M5, 0, 20)
        atr = 0
        if rates is not None and len(rates) >= 2:
            closes=[r['close'] for r in rates]; highs=[r['high'] for r in rates]
            lows=[r['low'] for r in rates]
            trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),
                     abs(lows[i]-closes[i-1])) for i in range(1,len(rates))]
            atr = sum(trs[-14:])/14 if len(trs)>=14 else sum(trs)/len(trs)
        atr_pip = atr / pip

        # 第1段階発動に必要な利益
        commission_pips = 0.14
        stage1_needed = (spread/pip * 2 + commission_pips)  # pips

        print(f"\n{p.comment.replace('FXBot_','')} {p.symbol} "
              f"{'買い' if direction==1 else '売り'}")
        print(f"  entry:{entry:.5f} current:{current:.5f} "
              f"利益:{profit_pip:+.1f}pips")
        print(f"  SL:{p.sl:.5f} SL距離:{sl_dist:.1f}pips "
              f"SL位置(entry比):{sl_vs_entry:+.1f}pips")
        print(f"  ATR:{atr_pip:.1f}pips スプレッド:{spread/pip:.1f}pips")
        print(f"  第1段階発動に必要: {stage1_needed:.1f}pips利益")

        if profit_pip >= stage1_needed:
            ideal_sl = entry - atr * 0.3 * direction
            print(f"  → ✅第1段階発動条件を満たしている")
            print(f"  → 理想SL:{ideal_sl:.5f} "
                  f"現在SL:{p.sl:.5f} "
                  f"差分:{(ideal_sl-p.sl)*direction/pip:+.1f}pips")
        else:
            print(f"  → ❌第1段階未発動（利益不足 "
                  f"{profit_pip:.1f}<{stage1_needed:.1f}pips）")
else:
    print("BBポジションなし")

mt5.shutdown()
