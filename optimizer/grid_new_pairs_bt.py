"""
grid_new_pairs_bt.py - Grid strategy BT screen for new candidate pairs.

Pairs: AUDCAD / NZDJPY / GBPAUD
Sweep: atr_mult x max_levels, with IS(70%) / OOS(30%) split.

Output: optimizer/grid_new_pairs_bt_result.csv
"""

import math
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ────────────────────────────────────────────────────────────────────
if platform.system() == 'Windows':
    DATA_DIR   = r'C:\Users\Administrator\fx_bot\data'
    OUTPUT_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')
else:
    DATA_DIR   = str(Path(__file__).parent.parent / 'data')
    OUTPUT_DIR = Path(__file__).parent

OUTPUT_CSV = str(OUTPUT_DIR / 'grid_new_pairs_bt_result.csv')

# ── Pair config ──────────────────────────────────────────────────────────────
# quote_jpy: 1 pip profit = lot * 10 * quote_jpy  (non-JPY pairs)
#            AUDCAD quote=CAD: CADJPY approx from AUDJPY(113)/AUDCAD(0.90) ~ 102
#            GBPAUD quote=AUD: AUDJPY approx 113
# JPY pairs: 1 pip profit = lot * 1000
PAIR_CFG = {
    'AUDCAD': {'is_jpy': False, 'pip': 0.0001, 'quote_jpy': 102.0, 'lot': 0.02},
    'NZDJPY': {'is_jpy': True,  'pip': 0.01,   'quote_jpy': None,  'lot': 0.02},
    'GBPAUD': {'is_jpy': False, 'pip': 0.0001, 'quote_jpy': 113.5, 'lot': 0.02},
}

ATR_MULT_LIST   = [1.0, 1.5, 2.0, 2.5]
MAX_LEVELS_LIST = [5, 7, 9, 11, 13]
ATR_PERIOD      = 14
CI_PERIOD       = 14
CI_THRESHOLD    = 61.8
B48_HOURS       = 48
IS_RATIO        = 0.70


# ── Data helpers ─────────────────────────────────────────────────────────────
def load_data(pair):
    path = os.path.join(DATA_DIR, pair + '_1h.csv')
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    result = {}
    for target in ['open', 'high', 'low', 'close']:
        for col in df.columns:
            if col.lower() == target and target not in result:
                result[target] = df[col]
    return pd.DataFrame(result).sort_index().dropna()


def compute_atr_series(df, period=ATR_PERIOD):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def compute_ci_series(df_h1, period=CI_PERIOD):
    df_d1 = df_h1.resample('D').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna(subset=['open'])
    h, l, c = df_d1['high'], df_d1['low'], df_d1['close']
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    tr_sum  = tr.rolling(period, min_periods=period).sum()
    h_max   = h.rolling(period, min_periods=period).max()
    l_min   = l.rolling(period, min_periods=period).min()
    hl      = h_max - l_min
    valid   = (hl > 0) & (tr_sum > 0)
    ci_d1   = pd.Series(np.nan, index=df_d1.index)
    ci_d1[valid] = (
        100.0 * np.log10(tr_sum[valid] / hl[valid]) / math.log10(period)
    )
    ci_d1.index = ci_d1.index + pd.Timedelta(days=1)
    return ci_d1.reindex(df_h1.index, method='ffill')


def pnl_jpy(price_diff, cfg):
    lot = cfg['lot']
    if cfg['is_jpy']:
        return (price_diff / cfg['pip']) * lot * 1000
    else:
        return (price_diff / cfg['pip']) * lot * 10 * cfg['quote_jpy']


# ── Simulation ───────────────────────────────────────────────────────────────
def run_bt(df, atr_series, ci_series, atr_mult, max_levels, cfg):
    long_pos        = []
    short_pos       = []
    b48_long_start  = None
    b48_short_start = None

    tp_pnls      = []
    b48_pnls     = []
    b48_pos_pnls = []
    skip_count   = 0
    realized     = 0.0
    peak         = 0.0
    max_dd       = 0.0

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci  = ci_series.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue

        gw     = atr * atr_mult
        bar_h  = row['high']
        bar_l  = row['low']
        bar_cl = row['close']

        long_was_max  = len(long_pos)  >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # TP check
        for p in [p for p in long_pos  if bar_h >= p['tp']]:
            pnl = pnl_jpy(p['tp'] - p['entry'], cfg)
            tp_pnls.append(pnl); realized += pnl; long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            pnl = pnl_jpy(p['entry'] - p['tp'], cfg)
            tp_pnls.append(pnl); realized += pnl; short_pos.remove(p)

        # B48 reset on TP
        if long_was_max  and len(long_pos)  < max_levels: b48_long_start  = None
        if short_was_max and len(short_pos) < max_levels: b48_short_start = None

        # B48 expiry
        if b48_long_start is not None:
            if (ts - b48_long_start).total_seconds() / 3600 >= B48_HOURS:
                pos_pnls  = [pnl_jpy(bar_cl - p['entry'], cfg) for p in long_pos]
                b48_pos_pnls.extend(pos_pnls)
                event_pnl = sum(pos_pnls)
                b48_pnls.append(event_pnl); realized += event_pnl
                long_pos = []; b48_long_start = None

        if b48_short_start is not None:
            if (ts - b48_short_start).total_seconds() / 3600 >= B48_HOURS:
                pos_pnls  = [pnl_jpy(p['entry'] - bar_cl, cfg) for p in short_pos]
                b48_pos_pnls.extend(pos_pnls)
                event_pnl = sum(pos_pnls)
                b48_pnls.append(event_pnl); realized += event_pnl
                short_pos = []; b48_short_start = None

        # DD
        if realized > peak: peak = realized
        dd = peak - realized
        if dd > max_dd: max_dd = dd

        ci_ok = (not pd.isna(ci)) and (ci > CI_THRESHOLD)

        # Long entry
        if len(long_pos) == 0:
            if ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
        elif len(long_pos) < max_levels:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                if len(long_pos) == max_levels: b48_long_start = ts
        else:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                skip_count += 1

        # Short entry
        if len(short_pos) == 0:
            if ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts
        elif len(short_pos) < max_levels:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                if len(short_pos) == max_levels: b48_short_start = ts
        else:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                skip_count += 1

    all_pnls = tp_pnls + b48_pos_pnls
    wins   = [p for p in all_pnls if p >= 0]
    losses = [p for p in all_pnls if p < 0]
    gross_l = abs(sum(losses))
    pf = sum(wins) / gross_l if gross_l > 0 else float('inf')

    n_tp  = len(tp_pnls)
    n_b48 = len(b48_pnls)
    total_ev = n_tp + n_b48 + skip_count
    skip_rate = skip_count / total_ev if total_ev > 0 else 0.0

    return {
        'pf':         round(pf, 3) if pf != float('inf') else 9.999,
        'pnl':        round(realized),
        'n_tp':       n_tp,
        'n_b48':      n_b48,
        'b48_avg':    round(sum(b48_pnls) / n_b48) if n_b48 > 0 else 0,
        'max_dd':     round(max_dd),
        'skip_count': skip_count,
        'skip_rate':  round(skip_rate, 3),
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    rows = []

    for pair, cfg in PAIR_CFG.items():
        print(f'\n{"="*60}')
        print(f'{pair}  (lot={cfg["lot"]}, pip={cfg["pip"]})')
        print(f'{"="*60}')

        df = load_data(pair)
        print(f'data: {len(df)} bars  {df.index[0].date()} ~ {df.index[-1].date()}')

        split_i  = int(len(df) * IS_RATIO)
        df_is    = df.iloc[:split_i]
        df_oos   = df.iloc[split_i:]
        split_dt = df.index[split_i].date()
        print(f'IS: ~ {split_dt} ({len(df_is)} bars)  OOS: {split_dt} ~ ({len(df_oos)} bars)')

        # Precompute indicators on full data, then slice (avoids warm-up loss)
        atr_full = compute_atr_series(df)
        ci_full  = compute_ci_series(df)

        atr_is  = atr_full.loc[df_is.index]
        ci_is   = ci_full.loc[df_is.index]
        atr_oos = atr_full.loc[df_oos.index]
        ci_oos  = ci_full.loc[df_oos.index]

        # Header
        hdr = (f'  {"gm":>4} | {"lv":>3} | {"full_PF":>7} | {"IS_PF":>6} | '
               f'{"OOS_PF":>6} | {"OOS_n":>5} | {"OOS_dd":>7} | {"n_b48":>5} | {"skip%":>5}')
        print(hdr)
        print('  ' + '-' * (len(hdr) - 2))

        for gm in ATR_MULT_LIST:
            for lv in MAX_LEVELS_LIST:
                r_full = run_bt(df,     atr_full, ci_full, gm, lv, cfg)
                r_is   = run_bt(df_is,  atr_is,   ci_is,   gm, lv, cfg)
                r_oos  = run_bt(df_oos, atr_oos,  ci_oos,  gm, lv, cfg)

                mark = ' ◀' if r_oos['pf'] >= 1.5 and r_is['pf'] >= 1.2 and r_oos['n_tp'] + r_oos['n_b48'] >= 20 else ''
                print(
                    f'  {gm:>4.1f} | {lv:>3} | {r_full["pf"]:>7.3f} | {r_is["pf"]:>6.3f} | '
                    f'{r_oos["pf"]:>6.3f} | {r_oos["n_tp"]+r_oos["n_b48"]:>5} | '
                    f'{r_oos["max_dd"]:>7,.0f} | {r_oos["n_b48"]:>5} | {r_oos["skip_rate"]:>5.1%}'
                    + mark
                )

                rows.append({
                    'pair':       pair,
                    'atr_mult':   gm,
                    'max_levels': lv,
                    'full_PF':    r_full['pf'],
                    'full_pnl':   r_full['pnl'],
                    'IS_PF':      r_is['pf'],
                    'IS_pnl':     r_is['pnl'],
                    'OOS_PF':     r_oos['pf'],
                    'OOS_pnl':    r_oos['pnl'],
                    'OOS_n':      r_oos['n_tp'] + r_oos['n_b48'],
                    'OOS_n_b48':  r_oos['n_b48'],
                    'OOS_dd':     r_oos['max_dd'],
                    'OOS_skip_r': r_oos['skip_rate'],
                    'full_n_b48': r_full['n_b48'],
                    'full_dd':    r_full['max_dd'],
                })

    df_out = pd.DataFrame(rows)
    df_out.to_csv(OUTPUT_CSV, index=False)

    # Top candidates summary
    print(f'\n\n{"="*60}')
    print('TOP CANDIDATES  (OOS_PF >= 1.5, IS_PF >= 1.2, OOS_n >= 20)')
    print('='*60)
    cands = df_out[(df_out['OOS_PF'] >= 1.5) & (df_out['IS_PF'] >= 1.2) & (df_out['OOS_n'] >= 20)]
    cands = cands.sort_values('OOS_PF', ascending=False)
    if len(cands):
        print(cands[['pair','atr_mult','max_levels','full_PF','IS_PF','OOS_PF','OOS_n','OOS_dd','OOS_n_b48']].to_string(index=False))
    else:
        print('  (none met criteria)')

    print(f'\nSaved: {OUTPUT_CSV}')


if __name__ == '__main__':
    main()
