"""
portfolio_regime.py - Case B: regime overlay to cut Grid trend-tail.

Grid weakness = float-stop tails when a range breaks into a trend AFTER entry.
The grid already gates entries on CI (ranging). This adds a TREND-STRENGTH gate
on top, using only info up to t-1 (causal). Two variants:

  noadd : block NEW grid entries while trend is strong (ladder stays shallow).
  exit  : additionally force-flat all positions at bar close when trend strong
          (book a small loss before it grows into a float-stop).

Trend signals (both tested), per pair on 1h close, value at t-1:
  ER  : Kaufman efficiency ratio over n bars (|net move| / sum|bar move|).
  ADX : Wilder ADX(14).

Adoption (per pair, vs baseline grid): OOS maxDD AND worst-single shrink, with
OOS net retention >= 80%. IS/OOS robust, minimal params.
"""

import numpy as np
import pandas as pd
import grid_floatstop_bt as G
from portfolio_meta_bt import V7_CONFIG, metrics, IS_END, OOS_START, events_to_daily

PAIRS = ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD']


def efficiency_ratio(close, n):
    net = close.diff(n).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (net / vol.replace(0, np.nan))


def adx(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    up = h.diff(); dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def grid_engine(cfg, df, atr_series, ci_series, block=None, force_exit=None):
    """Returns events list[(ts,pnl)]. block/force_exit: bool Series (causal),
    block => no new entries; force_exit => flat all at bar close."""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot, atr_mult, ci_th = cfg['lot'], cfg['atr_mult'], cfg['ci_threshold']
    b48_h, max_lv, fstop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']
    long_pos, short_pos = [], []
    bl_start = bs_start = None
    ev = []
    pj = lambda d: G.pnl_jpy(d, lot)
    for ts, row in df.iterrows():
        atr = atr_series.get(ts); ci = ci_series.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue
        gw = atr * atr_mult
        bh, bl, bc = row['high'], row['low'], row['close']
        lwm = len(long_pos) >= max_lv; swm = len(short_pos) >= max_lv
        # TP
        for p in [p for p in long_pos if bh >= p['tp']]:
            ev.append((ts, pj(p['tp'] - p['entry']))); long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            ev.append((ts, pj(p['entry'] - p['tp']))); short_pos.remove(p)
        # float stop
        if long_pos and sum(pj(bl - p['entry']) for p in long_pos) <= fstop:
            ev.append((ts, sum(pj(bl - p['entry']) for p in long_pos))); long_pos = []; bl_start = None
        if short_pos and sum(pj(p['entry'] - bh) for p in short_pos) <= fstop:
            ev.append((ts, sum(pj(p['entry'] - bh) for p in short_pos))); short_pos = []; bs_start = None
        # b48 reset
        if lwm and len(long_pos) < max_lv: bl_start = None
        if swm and len(short_pos) < max_lv: bs_start = None
        # b48 expiry
        if bl_start is not None and (ts - bl_start).total_seconds() / 3600 >= b48_h:
            ev.append((ts, sum(pj(bc - p['entry']) for p in long_pos))); long_pos = []; bl_start = None
        if bs_start is not None and (ts - bs_start).total_seconds() / 3600 >= b48_h:
            ev.append((ts, sum(pj(p['entry'] - bc) for p in short_pos))); short_pos = []; bs_start = None
        # regime force-exit (book at close)
        if force_exit is not None and bool(force_exit.get(ts, False)):
            if long_pos:
                ev.append((ts, sum(pj(bc - p['entry']) for p in long_pos))); long_pos = []; bl_start = None
            if short_pos:
                ev.append((ts, sum(pj(p['entry'] - bc) for p in short_pos))); short_pos = []; bs_start = None
        # entries
        ci_ok = (not pd.isna(ci)) and (ci > ci_th)
        blocked = bool(block.get(ts, False)) if block is not None else False
        if ci_ok and not blocked:
            if len(long_pos) == 0:
                long_pos.append({'entry': bc, 'tp': bc + gw})
                if len(long_pos) == max_lv: bl_start = ts
            elif len(long_pos) < max_lv and bc <= min(p['entry'] for p in long_pos) - gw:
                long_pos.append({'entry': bc, 'tp': bc + gw})
                if len(long_pos) == max_lv: bl_start = ts
            if len(short_pos) == 0:
                short_pos.append({'entry': bc, 'tp': bc - gw})
                if len(short_pos) == max_lv: bs_start = ts
            elif len(short_pos) < max_lv and bc >= max(p['entry'] for p in short_pos) + gw:
                short_pos.append({'entry': bc, 'tp': bc - gw})
                if len(short_pos) == max_lv: bs_start = ts
    return ev


def seg(daily, which):
    if which == 'IS': return daily[daily.index <= IS_END]
    if which == 'OOS': return daily[daily.index >= OOS_START]
    return daily


def main():
    print('=== Case B: regime overlay on Grid (per-pair, causal trend gate) ===')
    rows = []
    robust = []
    for pair in PAIRS:
        df = G.load_data(pair)
        atr_s = G.compute_atr_series(df)
        ci_s = G.compute_ci_series(df)
        cfg = V7_CONFIG[pair]
        # baseline
        base = events_to_daily(grid_engine(cfg, df, atr_s, ci_s))
        # signals (causal: shift 1 bar)
        er24 = efficiency_ratio(df['close'], 24).shift(1)
        er48 = efficiency_ratio(df['close'], 48).shift(1)
        adx14 = adx(df, 14).shift(1)
        print(f'\n----- {pair}  (base net {metrics(base)["net"]:,.0f}, '
              f'maxDD {metrics(base)["maxdd"]:,.0f}, worst {metrics(base)["worst"]:,.0f}) -----')
        print(f'{"overlay":22s} {"var":5s}|{"seg":4s} {"PF":>5s} {"net":>11s} {"maxDD":>11s} '
              f'{"worst":>11s} {"retain%":>7s}')
        configs = []
        for th in [0.35, 0.50]:
            configs.append((f'ER24>{th}', (er24 > th)))
            configs.append((f'ER48>{th}', (er48 > th)))
        for th in [25, 35]:
            configs.append((f'ADX>{th}', (adx14 > th)))
        base_seg = {w: metrics(seg(base, w)) for w in ['FULL', 'IS', 'OOS']}
        for nm, sig in configs:
            sig = sig.reindex(df.index).fillna(False)
            for var, kw in [('noadd', dict(block=sig)), ('exit', dict(force_exit=sig, block=sig))]:
                d = events_to_daily(grid_engine(cfg, df, atr_s, ci_s, **kw))
                seg_m = {}
                for w in ['IS', 'OOS']:
                    m = metrics(seg(d, w)); b = base_seg[w]
                    retain = (m['net'] / b['net'] * 100) if b['net'] != 0 else float('nan')
                    seg_m[w] = (m, retain)
                    flag = ''
                    if w == 'OOS' and m['maxdd'] <= b['maxdd'] and abs(m['worst']) <= abs(b['worst']) and retain >= 80:
                        flag = ' <=ADOPT'
                    print(f'{nm:22s} {var:5s}|{w:4s} {m["pf"]:5.2f} {m["net"]:11,.0f} '
                          f'{m["maxdd"]:11,.0f} {m["worst"]:11,.0f} {retain:7.1f}{flag}')
                    rows.append({'pair': pair, 'overlay': nm, 'var': var, 'seg': w,
                                 'pf': round(m['pf'], 2), 'net': round(m['net']),
                                 'maxdd': round(m['maxdd']), 'worst': round(m['worst']),
                                 'retain_pct': round(retain, 1)})
                # ROBUST = non-degenerate tail reduction on BOTH IS & OOS:
                #   net retain>=80 both, AND (maxDD reduced OR worst reduced) on IS
                #   (IS holds the real adverse-trend tail), AND OOS not worse.
                mIS, rIS = seg_m['IS']; mOOS, rOOS = seg_m['OOS']
                bIS, bOOS = base_seg['IS'], base_seg['OOS']
                tail_cut_IS = (mIS['maxdd'] < bIS['maxdd'] * 0.95) or (abs(mIS['worst']) < abs(bIS['worst']) * 0.95)
                if rIS >= 80 and rOOS >= 80 and tail_cut_IS and mOOS['maxdd'] <= bOOS['maxdd'] * 1.05:
                    robust.append((pair, nm, var, round(rIS, 0), round(rOOS, 0),
                                   round(mIS['maxdd']), round(bIS['maxdd']),
                                   round(mIS['worst']), round(bIS['worst'])))

    pd.DataFrame(rows).to_csv('portfolio_regime_result.csv', index=False)
    print('\n=== ROBUST set: net retain>=80% BOTH IS&OOS, IS tail (maxDD or worst) '
          'cut >=5%, OOS maxDD not worse ===')
    if not robust:
        print('  (none) - no overlay cuts the IS tail while keeping >=80% net on both segments')
    else:
        print(f'  {"pair":7s} {"overlay":12s} {"var":6s} {"rIS%":>5s} {"rOOS%":>6s} '
              f'{"IS_maxDD":>10s} {"base":>10s} {"IS_worst":>10s} {"base":>10s}')
        for r in robust:
            print(f'  {r[0]:7s} {r[1]:12s} {r[2]:6s} {r[3]:5.0f} {r[4]:6.0f} '
                  f'{r[5]:10,.0f} {r[6]:10,.0f} {r[7]:10,.0f} {r[8]:10,.0f}')
    print('Saved: portfolio_regime_result.csv')


if __name__ == '__main__':
    main()
