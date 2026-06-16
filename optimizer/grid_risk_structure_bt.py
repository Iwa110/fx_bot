"""
grid_risk_structure_bt.py - C1/C2: realized リスク構造で req_cap(=realized損失クラスタ)を圧縮。

A1(grid_joint_exposure_cap.py)で「basket req_cap は瞬間 open 露出でなく realized 月次損失の
累積/連続で決まる」と判明。それに効くのは **per-pair の realized テール構造**(既存 combo の発展)。

  C2 per-leg 破局stop (leg_stop_mult): 各レッグに entry∓leg_stop_mult×gw の hard stop を持たせ、
     basket FS(=-fs 一斉決済, A4知見で設定値を最大1.83倍超過)より **前に** intrabar で個別決済。
     一斉ダンプを bounded・staggered な個別 exit に変換 → worst_event / req_cap_999(深テール)を制御。
  C1 progressive cull drain (cull_drain): basket 含み損が cull_frac×fs を割ったら、その足のうちに
     worst レッグを閾値超まで連続 shed(現行=1足1本)。急ギャップでもラダーを薄くして -fs 到達を回避。

ガードレール(規律):
  - leg_stop_mult=None ∧ cull_drain=False で DB.run_bt と完全一致(静的assert)。
  - しきい値は IS=2015-21凍結→OOS/WFO。失敗signature(IS↔OOS逆相関/薄標本/単一局面/崖)点検。
  - 既知の prior: stop を締めると回復可能ラダーを切り PF/DD 悪化(float_stop教訓)。C2は「net を
    多少払っても gap テール(req_cap_999)を bound できるか」を定量化するのが主眼。
  - basket req_cap(99 / 99.9)は grid_joint_stepb と同一の暦月ブロックブートストラップ。

実行: .venv_dukas/bin/python optimizer/grid_risk_structure_bt.py
出力: grid_risk_structure_bt_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY

OUT = Path(__file__).resolve().parent / 'grid_risk_structure_bt_result.csv'
CONTRACT = G.CONTRACT
IS_WIN = D.IS_WIN; OOS_WIN = D.OOS_WIN; WFO_YEARS = [2022, 2023, 2024, 2025]
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
SEED = 42; N_MC = 20000; BLOCK = 3; HORIZON = 60


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def cadchf_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref = float(atr_ac.median()) * 108.0
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df)
    qj = QUOTE_JPY['CADCHF']
    return template_cfg(qj, round(-750_000.0 * (float(atr.median()) * qj) / ref, 0))


def run_bt(cfg, df, atr_series, ci_series, ret24=None, mom_thr=None, cull_frac=None,
           taper=None, long_ml=None, short_ml=None, long_lot_mult=1.0, short_lot_mult=1.0,
           short_block_up=None, leg_stop_mult=None, cull_drain=False, collect=False):
    """DB.run_bt + per-leg 破局stop(leg_stop_mult) + progressive cull drain(cull_drain)。
    leg_stop_mult=None ∧ cull_drain=False で DB.run_bt と完全一致。"""
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']; float_stop = cfg['float_stop']
    lml = long_ml if long_ml is not None else cfg['max_levels']
    sml = short_ml if short_ml is not None else cfg['max_levels']

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp, cull_pnls, legstop_pnls = [], [], [], [], [], [], []
    realized = peak = max_dd = worst = 0.0
    monthly = {}
    def _m(ts, v): monthly[ts.strftime('%Y-%m')] = monthly.get(ts.strftime('%Y-%m'), 0.0) + v
    def llot(lv): return base_lot * long_lot_mult * (taper ** (lv - 1) if taper else 1.0)
    def slot(lv): return base_lot * short_lot_mult * (taper ** (lv - 1) if taper else 1.0)

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult; ci = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= lml; swm = len(short_pos) >= sml

        # TP
        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); tp_pnls.append(v); realized += v; _m(ts, v); long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); tp_pnls.append(v); realized += v; _m(ts, v); short_pos.remove(p)

        # ── C2 per-leg 破局stop (basket FS より前・intrabar 個別決済) ──
        if leg_stop_mult is not None:
            for p in [p for p in long_pos if bl <= p['stop']]:
                v = pj(p['stop'] - p['entry'], p['lot']); legstop_pnls.append(v); realized += v
                _m(ts, v); worst = min(worst, v); long_pos.remove(p)
                if len(long_pos) < lml: b48_ls = None
            for p in [p for p in short_pos if bh >= p['stop']]:
                v = pj(p['entry'] - p['stop'], p['lot']); legstop_pnls.append(v); realized += v
                _m(ts, v); worst = min(worst, v); short_pos.remove(p)
                if len(short_pos) < sml: b48_ss = None

        # basket FS
        if long_pos and sum(pj(bl - p['entry'], p['lot']) for p in long_pos) <= float_stop:
            pp = [pj(bl - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; _m(ts, ev); worst = min(worst, ev)
            long_pos = []; b48_ls = None
        if short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in short_pos) <= float_stop:
            pp = [pj(p['entry'] - bh, p['lot']) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; _m(ts, ev); worst = min(worst, ev)
            short_pos = []; b48_ss = None

        if lwm and len(long_pos) < lml: b48_ls = None
        if swm and len(short_pos) < sml: b48_ss = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(bc - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; _m(ts, ev); worst = min(worst, ev)
            long_pos = []; b48_ls = None
        if b48_ss is not None and (ts - b48_ss).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(p['entry'] - bc, p['lot']) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; _m(ts, ev); worst = min(worst, ev)
            short_pos = []; b48_ss = None

        # cull (DB.run_bt と同一・cull_drain=True で同足連続shed)。1巡=DB と完全一致。
        if cull_frac is not None:
            while len(long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v; _m(ts, v)
                    worst = min(worst, v); long_pos.remove(p)
                    if len(long_pos) < lml: b48_ls = None
                    if not cull_drain: break
                else:
                    break
            while len(short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in short_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v; _m(ts, v)
                    worst = min(worst, v); short_pos.remove(p)
                    if len(short_pos) < sml: b48_ss = None
                    if not cull_drain: break
                else:
                    break

        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        r = ret24[i] if ret24 is not None else np.nan
        mom_long = (mom_thr is None or np.isnan(r) or r > -mom_thr)
        mom_short = (mom_thr is None or np.isnan(r) or r < mom_thr)
        reg_short = True
        if short_block_up is not None and short_block_up[i] == True:
            reg_short = False
        long_ok = ci_ok and mom_long
        short_ok = ci_ok and mom_short and reg_short

        sd = (leg_stop_mult * gw) if leg_stop_mult is not None else None
        if lml > 0:
            if len(long_pos) == 0:
                if long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(1),
                                     'stop': bc - sd if sd else None})
                    if len(long_pos) == lml: b48_ls = ts
            elif len(long_pos) < lml:
                if bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(len(long_pos) + 1),
                                     'stop': bc - sd if sd else None})
                    if len(long_pos) == lml: b48_ls = ts
        if sml > 0:
            if len(short_pos) == 0:
                if short_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(1),
                                      'stop': bc + sd if sd else None})
                    if len(short_pos) == sml: b48_ss = ts
            elif len(short_pos) < sml:
                if bc >= max(p['entry'] for p in short_pos) + gw and short_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(len(short_pos) + 1),
                                      'stop': bc + sd if sd else None})
                    if len(short_pos) == sml: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp + cull_pnls + legstop_pnls
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    out = {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
           'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'n_cull': len(cull_pnls),
           'n_legstop': len(legstop_pnls), 'worst_event': round(worst, 0), 'max_dd': round(max_dd, 0)}
    if collect:
        out['monthly'] = monthly
    return out


def metrics(cfg, df, atr, ci, ret24=None, regime_arr=None, **kw):
    def w(lo=None, hi=None):
        m = D.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        g = dict(kw); r24 = ret24[m] if ret24 is not None else None
        if regime_arr is not None: g['short_block_up'] = regime_arr[m]
        return run_bt(cfg, sub, atr, ci, r24, **g)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nls': full['n_legstop'], 'full_worst': full['worst_event'],
            'full_ntp': full['n_tp'], 'is_pf': isr['pf'] if isr else np.nan, 'oos_pf': oos['pf'],
            'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)) if len(wfo) else np.nan,
            'wfo_min': float(wfo.min()) if len(wfo) else np.nan,
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None or np.isnan(v) or np.isnan(b): return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    iss = f'{m["is_pf"]:4.2f}' if not np.isnan(m['is_pf']) else '  - '
    print(f'{tag:26s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>10,.0f}{mk(m["full_worst"], b["full_worst"]) if b else ""} '
          f'nFS={m["full_nfs"]:2d} nLS={m["full_nls"]:3d} | IS={iss}{mk(m["is_pf"], b["is_pf"]) if b else ""} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} | '
          f'WFOmin={m["wfo_min"]:4.2f}{mk(m["wfo_min"], b["wfo_min"]) if b else ""} {m["wfo_each"]}')


def bootstrap(monthly, rng):
    n = len(monthly); nb = int(np.ceil(HORIZON / BLOCK))
    starts = rng.integers(0, n - BLOCK + 1, size=(N_MC, nb))
    mdd = np.empty(N_MC)
    for i in range(N_MC):
        seq = np.concatenate([monthly[s:s + BLOCK] for s in starts[i]])[:HORIZON]
        eq = np.cumsum(seq); peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        mdd[i] = (peak[1:] - eq).max()
    return mdd


def req_caps(cfg, df, atr, ci, r24, regime_arr, kw):
    g = dict(kw)
    if regime_arr is not None: g['short_block_up'] = regime_arr
    res = run_bt(cfg, df, atr, ci, r24, collect=True, **g)
    s = pd.Series(res['monthly'])
    cal = pd.period_range(min(s.index), max(s.index), freq='M').strftime('%Y-%m')
    col = s.reindex(cal).fillna(0.0).to_numpy()
    rng = np.random.default_rng(SEED); mdd = bootstrap(col, rng)
    ny = col.sum() / (len(col) / 12.0)
    return ny, float(np.percentile(mdd, 99)), float(np.percentile(mdd, 99.9)), res['worst_event']


PAIRS = {
    'AUDCAD': (D.AUDCAD, 1200, COMBO),
    'CADCHF': (cadchf_cfg(), 1200, {}),
    'EURGBP': (D.EURGBP, None, {'short_lot_mult': 0.5, **COMBO}),
}


def main():
    rows = []
    for pair, (cfg, sma_n, extra) in PAIRS.items():
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        regime = DB.sma_regime(df, sma_n) if sma_n else None
        base_kw = dict(extra)
        print('=' * 140); print(f'{pair}  (確定構成: {("R-SMA%d+" % sma_n) if sma_n else ""}{extra})'); print('=' * 140)

        base = metrics(cfg, df, atr, ci, r24, regime_arr=regime, **base_kw)
        show('baseline(確定構成)', base); rows.append((pair, 'baseline', base))
        # 静的一致(combo構成 vs DB.run_bt)
        gref = dict(base_kw)
        if regime is not None: gref['short_block_up'] = regime
        ref = DB.run_bt(cfg, df, atr, ci, ret24=r24, **gref)
        mine = run_bt(cfg, df, atr, ci, r24, **gref)
        assert abs(ref['total_pnl'] - mine['total_pnl']) < 1.0, f'{pair} mismatch {ref["total_pnl"]} vs {mine["total_pnl"]}'
        print(f'  [static-match] DB.run_bt 一致 OK (net={mine["total_pnl"]:,.0f})')

        ny0, r99_0, r999_0, w0 = req_caps(cfg, df, atr, ci, r24, regime, base_kw)
        print(f'  [Step B] net/yr={ny0:,.0f}  req99={r99_0:,.0f}  req99.9={r999_0:,.0f}  worst単発={w0:,.0f}')

        print('\n--- C2 per-leg 破局stop (entry∓m×gw, basket FSより前に個別決済) ---')
        for lsm in [8.0, 6.0, 4.0, 3.0]:
            m = metrics(cfg, df, atr, ci, r24, regime_arr=regime, leg_stop_mult=lsm, **base_kw)
            ny, r99, r999, ws = req_caps(cfg, df, atr, ci, r24, regime, {**base_kw, 'leg_stop_mult': lsm})
            show(f'C2 legstop={lsm}', m, base)
            print(f'      → net/yr={ny:,.0f}({(ny/ny0-1)*100:+.0f}%) req99={r99:,.0f}({(r99/r99_0-1)*100:+.0f}%) '
                  f'req99.9={r999:,.0f}({(r999/r999_0-1)*100:+.0f}%) worst単発={ws:,.0f}')
            rows.append((pair, f'C2_legstop{lsm}', m))

        print('\n--- C1 progressive cull drain (同足で worst を閾値超まで連続shed) ---')
        m = metrics(cfg, df, atr, ci, r24, regime_arr=regime, cull_drain=True, **base_kw)
        ny, r99, r999, ws = req_caps(cfg, df, atr, ci, r24, regime, {**base_kw, 'cull_drain': True})
        show('C1 cull_drain', m, base)
        print(f'      → net/yr={ny:,.0f}({(ny/ny0-1)*100:+.0f}%) req99={r99:,.0f}({(r99/r99_0-1)*100:+.0f}%) '
              f'req99.9={r999:,.0f}({(r999/r999_0-1)*100:+.0f}%) worst単発={ws:,.0f}')
        rows.append((pair, 'C1_cull_drain', m))

        print('\n--- C1+C2 併用 (cull_drain + 最良legstop) ---')
        for lsm in [6.0, 4.0]:
            m = metrics(cfg, df, atr, ci, r24, regime_arr=regime, cull_drain=True, leg_stop_mult=lsm, **base_kw)
            ny, r99, r999, ws = req_caps(cfg, df, atr, ci, r24, regime, {**base_kw, 'cull_drain': True, 'leg_stop_mult': lsm})
            show(f'C1+C2 drain+ls{lsm}', m, base)
            print(f'      → net/yr={ny:,.0f}({(ny/ny0-1)*100:+.0f}%) req99={r99:,.0f}({(r99/r99_0-1)*100:+.0f}%) '
                  f'req99.9={r999:,.0f}({(r999/r999_0-1)*100:+.0f}%) worst単発={ws:,.0f}')
            rows.append((pair, f'C1C2_drain_ls{lsm}', m))
        print()

    out = [{'pair': p, 'tag': t, **{k: v for k, v in m.items() if k != 'wfo_each'}} for p, t, m in rows]
    pd.DataFrame(out).to_csv(OUT, index=False)
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
