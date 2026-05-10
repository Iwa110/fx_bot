"""
リスク管理モジュール v2
- 案A：残高の1.5%をリスク額として固定
- 案B：ATRベースでSL幅を動的計算→ロットを逆算
- ATR下限設定（5分足の過小ATRによるロット過大を防止）
- 将来の案C（ケリー基準）に備えて勝率・RRを記録
"""
import json, os
from datetime import datetime

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(BASE_DIR, 'strategy_stats.json')

RISK_PCT   = 0.015   # 残高の1.5%をリスク許容額
MIN_LOT    = 0.01    # 最小ロット
MAX_LOT    = 0.2     # 最大ロット上限
USDJPY     = 150.0

# ATR下限（時間足・戦略ごとの現実的な最小値）
ATR_FLOOR_JPY    = 0.005   # JPYペア5分足（ほぼ無効化・実ATR優先）
ATR_FLOOR_NONJPY = 0.00002 # 非JPYペア5分足（ほぼ無効化・実ATR優先）
ATR_FLOOR_DAY    = {
    'TRI':     0.0003,
    'MOM_JPY': 0.30,
    'MOM_GBJ': 0.50,
    'CORR':    0.0003,
    'STR':     0.0003,
}

def calc_lot(balance: float, sl_dist: float, symbol: str) -> float:
    risk_amount  = balance * RISK_PCT
    is_jpy       = 'JPY' in symbol
    loss_per_lot = sl_dist * 100_000 * (1.0 if is_jpy else USDJPY)

    if loss_per_lot <= 0:
        return MIN_LOT

    lot = risk_amount / loss_per_lot
    lot = round(lot / 0.01) * 0.01
    return max(MIN_LOT, min(MAX_LOT, lot))

def calc_tp_sl(atr: float, strategy: str, is_jpy: bool = False) -> tuple:
    if strategy.startswith('BB'):
        floor = ATR_FLOOR_JPY if is_jpy else ATR_FLOOR_NONJPY
    else:
        floor = ATR_FLOOR_DAY.get(strategy, 0.0003)
    atr = max(atr, floor)

    MULTIPLIERS = {
        'TRI':     {'tp': 1.5, 'sl': 4.0},
        'MOM_JPY': {'tp': 3.0, 'sl': 1.0},
        'MOM_GBJ': {'tp': 1.0, 'sl': 0.5},
        'CORR':    {'tp': 1.5, 'sl': 2.0},  # corr_bt Stage2最優 PF=1.924
        'STR':     {'tp': 2.5, 'sl': 1.5},
        'BB':      {'tp': 3.0, 'sl': 2.0},
    }
    mult    = MULTIPLIERS.get(strategy, {'tp': 2.0, 'sl': 1.5})
    tp_dist = atr * mult['tp']
    sl_dist = atr * mult['sl']
    return tp_dist, sl_dist

def load_stats() -> dict:
    if os.path.exists(STATS_PATH):
        with open(STATS_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_stats(stats: dict):
    with open(STATS_PATH, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def record_trade(strategy: str, profit: float, sl_dist: float,
                 tp_dist: float, entry: float,
                 symbol: str = '', lot: float = 0.0):
    stats = load_stats()
    if strategy not in stats:
        stats[strategy] = {
            'trades': [], 'total': 0, 'wins': 0, 'losses': 0,
            'total_pnl': 0, 'avg_win': 0, 'avg_loss': 0,
            'win_rate': 0, 'avg_rr': 0, 'kelly': 0, 'last_updated': ''
        }

    s      = stats[strategy]
    is_win = profit > 0
    if sl_dist > 0 and lot > 0:
        is_jpy = 'JPY' in symbol
        loss = sl_dist * 100_000 * lot * (1.0 if is_jpy else USDJPY)
        rr_actual = abs(profit) / loss if loss > 0 else 0
    else:
        rr_actual = 0

    s['trades'].append({
        'time':   datetime.now().strftime('%Y-%m-%d %H:%M'),
        'profit': round(profit, 0),
        'result': 'win' if is_win else 'loss',
        'rr':     round(rr_actual, 2),
    })
    s['trades'] = s['trades'][-200:]

    s['total']     += 1
    s['total_pnl'] += profit
    if is_win: s['wins']   += 1
    else:      s['losses'] += 1

    wins   = [t for t in s['trades'] if t['result']=='win']
    losses = [t for t in s['trades'] if t['result']=='loss']

    s['win_rate'] = round(s['wins'] / s['total'] * 100, 1) if s['total'] > 0 else 0
    s['avg_win']  = round(sum(t['profit'] for t in wins)   / len(wins),   0) if wins   else 0
    s['avg_loss'] = round(sum(t['profit'] for t in losses) / len(losses), 0) if losses else 0
    s['avg_rr']   = round(sum(t['rr']     for t in wins)   / len(wins),   2) if wins   else 0

    if s['total'] >= 20:
        w = s['win_rate'] / 100
        r = s['avg_rr'] if s['avg_rr'] > 0 else 1.5
        kelly = w - (1 - w) / r
        s['kelly'] = round(max(0, kelly), 3)

    s['last_updated']  = datetime.now().strftime('%Y-%m-%d %H:%M')
    stats[strategy]    = s
    save_stats(stats)

def get_kelly_lot(strategy: str, balance: float,
                  sl_dist: float, symbol: str) -> float:
    stats = load_stats()
    s     = stats.get(strategy, {})

    if s.get('total', 0) < 20 or s.get('kelly', 0) <= 0:
        return calc_lot(balance, sl_dist, symbol)

    half_kelly   = s['kelly'] * 0.5
    risk_pct     = min(half_kelly, 0.03)
    is_jpy       = 'JPY' in symbol
    loss_per_lot = sl_dist * 100_000 * (1.0 if is_jpy else USDJPY)
    risk_amount  = balance * risk_pct
    lot          = risk_amount / loss_per_lot if loss_per_lot > 0 else MIN_LOT
    lot          = round(lot / 0.01) * 0.01
    return max(MIN_LOT, min(MAX_LOT, lot))

def print_stats_summary():
    stats = load_stats()
    if not stats:
        print("記録なし")
        return
    print(f"\n{'='*62}")
    print("【戦略別統計・ケリー基準】")
    print(f"{'='*62}")
    print(f"  {'戦略':<14} {'取引':>5} {'勝率':>6} {'平均RR':>7} "
          f"{'Kelly':>7} {'総損益':>10}")
    print(f"  {'-'*57}")
    for strategy, s in sorted(stats.items()):
        kelly_str = f"{s['kelly']:.3f}" if s.get('kelly', 0) > 0 else "未算出"
        print(f"  {strategy:<14} {s['total']:>5}回 {s['win_rate']:>5.1f}% "
              f"{s['avg_rr']:>7.2f} {kelly_str:>7} "
              f"{s['total_pnl']:>+10,.0f}円")
    print(f"{'='*62}")
    print("  ※20回以上でKelly算出・ハーフKelly(×0.5)を使用")

def get_balance() -> float:
    import MetaTrader5 as mt5
    account = mt5.account_info()
    return float(account.balance) if account else 1_000_000.0

def get_atr(symbol: str, strategy: str) -> float:
    import MetaTrader5 as mt5
    import pandas as pd

    # BB戦略はH1足でATR計算（実RR改善のため）
    tf = mt5.TIMEFRAME_H1 if strategy.startswith('BB') else mt5.TIMEFRAME_M5
    n  = 14 if strategy.startswith('BB') else 20

    bars = mt5.copy_rates_from_pos(symbol, tf, 0, n + 5)
    if bars is None or len(bars) < n:
        is_jpy = 'JPY' in symbol
        return ATR_FLOOR_JPY if is_jpy else ATR_FLOOR_NONJPY

    df = pd.DataFrame(bars)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

    is_jpy = 'JPY' in symbol
    floor  = ATR_FLOOR_JPY if is_jpy else ATR_FLOOR_NONJPY
    return max(atr, floor)

if __name__ == '__main__':
    print_stats_summary()
