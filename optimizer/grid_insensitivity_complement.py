"""
grid_insensitivity_complement.py - Candidate A: CI-inverse-gated multi-pair
trend leg as a complement to the Grid, evaluated CONDITIONED on grid
insensitivity windows (grid_insensitivity.py).

Idea (different angle vs closed pullback 案B/D/A which were single-pair GBPJPY):
  The Grid enters only when CI > threshold (range). When CI < threshold (trend)
  the grid is dormant/bleeding. Run a Donchian-breakout + ATR-trail TREND leg on
  the SAME 4 grid pairs, GATED to fire only when CI < threshold (inverse of the
  grid's gate). Ask: does this earn on grid-insensitivity days, is it
  zero/negatively correlated with grid PnL, and does blending lift risk-adjusted
  return without hurting grid's normal (range) days?

Guardrails:
  - next-bar fill: signal on H4 close bar -> enter at NEXT H1 open. No bar-close.
  - 2yr data + IS(<=2025-06)/OOS(>=2025-07) split.
  - all gates use CI (D1, shifted +1d) and Donchian (D1 shift(1)) = t-1 only.
  - complement is sized in R (risk=1R=trail_mult*ATR_entry). Blend converts via a
    swept risk_yen so we compare to grid JPY (pullback_grid_complement style).

Output: grid_insensitivity_complement_result.csv (scorecard) + console tables.
"""

import itertools
import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

OUT = Path(__file__).resolve().parent
RESULT_CSV = OUT / 'grid_insensitivity_complement_result.csv'
TRADES_CSV = OUT / 'grid_insensitivity_complement_trades.csv'

IS_END = pd.Timestamp('2025-06-30')
OOS_START = pd.Timestamp('2025-07-01')
PAIRS = GI.GRID_PAIRS                       # GBPJPY/CHFJPY/NZDJPY/AUDCAD
SPREAD_PIPS = {'GBPJPY': 2, 'CHFJPY': 2, 'NZDJPY': 2, 'AUDCAD': 2}
PIP = {'GBPJPY': 0.01, 'CHFJPY': 0.01, 'NZDJPY': 0.01, 'AUDCAD': 0.0001}
ANN = 252

# small, deliberately coarse grid (overfit guard)
DC = [20, 40]
ADX_TH = [None, 25]
TRAIL = [3.0]
ATR_P = 14


# ─────────────────── indicators (tz-naive, on utc->naive H1) ─────────────────
def calc_atr(df, period):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    pdm = high.diff(); mdm = low.diff().mul(-1)
    pdm = pdm.where((pdm > mdm) & (pdm > 0), 0.0)
    mdm = mdm.where((mdm > pdm) & (mdm > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def prepare(pair):
    """utc H1 -> naive H1 with H4 close map, ADX4, D1 Donchian, ATR, CI, ci_th."""
    h1 = G.load_data(pair).copy()
    h1.index = h1.index.tz_convert(None)
    # H4
    o = h1['open'].resample('4h', label='left', closed='left').first()
    h = h1['high'].resample('4h', label='left', closed='left').max()
    l = h1['low'].resample('4h', label='left', closed='left').min()
    c = h1['close'].resample('4h', label='left', closed='left').last()
    h4 = pd.DataFrame({'open': o, 'high': h, 'low': l, 'close': c}).dropna()
    h4['adx'] = calc_adx(h4, 14)
    h4_close_time = h4.index + pd.Timedelta(hours=3)
    h4map = pd.DataFrame({'h4_close': h4['close'].values, 'adx4': h4['adx'].values}, index=h4_close_time)
    h1 = h1.join(h4map)
    h1['is_h4_close'] = h1.index.isin(h4_close_time)
    # D1 Donchian (completed days only, shift(1))
    d1 = h1[['open', 'high', 'low', 'close']].resample('D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    day_key = h1.index.normalize()
    for p in DC:
        up = d1['high'].rolling(p).max().shift(1); lo = d1['low'].rolling(p).min().shift(1)
        h1[f'dc_up_{p}'] = day_key.map(up); h1[f'dc_lo_{p}'] = day_key.map(lo)
    h1[f'atr_{ATR_P}'] = calc_atr(h1, ATR_P)
    # CI (same engine as grid; tz_convert to naive)
    ci = G.compute_ci_series(G.load_data(pair))
    ci.index = ci.index.tz_convert(None)
    h1['ci'] = ci.reindex(h1.index)
    h1['ci_th'] = GI.V7_CONFIG[pair]['ci_threshold']
    return h1.reset_index(names='time')


def run_breakout(df, pair, dc, adx_th, trail, gate):
    """gate: 'inverse' (CI<=th, trend only) | 'none'. Returns trades DataFrame."""
    spr = SPREAD_PIPS[pair] * PIP[pair]
    opn, high, low, close = df['open'].values, df['high'].values, df['low'].values, df['close'].values
    atr = df[f'atr_{ATR_P}'].values
    adx4 = df['adx4'].values
    is_h4 = df['is_h4_close'].values
    dcu, dcl = df[f'dc_up_{dc}'].values, df[f'dc_lo_{dc}'].values
    ci, ci_th = df['ci'].values, df['ci_th'].values
    time = df['time'].values
    trades, pos, pending = [], None, None
    for i in range(len(df)):
        just = False
        if pending is not None and pos is None:
            ae = atr[i]
            if ae > 0 and not np.isnan(ae):
                side = pending
                entry = opn[i] + (spr / 2) * side
                risk = trail * ae
                pos = {'side': side, 'entry': entry, 'etime': time[i],
                       'stop': entry - risk * side, 'risk': risk, 'ext': entry}
                just = True
            pending = None
        if pos is not None and not just:
            ex = None
            if pos['side'] == 1:
                if opn[i] <= pos['stop']: ex = opn[i]
                elif low[i] <= pos['stop']: ex = pos['stop']
            else:
                if opn[i] >= pos['stop']: ex = opn[i]
                elif high[i] >= pos['stop']: ex = pos['stop']
            if ex is not None:
                net = (ex - pos['entry']) * pos['side'] - spr
                trades.append({'pair': pair, 'entry_time': pos['etime'], 'exit_time': time[i],
                               'side': pos['side'], 'pnl_r': net / pos['risk']})
                pos = None
            else:
                if pos['side'] == 1:
                    pos['ext'] = max(pos['ext'], high[i])
                    pos['stop'] = max(pos['stop'], pos['ext'] - trail * atr[i])
                else:
                    pos['ext'] = min(pos['ext'], low[i])
                    pos['stop'] = min(pos['stop'], pos['ext'] + trail * atr[i])
        if pos is None and pending is None and is_h4[i]:
            up, lo, adxv, civ = dcu[i], dcl[i], adx4[i], ci[i]
            if np.isnan(up) or np.isnan(lo):
                continue
            if gate == 'inverse' and not (not np.isnan(civ) and civ <= ci_th[i]):
                continue
            if adx_th is not None and not (not np.isnan(adxv) and adxv > adx_th):
                continue
            if close[i] > up: pending = 1
            elif close[i] < lo: pending = -1
    return pd.DataFrame(trades)


def daily_R(tr, cal):
    if len(tr) == 0:
        return pd.Series(0.0, index=cal)
    s = tr.copy()
    s['d'] = pd.to_datetime(s['exit_time']).dt.normalize()
    d = s.groupby('d')['pnl_r'].sum()
    return d.reindex(cal).fillna(0.0)


def pf_dd(v):
    v = np.asarray(v, float)
    gp = v[v > 0].sum(); gl = -v[v < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    sh = v.mean() / v.std() * np.sqrt(ANN) if v.std() > 0 else np.nan
    return pf, dd, float(v.sum()), float(v.min()), sh


def main():
    # 1) grid daily state + flags per pair, and grid daily JPY (combined)
    flags = GI.build_all()
    lo = min(d.index.min() for d in flags.values())
    hi = max(d.index.max() for d in flags.values())
    cal = pd.date_range(lo, hi, freq='D')
    grid_jpy = pd.DataFrame({p: flags[p]['grid_realized'].reindex(cal).fillna(0.0) for p in PAIRS})
    grid_comb = grid_jpy.sum(axis=1)
    # combined insens mask: any pair insens that day (grid as a whole not in range mode)
    insens_any = pd.DataFrame({p: flags[p]['insens'].reindex(cal).fillna(False) for p in PAIRS}).any(axis=1)
    # combined: ALL grid pairs dormant/bleeding (strict)
    insens_all = pd.DataFrame({p: flags[p]['insens'].reindex(cal).fillna(False) for p in PAIRS}).all(axis=1)

    prepared = {p: prepare(p) for p in PAIRS}
    rows = []
    best = None
    print('=== Candidate A: CI-inverse-gated multi-pair Donchian trend ===')
    print(f'period {lo.date()}~{hi.date()}  pairs={PAIRS}\n')

    for dc, adx_th, trail, gate in itertools.product(DC, ADX_TH, TRAIL, ['inverse', 'none']):
        # build combined complement daily R (sum across pairs, each trade 1R)
        per = {}
        alltr = []
        for p in PAIRS:
            tr = run_breakout(prepared[p], p, dc, adx_th, trail, gate)
            per[p] = tr
            alltr.append(tr)
        alltr = pd.concat(alltr, ignore_index=True) if alltr else pd.DataFrame()
        comp = daily_R(alltr, cal)

        for seg, m in [('IS', cal <= IS_END), ('OOS', cal >= OOS_START)]:
            cm = comp[m]; gm = grid_comb[m]
            ins = insens_any[m]
            n = len(alltr[(pd.to_datetime(alltr['entry_time']) <= IS_END)]) if seg == 'IS' \
                else len(alltr[(pd.to_datetime(alltr['entry_time']) >= OOS_START)])
            comp_ins = cm[ins].sum(); comp_norm = cm[~ins].sum()
            pf, dd, net, worst, sh = pf_dd(cm.values)
            corr_all = np.corrcoef(gm.values, cm.values)[0, 1] if cm.std() > 0 else np.nan
            corr_ins = np.corrcoef(gm[ins].values, cm[ins].values)[0, 1] if ins.sum() > 2 and cm[ins].std() > 0 else np.nan
            rows.append({'dc': dc, 'adx': 'OFF' if adx_th is None else adx_th, 'trail': trail,
                         'gate': gate, 'seg': seg, 'n_tr': n, 'pf': round(pf, 3),
                         'net_R': round(net, 2), 'worst_R': round(worst, 2),
                         'sharpe': round(sh, 3) if np.isfinite(sh) else np.nan,
                         'comp_insR': round(comp_ins, 2), 'comp_normR': round(comp_norm, 2),
                         'corr_all': round(corr_all, 3) if np.isfinite(corr_all) else np.nan,
                         'corr_ins': round(corr_ins, 3) if np.isfinite(corr_ins) else np.nan})
        # track best inverse-gate config by IS comp_insR
        if gate == 'inverse':
            is_ins = comp[(cal <= IS_END) & insens_any.values].sum()
            if best is None or is_ins > best[0]:
                best = (is_ins, dict(dc=dc, adx_th=adx_th, trail=trail), comp, alltr)

    res = pd.DataFrame(rows)
    res.to_csv(RESULT_CSV, index=False)
    with pd.option_context('display.width', 220, 'display.max_rows', 200):
        print(res.to_string(index=False))
    print(f'\nsaved {RESULT_CSV}')

    # 2) blend sweep with best inverse config
    if best is not None:
        _, bp, comp, alltr = best
        alltr.to_csv(TRADES_CSV, index=False)
        print(f'\n=== BLEND (best inverse cfg {bp}) : grid_comb_jpy + risk_yen*comp_R ===')
        print(f'{"seg":4s} {"R_yen":>9s} {"PF":>6s} {"maxDD":>13s} {"net":>14s} {"worstDay":>13s} {"Sharpe":>7s} {"normRetain":>10s}')
        for seg, m in [('IS', cal <= IS_END), ('OOS', cal >= OOS_START)]:
            gm = grid_comb[m]; cm = comp[m]; ins = insens_any[m]
            g_pf, g_dd, g_net, g_worst, g_sh = pf_dd(gm.values)
            g_norm = gm[~ins].sum()
            print(f'{seg:4s} {"grid-only":>9s} {g_pf:6.2f} {g_dd:13,.0f} {g_net:14,.0f} {g_worst:13,.0f} {g_sh:7.2f} {"-":>10s}')
            for ry in [25_000, 50_000, 100_000, 200_000]:
                bl = gm + ry * cm
                pf, dd, net, worst, sh = pf_dd(bl.values)
                bl_norm = (gm + ry * cm)[~ins].sum()
                retain = bl_norm / g_norm if g_norm != 0 else np.nan
                print(f'{seg:4s} {ry:9,} {pf:6.2f} {dd:13,.0f} {net:14,.0f} {worst:13,.0f} {sh:7.2f} {retain:9.1%}')


if __name__ == '__main__':
    main()
