"""
grid_entry_filter2_bt.py - モメンタム・ゲート以外のエントリー改善案を検証。

負けパターン診断(grid_entry_analysis.py)=「レンジがトレンドに転化→ラダー深化→float-stop」を
別角度で抑止する3案。すべて t-1 安全・grid_floatstop_bt を1:1踏襲(ゲート無効で静的一致)。

  A dd_throttle : 既存ラダーの含み損が float_stop 予算の dd_frac 超なら追加レベルを見送り
                  (=ラダーの自己状態で深化を停止。max_levels静的とは別の動的キャップ)。
  B adx_gate    : H1 ADX14(t-1) が adx_thr 超なら新規建て(新ラダー&追加)を全面見送り(非方向)。
  C ci_slope    : D1 CI の前日差(t-1)が slope_thr 未満(=CI下降, レンジ崩れ)なら新規建て見送り。

ガードレール: しきい値は IS=2015-2021 で凍結 → OOS=2022-2026・WFO(2022-25)で評価。
合否=baseline(atr1.5)を OOS PF・WFO中央・maxDD で悪化させず、リスク(maxDD/nFS/worst)改善。

実行: python3 optimizer/grid_entry_filter2_bt.py  出力: grid_entry_filter2_bt_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G, grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_entry_filter2_bt_result.csv'
PAIR = 'AUDCAD'
BASE = {**GI.V7_CONFIG[PAIR], 'atr_mult': 1.5}
CONTRACT = G.CONTRACT
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def adx_series(df, period=14):
    h, l, c = df['high'], df['low'], df['close']
    up = h.diff(); dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean().shift(1)   # t-1


def ci_slope_series(df, ci_series):
    """D1 CI の日次差を t-1 で。ci_series は H1 にffillされた D1(+1d shift)値。"""
    d1 = ci_series.resample('D').last()
    slope = d1.diff()
    slope.index = slope.index + pd.Timedelta(days=1)   # t-1 化(翌日に適用)
    return slope.reindex(df.index, method='ffill').to_numpy()


def run_bt(cfg, df, atr_series, ci_series, *, dd_frac=None, adx_arr=None, adx_thr=None,
           ci_slope=None, slope_thr=None):
    qj = cfg.get('quote_jpy', 1.0); lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']
    max_levels = cfg['max_levels']; float_stop = cfg['float_stop']

    def pj(d): return d * lot * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp = [], [], [], [], []
    realized = peak = max_dd = worst = 0.0

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr*atr_mult; ci = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= max_levels; swm = len(short_pos) >= max_levels

        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp']-p['entry']); tp_pnls.append(v); realized += v; long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry']-p['tp']); tp_pnls.append(v); realized += v; short_pos.remove(p)

        if long_pos and sum(pj(bl-p['entry']) for p in long_pos) <= float_stop:
            pp = [pj(bl-p['entry']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None
        if short_pos and sum(pj(p['entry']-bh) for p in short_pos) <= float_stop:
            pp = [pj(p['entry']-bh) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None

        if lwm and len(long_pos) < max_levels: b48_ls = None
        if swm and len(short_pos) < max_levels: b48_ss = None
        if b48_ls is not None and (ts-b48_ls).total_seconds()/3600.0 >= b48_hours:
            pp = [pj(bc-p['entry']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None
        if b48_ss is not None and (ts-b48_ss).total_seconds()/3600.0 >= b48_hours:
            pp = [pj(p['entry']-bc) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None

        peak = max(peak, realized); max_dd = max(max_dd, peak-realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        # B ADX / C CI-slope は新規建て全体を抑止(非方向)
        gate_ok = ci_ok
        if adx_arr is not None and adx_thr is not None:
            a = adx_arr[i]
            if not np.isnan(a) and a > adx_thr: gate_ok = False
        if ci_slope is not None and slope_thr is not None:
            s = ci_slope[i]
            if not np.isnan(s) and s < slope_thr: gate_ok = False

        # A dd_throttle: 既存ラダー含み損が予算超なら「追加」のみ抑止(新ラダーは可)
        def add_ok_long():
            if dd_frac is None: return True
            unreal = sum(pj(bc-p['entry']) for p in long_pos)
            return unreal > dd_frac * float_stop
        def add_ok_short():
            if dd_frac is None: return True
            unreal = sum(pj(p['entry']-bc) for p in short_pos)
            return unreal > dd_frac * float_stop

        if len(long_pos) == 0:
            if gate_ok:
                long_pos.append({'entry': bc, 'tp': bc+gw})
                if len(long_pos) == max_levels: b48_ls = ts
        elif len(long_pos) < max_levels:
            if bc <= min(p['entry'] for p in long_pos)-gw and gate_ok and add_ok_long():
                long_pos.append({'entry': bc, 'tp': bc+gw})
                if len(long_pos) == max_levels: b48_ls = ts

        if len(short_pos) == 0:
            if gate_ok:
                short_pos.append({'entry': bc, 'tp': bc-gw})
                if len(short_pos) == max_levels: b48_ss = ts
        elif len(short_pos) < max_levels:
            if bc >= max(p['entry'] for p in short_pos)+gw and gate_ok and add_ok_short():
                short_pos.append({'entry': bc, 'tp': bc-gw})
                if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp/gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'worst_event': round(worst, 0),
            'max_dd': round(max_dd, 0)}


def win_mask(df, lo, hi):
    m = pd.Series(True, index=df.index)
    if lo: m &= df.index >= pd.Timestamp(lo, tz='UTC')
    if hi: m &= df.index <= pd.Timestamp(hi, tz='UTC')+pd.Timedelta(days=1)
    return m.to_numpy()


def metrics(cfg, df, atr, ci, **gate):
    def w(lo=None, hi=None):
        m = win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        g = dict(gate)
        for k in ('adx_arr', 'ci_slope'):
            if g.get(k) is not None: g[k] = g[k][m]
        return run_bt(cfg, sub, atr, ci, **g)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nb48': full['n_b48'], 'full_worst': full['worst_event'],
            'full_ntp': full['n_tp'], 'is_pf': isr['pf'], 'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'],
            'oos_dd': oos['max_dd'], 'wfo_med': float(np.median(wfo)), 'wfo_min': float(wfo.min()),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def s(v, b, low=False):
        if base is None: return ''
        return ('+' if ((v <= b) if low else (v >= b)) else '-')
    print(f'{tag:18s} fPF={m["full_pf"]:.2f} net={m["full_net"]:>11,.0f} DD={m["full_dd"]:>9,.0f}'
          f'{s(m["full_dd"],base["full_dd"],True) if base else ""} nFS={m["full_nfs"]:2d} nB48={m["full_nb48"]:2d} '
          f'worst={m["full_worst"]:>9,.0f} nTP={m["full_ntp"]:4d} | IS={m["is_pf"]:.2f} '
          f'OOS={m["oos_pf"]:.2f}{s(m["oos_pf"],base["oos_pf"]) if base else ""} | '
          f'WFOmed={m["wfo_med"]:.2f}{s(m["wfo_med"],base["wfo_med"]) if base else ""} min={m["wfo_min"]:.2f} {m["wfo_each"]}')


def main():
    df = load_duk(PAIR); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    adx = adx_series(df).to_numpy(); cisl = ci_slope_series(df, ci)
    rows = []

    base_m = metrics(BASE, df, atr, ci)
    print('BASELINE = 静的最良 atr1.5/ci65/lv5/fs-750k')
    show('baseline', base_m); rows.append({'tag': 'baseline', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})

    print('\n=== A: 含み損アドオン・スロットル (ラダー含み損 > dd_frac×float_stop で追加見送り) ===')
    for f in [0.3, 0.4, 0.5, 0.6, 0.75]:
        m = metrics(BASE, df, atr, ci, dd_frac=f); show(f'A dd_frac={f}', m, base_m)
        rows.append({'tag': f'A_dd{f}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n=== B: ADX(H1,t-1) ゲート (adx > thr で新規建て全面停止) ===')
    print(f'  (参考 IS ADX分位: '
          f'{np.nanpercentile(adx[win_mask(df,*IS_WIN)],[50,70,80,90]).round(1)})')
    for t in [20, 25, 30, 35]:
        m = metrics(BASE, df, atr, ci, adx_arr=adx, adx_thr=float(t)); show(f'B adx_thr={t}', m, base_m)
        rows.append({'tag': f'B_adx{t}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n=== C: CI傾き ゲート (D1 CI 前日差 < thr=CI下降 で新規建て停止) ===')
    for t in [0.0, -1.0, -2.0]:
        m = metrics(BASE, df, atr, ci, ci_slope=cisl, slope_thr=float(t)); show(f'C slope_thr={t}', m, base_m)
        rows.append({'tag': f'C_slope{t}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
