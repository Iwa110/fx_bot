"""
grid_bleed_hedge.py - Candidate B: structural HEDGE for grid bleed windows.

When a grid ladder is deeply underwater (open positions' aggregate unrealized PnL
crosses a trigger toward float_stop), the price is trending against the grid. Open
a SAME-direction-as-the-trend hedge (grid long underwater => price falling =>
SHORT hedge) to offset the ladder's mounting loss, and (ideally) shrink the worst
single forced-exit event and the maxDD.

This is NOT an independent edge (candidate A already showed trend legs on these
pairs have no edge). It is a structural insurance test: does momentum-continuation
from a deep-drawdown trigger offset the grid loss, or does it get whipsawed when
price mean-reverts (grid recovers, hedge loses)?

Engine: grid (v7, faithful to grid_floatstop_bt) and hedge run in ONE bar loop.
  - next-bar fill: hedge trigger evaluated on bar t (using bar-low/high adverse
    aggregate unrealized), hedge entered at bar t+1 OPEN.
  - hedge exit: ATR trail (trail_mult*ATR), OR when the hedged grid side goes flat
    (all TP'd / float-stopped / B48), OR opposite trigger.
  - hedge sized at hedge_lot (fraction of grid lot). PnL in JPY (quote_jpy).
  - IS(<=2025-06)/OOS split. Compare grid-only vs grid+hedge: worst event, maxDD,
    net, PF, and hedge PnL booked on bleed days.

Output: grid_bleed_hedge_result.csv + console.
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

OUT = Path(__file__).resolve().parent
RESULT_CSV = OUT / 'grid_bleed_hedge_result.csv'
IS_END = pd.Timestamp('2025-06-30')
OOS_START = pd.Timestamp('2025-07-01')
PAIRS = GI.GRID_PAIRS
ANN = 252


def sim(cfg, df, atr_s, ci_s, hedge_lot, trig_frac, trail_mult, hedge_on):
    """Run grid + optional hedge. Returns dict of daily series + event stats."""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot, atr_mult, ci_th = cfg['lot'], cfg['atr_mult'], cfg['ci_threshold']
    b48_hours, max_levels, float_stop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']
    trig = trig_frac * abs(float_stop) * -1.0     # negative JPY trigger level

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    # hedge state: side (+1 long / -1 short), entry, stop, ext, risk; one per book
    hedge = None
    pending_hedge = None     # (side,) to enter at next bar open
    grid_daily, hedge_daily = {}, {}
    worst_grid_event = 0.0   # most negative single grid forced-exit event
    worst_comb_day = 0.0
    hedge_bleed_pnl = 0.0    # hedge pnl booked while a bleed was active

    def pj(d):
        return G.pnl_jpy(d, lot)

    def hpj(d):
        return G.pnl_jpy(d, hedge_lot)

    def add(dic, ts, v):
        k = pd.Timestamp(ts).tz_convert(None).normalize()
        dic[k] = dic.get(k, 0.0) + v

    rows = list(df.iterrows())
    for i, (ts, row) in enumerate(rows):
        atr = atr_s.get(ts); ci = ci_s.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue
        gw = atr * atr_mult
        bar_h, bar_l, bar_cl, bar_o = row['high'], row['low'], row['close'], row['open']
        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # ── hedge entry (pending from prev bar) ──
        if hedge_on and pending_hedge is not None and hedge is None:
            side = pending_hedge
            risk = trail_mult * atr
            hedge = {'side': side, 'entry': bar_o, 'stop': bar_o - risk * side,
                     'risk': risk, 'ext': bar_o}
            pending_hedge = None
        elif pending_hedge is not None and hedge is not None:
            pending_hedge = None

        # ── hedge exit (trail stop) ──
        if hedge is not None:
            ex = None
            if hedge['side'] == 1:
                if bar_o <= hedge['stop']: ex = bar_o
                elif bar_l <= hedge['stop']: ex = hedge['stop']
            else:
                if bar_o >= hedge['stop']: ex = bar_o
                elif bar_h >= hedge['stop']: ex = hedge['stop']
            if ex is not None:
                v = hpj((ex - hedge['entry']) * hedge['side'])
                add(hedge_daily, ts, v); hedge_bleed_pnl += v
                hedge = None
            else:
                if hedge['side'] == 1:
                    hedge['ext'] = max(hedge['ext'], bar_h)
                    hedge['stop'] = max(hedge['stop'], hedge['ext'] - trail_mult * atr)
                else:
                    hedge['ext'] = min(hedge['ext'], bar_l)
                    hedge['stop'] = min(hedge['stop'], hedge['ext'] + trail_mult * atr)

        # ── grid TP ──
        day_grid = 0.0
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            v = pj(p['tp'] - p['entry']); day_grid += v; long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            v = pj(p['entry'] - p['tp']); day_grid += v; short_pos.remove(p)

        # ── bleed trigger (adverse extreme aggregate unrealized) ──
        long_unreal = sum(pj(bar_l - p['entry']) for p in long_pos) if long_pos else 0.0
        short_unreal = sum(pj(p['entry'] - bar_h) for p in short_pos) if short_pos else 0.0
        long_bleed = bool(long_pos) and long_unreal <= trig
        short_bleed = bool(short_pos) and short_unreal <= trig

        # ── grid float stop ──
        if long_pos and long_unreal <= float_stop:
            v = long_unreal; day_grid += v
            worst_grid_event = min(worst_grid_event, v)
            long_pos = []; b48_long_start = None
        if short_pos and short_unreal <= float_stop:
            v = short_unreal; day_grid += v
            worst_grid_event = min(worst_grid_event, v)
            short_pos = []; b48_short_start = None

        # ── B48 reset/expiry ──
        if long_was_max and len(long_pos) < max_levels: b48_long_start = None
        if short_was_max and len(short_pos) < max_levels: b48_short_start = None
        if b48_long_start is not None and (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
            v = sum(pj(bar_cl - p['entry']) for p in long_pos); day_grid += v
            worst_grid_event = min(worst_grid_event, v); long_pos = []; b48_long_start = None
        if b48_short_start is not None and (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
            v = sum(pj(p['entry'] - bar_cl) for p in short_pos); day_grid += v
            worst_grid_event = min(worst_grid_event, v); short_pos = []; b48_short_start = None

        if day_grid != 0.0:
            add(grid_daily, ts, day_grid)

        # ── hedge: close if hedged side went flat ──
        if hedge is not None:
            if hedge['side'] == -1 and not long_pos:   # was hedging long ladder
                v = hpj((bar_cl - hedge['entry']) * hedge['side'])
                add(hedge_daily, ts, v); hedge_bleed_pnl += v; hedge = None
            elif hedge is not None and hedge['side'] == 1 and not short_pos:
                v = hpj((bar_cl - hedge['entry']) * hedge['side'])
                add(hedge_daily, ts, v); hedge_bleed_pnl += v; hedge = None

        # ── hedge trigger -> pending next bar (grid long underwater => short hedge) ──
        if hedge_on and hedge is None and pending_hedge is None and i + 1 < len(rows):
            if long_bleed:
                pending_hedge = -1
            elif short_bleed:
                pending_hedge = 1

        # ── grid new entries ──
        ci_ok = (not pd.isna(ci)) and (ci > ci_th)
        if ci_ok:
            if len(long_pos) == 0:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
            elif len(long_pos) < max_levels and bar_cl <= min(p['entry'] for p in long_pos) - gw:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
            if len(short_pos) == 0:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts
            elif len(short_pos) < max_levels and bar_cl >= max(p['entry'] for p in short_pos) + gw:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts

    gd = pd.Series(grid_daily).sort_index()
    hd = pd.Series(hedge_daily).sort_index()
    return {'grid': gd, 'hedge': hd, 'worst_grid_event': worst_grid_event,
            'hedge_bleed_pnl': hedge_bleed_pnl}


def stats(v):
    v = np.asarray(v, float)
    if len(v) == 0 or v.std() == 0:
        gp = v[v > 0].sum() if len(v) else 0; gl = -v[v < 0].sum() if len(v) else 0
        pf = gp / gl if gl > 0 else np.nan
        return dict(pf=pf, maxdd=np.nan, net=float(v.sum()) if len(v) else 0.0,
                    worst=float(v.min()) if len(v) else 0.0, sharpe=np.nan)
    gp = v[v > 0].sum(); gl = -v[v < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    return dict(pf=pf, maxdd=dd, net=float(v.sum()), worst=float(v.min()),
                sharpe=v.mean() / v.std() * np.sqrt(ANN))


def main():
    print('=== Candidate B: grid bleed-window hedge ===\n')
    # hedge_lot fraction sweep, trigger fraction sweep, trail
    HEDGE_LOTS = [0.5, 1.0]
    TRIG_FRACS = [0.4, 0.6]
    TRAIL = 3.0
    rows = []
    for pair in PAIRS:
        cfg = GI.V7_CONFIG[pair]
        df = G.load_data(pair); atr_s = G.compute_atr_series(df); ci_s = G.compute_ci_series(df)
        base = sim(cfg, df, atr_s, ci_s, 0.0, 0.5, TRAIL, hedge_on=False)
        for hl in HEDGE_LOTS:
            for tf in TRIG_FRACS:
                h = sim(cfg, df, atr_s, ci_s, hl, tf, TRAIL, hedge_on=True)
                for seg, lo, hi in [('IS', None, IS_END), ('OOS', OOS_START, None)]:
                    def cut(s):
                        if len(s) == 0: return s
                        m = pd.Series(True, index=s.index)
                        if lo is not None: m &= s.index >= lo
                        if hi is not None: m &= s.index <= hi
                        return s[m]
                    g = cut(base['grid'])
                    hg = cut(h['grid']); hh = cut(h['hedge'])
                    cal = pd.date_range(min(g.index.min(), hg.index.min()),
                                        max(g.index.max(), hg.index.max()), freq='D') if len(g) else []
                    gA = g.reindex(cal).fillna(0.0)
                    comb = (hg.reindex(cal).fillna(0.0) + hh.reindex(cal).fillna(0.0))
                    sg, sc = stats(gA.values), stats(comb.values)
                    rows.append({'pair': pair, 'hl': hl, 'trig': tf, 'seg': seg,
                                 'grid_pf': round(sg['pf'], 2), 'blend_pf': round(sc['pf'], 2),
                                 'grid_maxdd': round(sg['maxdd'] or 0), 'blend_maxdd': round(sc['maxdd'] or 0),
                                 'grid_worstD': round(sg['worst']), 'blend_worstD': round(sc['worst']),
                                 'grid_net': round(sg['net']), 'blend_net': round(sc['net']),
                                 'hedge_net': round(hh.sum())})
    res = pd.DataFrame(rows)
    res.to_csv(RESULT_CSV, index=False)
    with pd.option_context('display.width', 240, 'display.max_rows', 300):
        print(res.to_string(index=False))
    print(f'\nsaved {RESULT_CSV}')
    # summary: does hedge ever improve blend PF AND reduce maxDD vs grid in BOTH IS&OOS?
    print('\n=== net hedge contribution by pair/seg (sum over configs) ===')
    print(res.groupby(['pair', 'seg'])['hedge_net'].agg(['min', 'max', 'mean']).round(0).to_string())


if __name__ == '__main__':
    main()
