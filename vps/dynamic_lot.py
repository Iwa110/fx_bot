"""
dynamic_lot.py - Kelly adaptive aggressive lot sizing (exness demo only)
"""
import json
import os

import MetaTrader5 as mt5

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(BASE_DIR, 'strategy_stats.json')

_FLOOR     = 0.015
_CAP       = 0.75


def get_risk_pct(strategy: str) -> float:
    try:
        with open(STATS_PATH, encoding='utf-8') as f:
            stats = json.load(f)
        s = stats.get(strategy, {})
        count = s.get('total', 0)
        if count < 20:
            return _FLOOR
        w = s.get('win_rate', 0.0) / 100.0
        r = s.get('avg_rr', 0.0)
        if r <= 0:
            return _FLOOR
        kelly = w - (1.0 - w) / r
        return max(_FLOOR, min(kelly, _CAP))
    except Exception:
        return _FLOOR


def calc_aggressive_lot(balance: float, sl_dist: float, symbol: str,
                        strategy: str, min_lot: float = 0.01) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return min_lot

    tick_size  = info.trade_tick_size
    tick_value = info.trade_tick_value
    if tick_size <= 0 or tick_value <= 0 or sl_dist <= 0:
        return min_lot

    risk_amount = balance * get_risk_pct(strategy)
    ticks_in_sl = sl_dist / tick_size
    lot = risk_amount / (ticks_in_sl * tick_value)
    lot = round(round(lot / 0.01) * 0.01, 2)
    return max(min_lot, lot)
