"""
grid_dirbias_improve_bt.py - 方向バイアス(long-only)の更なる改善案を検証。

前回(grid_novel_bt.py)の課題:
  - AUDCAD: hard long-only は full PF↑だが short側の分散喪失で WFOmin 1.32→0.68 悪化。
  - NZDJPY: long-only は強い(IS1.71)が carry-crash年の弱fold(0.58)が残る。
「short を完全に殺す」のでなく賢い方向制御で両立を狙う:

  R レジーム条件付きショート : 長期トレンドが上(close>SMA_N, t-1)の時だけ新規shortを停止。
        レンジ/下落局面では short を復活させ分散を回収(structural bleedの源=上昇トレンド中の
        逆張りshortだけを断つ)。long は常時(構造的に強い側)。N=480/1200/2400h。
  S 非対称サイジング        : short を殺さず深さ(short_ml)とロット(short_lot)だけ縮小して残す。
  T 方向ロット・チルト       : long厚め/short薄めのソフト版(両側常時, lot比のみ)。
  C 採用comboとの併用       : 最良の方向制御に mom+cull+taper を重ねる。

エンジンは per-side max_levels / lot_mult / regime-gate を持つ新実装。全引数デフォルトで
grid_floatstop_bt と完全一致(assert)。IS=2015-21凍結→OOS/WFO, AUDCAD/EURGBP/NZDJPY。

実行: .venv_dukas/bin/python optimizer/grid_dirbias_improve_bt.py
出力: grid_dirbias_improve_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_dirbias_improve_result.csv'
IS_WIN = D.IS_WIN; OOS_WIN = D.OOS_WIN
CONTRACT = G.CONTRACT


def sma_regime(df, n):
    """t-1 の close>SMA_n を bool 配列で返す(True=上昇レジーム=short抑止対象)。"""
    sma = df['close'].rolling(n, min_periods=n).mean()
    return ((df['close'] > sma).shift(1)).to_numpy()


def run_bt(cfg, df, atr_series, ci_series, ret24=None, mom_thr=None,
           cull_frac=None, taper=None, long_ml=None, short_ml=None,
           long_lot_mult=1.0, short_lot_mult=1.0, short_block_up=None, long_block_dn=None,
           collect=False):
    """short_block_up: bool配列(True=上昇レジーム→新規short停止)。long_block_dn: 同(下落→long停止)。
    全引数デフォルトで grid_floatstop_bt.run_backtest と一致。
    collect=True で月次PnL(monthly)・強制決済イベント損(fs_events/b48_events/cull_events)を返す(Step B用)。"""
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']
    float_stop = cfg['float_stop']
    lml = long_ml if long_ml is not None else cfg['max_levels']
    sml = short_ml if short_ml is not None else cfg['max_levels']

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp, cull_pnls = [], [], [], [], [], []
    realized = peak = max_dd = worst = 0.0
    monthly = {}
    def _m(ts, v): monthly[ts.strftime('%Y-%m')] = monthly.get(ts.strftime('%Y-%m'), 0.0) + v

    def llot(level): return base_lot * long_lot_mult * (taper ** (level - 1) if taper else 1.0)
    def slot(level): return base_lot * short_lot_mult * (taper ** (level - 1) if taper else 1.0)

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult; ci = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= lml; swm = len(short_pos) >= sml

        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); tp_pnls.append(v); realized += v; _m(ts, v); long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); tp_pnls.append(v); realized += v; _m(ts, v); short_pos.remove(p)

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

        if cull_frac is not None:
            if len(long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v; _m(ts, v)
                    worst = min(worst, v); long_pos.remove(p)
                    if len(long_pos) < lml: b48_ls = None
            if len(short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in short_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v; _m(ts, v)
                    worst = min(worst, v); short_pos.remove(p)
                    if len(short_pos) < sml: b48_ss = None

        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        r = ret24[i] if ret24 is not None else np.nan
        mom_long = (mom_thr is None or np.isnan(r) or r > -mom_thr)
        mom_short = (mom_thr is None or np.isnan(r) or r < mom_thr)
        # レジーム・ゲート
        reg_long = True; reg_short = True
        if long_block_dn is not None and long_block_dn[i] == False and not np.isnan(closes[i]):
            pass  # placeholder(未使用): long は常時
        if short_block_up is not None:
            su = short_block_up[i]
            reg_short = not (su == True)   # 上昇レジームのバーは新規short停止(NaN時は許可)
        long_ok = ci_ok and mom_long and reg_long
        short_ok = ci_ok and mom_short and reg_short

        if lml > 0:
            if len(long_pos) == 0:
                if long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(1)})
                    if len(long_pos) == lml: b48_ls = ts
            elif len(long_pos) < lml:
                if bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(len(long_pos) + 1)})
                    if len(long_pos) == lml: b48_ls = ts

        if sml > 0:
            if len(short_pos) == 0:
                if short_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(1)})
                    if len(short_pos) == sml: b48_ss = ts
            elif len(short_pos) < sml:
                if bc >= max(p['entry'] for p in short_pos) + gw and short_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(len(short_pos) + 1)})
                    if len(short_pos) == sml: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp + cull_pnls
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    out = {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
           'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'worst_event': round(worst, 0),
           'max_dd': round(max_dd, 0)}
    if collect:
        out['monthly'] = monthly
        out['fs_events'] = fs_pnls         # per-FS event PnL(負)
        out['b48_events'] = b48_pnls       # per-B48 event PnL
        out['cull_events'] = cull_pnls     # per-cull leg PnL(負)
    return out


def metrics(cfg, df, atr, ci, ret24=None, regimes=None, **kw):
    """regimes: dict like {'short_block_up': arr}. 窓ごとにスライス。"""
    def w(lo=None, hi=None):
        m = D.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        g = dict(kw)
        r24 = ret24[m] if ret24 is not None else None
        if regimes:
            for k, v in regimes.items():
                g[k] = v[m]
        return run_bt(cfg, sub, atr, ci, r24, **g)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in [2022, 2023, 2024, 2025]]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_worst': full['worst_event'], 'full_ntp': full['n_tp'],
            'is_pf': isr['pf'] if isr else np.nan, 'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'],
            'oos_dd': oos['max_dd'], 'wfo_med': float(np.median(wfo)) if len(wfo) else np.nan,
            'wfo_min': float(wfo.min()) if len(wfo) else np.nan,
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None or np.isnan(v) or np.isnan(b): return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    iss = f'{m["is_pf"]:4.2f}' if not np.isnan(m['is_pf']) else '  - '
    print(f'{tag:22s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>10,.0f} nFS={m["full_nfs"]:2d} nTP={m["full_ntp"]:4d} | '
          f'IS={iss}{mk(m["is_pf"], b["is_pf"]) if b else ""} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} | '
          f'WFOmed={m["wfo_med"]:4.2f} min={m["wfo_min"]:4.2f}{mk(m["wfo_min"], b["wfo_min"]) if b else ""} {m["wfo_each"]}')


def run_pair(pair, cfg, rows):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    reg = {n: {'short_block_up': sma_regime(df, n)} for n in (480, 1200, 2400)}
    print('=' * 138); print(f'{pair}'); print('=' * 138)
    base = metrics(cfg, df, atr, ci); show('baseline', base); rows.append((pair, 'baseline', base))
    ref = G.run_backtest(pair, cfg, df, atr, ci)
    assert abs(ref['total_pnl'] - base['full_net']) < 1.0, f'{pair} mismatch'

    lo = metrics(cfg, df, atr, ci, long_ml=cfg['max_levels'], short_ml=0)  # short殺し=long-only
    # short_ml=0 だと割り算等問題ないか? len(short_pos)>=0 always True→新規shortしない。OK。
    show('long-only (ref)', lo, base); rows.append((pair, 'long-only', lo))

    print('--- R レジーム条件付きショート (close>SMA_N で新規short停止) ---')
    for n in (480, 1200, 2400):
        m = metrics(cfg, df, atr, ci, regimes=reg[n], short_block_up=reg[n]['short_block_up'])
        show(f'R SMA{n}', m, base); rows.append((pair, f'R_sma{n}', m))

    print('--- S 非対称サイジング (short深さ/ロット縮小・両側常時) ---')
    for tag, kw in [('S sml2', {'short_ml': 2}), ('S sml2+slot0.5', {'short_ml': 2, 'short_lot_mult': 0.5}),
                    ('S slot0.5', {'short_lot_mult': 0.5})]:
        m = metrics(cfg, df, atr, ci, **kw); show(tag, m, base); rows.append((pair, tag, m))

    print('--- T 方向ロット・チルト (long厚/short薄, 両側常時) ---')
    for sl in (0.5, 0.3):
        m = metrics(cfg, df, atr, ci, short_lot_mult=sl); show(f'T long1/short{sl}', m, base)
        rows.append((pair, f'T_slot{sl}', m))

    print('--- C 最良方向制御 + combo(mom+cull+taper) ---')
    for tag, kw in [('C R-SMA1200+combo', {'regimes': reg[1200], 'short_block_up': reg[1200]['short_block_up'],
                                           'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}),
                    ('C sml2+combo', {'short_ml': 2, 'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}),
                    ('C long-only+combo', {'short_ml': 0, 'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7})]:
        m = metrics(cfg, df, atr, ci, ret24=r24, **kw); show(tag, m, base); rows.append((pair, tag, m))
    print()


def main():
    rows = []
    run_pair('AUDCAD', D.AUDCAD, rows)
    run_pair('EURGBP', D.EURGBP, rows)
    run_pair('NZDJPY', GI.V7_CONFIG['NZDJPY'], rows)
    out = [{'pair': p, 'tag': t, **{k: v for k, v in m.items() if k != 'wfo_each'}} for p, t, m in rows]
    pd.DataFrame(out).to_csv(OUT, index=False)
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
