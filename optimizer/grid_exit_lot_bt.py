"""
grid_exit_lot_bt.py - 決済条件 + ロット構造による Grid PF 改善案の検証。

これまでの採用候補(grid_dd_reduction_bt.py: mom24=2.0 + cull0.5 + taper0.7)は
「エントリー抑止 + ポジション側リスク構造」だった。本検証は直交する「決済条件」を狙う:

  X1 TP距離倍率   : 各レッグの TP = gw × tp_mult (現行=1.0)。
                    狭→約定速い(高WR・小利) / 広→利を伸ばす。レンジ捕捉効率を最適化。
  X2 バスケットTP : 同方向ラダーの合計含み益が basket_tp(JPY) 到達で全レッグ一括利確。
                    平均回帰の戻りでラダー全体を一気に回収(深いラダーの早期解放)。
  X3 バスケット・トレール : 合計含み益のピークを追跡し、arm 到達後に give-back 分だけ
                    戻したら全決済。トレンド転換時に含み益を確保。
  X4 B48時間      : 強制時間決済 b48_hours スイープ(24/36/48/72)。短縮=塩漬け早期解放。
  L1 ロット・テーパー(対照) / L2 ピラミッド(逆=悪化想定の対照)。
  C  採用comboに最良決済を重ねる。

ガードレール: 全機能OFFで grid_floatstop_bt と完全一致(assert)。しきい値は IS=2015-21
で凍結 → OOS=2022-26・WFO(2022-25)。合否=IS-selectable ∧ OOS非悪化 ∧ リスク改善。
構造性=EURGBP(テンプレ, 再チューニング無し)転移。

実行: .venv_dukas/bin/python optimizer/grid_exit_lot_bt.py
出力: grid_exit_lot_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_exit_lot_result.csv'
IS_WIN = D.IS_WIN; OOS_WIN = D.OOS_WIN
CONTRACT = G.CONTRACT


def run_bt(cfg, df, atr_series, ci_series, ret24=None, mom_thr=None,
           cull_frac=None, taper=None, pyramid=None, tp_mult=1.0,
           basket_tp=None, trail_arm=None, trail_frac=None, b48_override=None):
    """全機能デフォルト(tp_mult=1.0, 他None)で grid_floatstop_bt.run_backtest と一致。
    決済優先順位: per-leg TP → basket TP/trail → FS → B48 → cull。"""
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']
    b48_hours = b48_override if b48_override is not None else cfg['b48_hours']
    max_levels = cfg['max_levels']; float_stop = cfg['float_stop']

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    lpeak = speak = 0.0          # basket trailing peaks (per direction)
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp, cull_pnls, bk_pnls, bk_pp = [], [], [], [], [], [], [], []
    realized = peak = max_dd = worst = 0.0

    def lot_for(level):
        if taper is not None: return base_lot * (taper ** (level - 1))
        if pyramid is not None: return base_lot * (pyramid ** (level - 1))
        return base_lot

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult; ci = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= max_levels; swm = len(short_pos) >= max_levels

        # ── per-leg TP ──
        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); tp_pnls.append(v); realized += v; long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); tp_pnls.append(v); realized += v; short_pos.remove(p)
        if not long_pos: b48_ls = None; lpeak = 0.0
        if not short_pos: b48_ss = None; speak = 0.0

        # ── X2 basket TP / X3 basket trailing (close基準) ──
        if (basket_tp is not None or trail_arm is not None):
            if long_pos:
                un = sum(pj(bc - p['entry'], p['lot']) for p in long_pos)
                hit = (basket_tp is not None and un >= basket_tp)
                if trail_arm is not None:
                    lpeak = max(lpeak, un)
                    if lpeak >= trail_arm and un <= lpeak - trail_frac * lpeak:
                        hit = True
                if hit:
                    pp = [pj(bc - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
                    bk_pp.extend(pp); bk_pnls.append(ev); realized += ev
                    long_pos = []; b48_ls = None; lpeak = 0.0
            if short_pos:
                un = sum(pj(p['entry'] - bc, p['lot']) for p in short_pos)
                hit = (basket_tp is not None and un >= basket_tp)
                if trail_arm is not None:
                    speak = max(speak, un)
                    if speak >= trail_arm and un <= speak - trail_frac * speak:
                        hit = True
                if hit:
                    pp = [pj(p['entry'] - bc, p['lot']) for p in short_pos]; ev = sum(pp)
                    bk_pp.extend(pp); bk_pnls.append(ev); realized += ev
                    short_pos = []; b48_ss = None; speak = 0.0

        # ── FS ──
        if long_pos and sum(pj(bl - p['entry'], p['lot']) for p in long_pos) <= float_stop:
            pp = [pj(bl - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; lpeak = 0.0
        if short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in short_pos) <= float_stop:
            pp = [pj(p['entry'] - bh, p['lot']) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; speak = 0.0

        # ── B48 ──
        if lwm and len(long_pos) < max_levels: b48_ls = None
        if swm and len(short_pos) < max_levels: b48_ss = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(bc - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; lpeak = 0.0
        if b48_ss is not None and (ts - b48_ss).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(p['entry'] - bc, p['lot']) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; speak = 0.0

        # ── cull ──
        if cull_frac is not None:
            if len(long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v
                    worst = min(worst, v); long_pos.remove(p)
                    if len(long_pos) < max_levels: b48_ls = None
            if len(short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in short_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); cull_pnls.append(v); realized += v
                    worst = min(worst, v); short_pos.remove(p)
                    if len(short_pos) < max_levels: b48_ss = None

        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        r = ret24[i] if ret24 is not None else np.nan
        long_ok = ci_ok and (mom_thr is None or np.isnan(r) or r > -mom_thr)
        short_ok = ci_ok and (mom_thr is None or np.isnan(r) or r < mom_thr)

        if len(long_pos) == 0:
            if long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw * tp_mult, 'lot': lot_for(1)})
                if len(long_pos) == max_levels: b48_ls = ts
        elif len(long_pos) < max_levels:
            if bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw * tp_mult, 'lot': lot_for(len(long_pos) + 1)})
                if len(long_pos) == max_levels: b48_ls = ts

        if len(short_pos) == 0:
            if short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw * tp_mult, 'lot': lot_for(1)})
                if len(short_pos) == max_levels: b48_ss = ts
        elif len(short_pos) < max_levels:
            if bc >= max(p['entry'] for p in short_pos) + gw and short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw * tp_mult, 'lot': lot_for(len(short_pos) + 1)})
                if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp + cull_pnls + bk_pp
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'n_cull': len(cull_pnls),
            'n_bk': len(bk_pnls), 'worst_event': round(worst, 0), 'max_dd': round(max_dd, 0)}


def metrics(cfg, df, atr, ci, ret24=None, **kw):
    def w(lo=None, hi=None):
        m = D.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        r24 = ret24[m] if ret24 is not None else None
        return run_bt(cfg, sub, atr, ci, r24, **kw)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in [2022, 2023, 2024, 2025]]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nbk': full['n_bk'], 'full_worst': full['worst_event'],
            'full_ntp': full['n_tp'], 'is_pf': isr['pf'], 'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'],
            'oos_dd': oos['max_dd'], 'wfo_med': float(np.median(wfo)), 'wfo_min': float(wfo.min()),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None: return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    print(f'{tag:22s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>10,.0f}{mk(m["full_worst"], b["full_worst"]) if b else ""} '
          f'nFS={m["full_nfs"]:2d} nBK={m["full_nbk"]:3d} nTP={m["full_ntp"]:4d} | '
          f'IS={m["is_pf"]:4.2f}{mk(m["is_pf"], b["is_pf"]) if b else ""} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} | '
          f'WFOmed={m["wfo_med"]:4.2f} min={m["wfo_min"]:4.2f} {m["wfo_each"]}')


def run_pair(pair, cfg, fs_unit):
    """fs_unit: basket_tp/trail_arm のJPYスケール基準(=|float_stop|)。"""
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    rows = []
    print('=' * 134); print(f'{pair}  fs={cfg["float_stop"]:,.0f}'); print('=' * 134)

    base_m = metrics(cfg, df, atr, ci)
    show('baseline', base_m); rows.append(('baseline', base_m))
    ref = G.run_backtest(pair, cfg, df, atr, ci)
    assert abs(ref['total_pnl'] - base_m['full_net']) < 1.0, f'{pair} engine mismatch'
    print(f'  [engine check] grid_floatstop_bt 一致 OK')

    print('--- X1 TP距離倍率 ---')
    for t in [0.6, 0.8, 1.2, 1.5, 2.0]:
        m = metrics(cfg, df, atr, ci, tp_mult=t); show(f'X1 tp_mult={t}', m, base_m); rows.append((f'X1_tp{t}', m))

    print('--- X2 バスケットTP (|fs|比) ---')
    for f in [0.15, 0.25, 0.40]:
        bt = abs(fs_unit) * f
        m = metrics(cfg, df, atr, ci, basket_tp=bt); show(f'X2 bk_tp={f}|fs|', m, base_m); rows.append((f'X2_bk{f}', m))

    print('--- X3 バスケット・トレール (arm|fs|比, give-back) ---')
    for arm, gb in [(0.15, 0.5), (0.25, 0.5), (0.25, 0.33)]:
        m = metrics(cfg, df, atr, ci, trail_arm=abs(fs_unit) * arm, trail_frac=gb)
        show(f'X3 arm{arm}/gb{gb}', m, base_m); rows.append((f'X3_arm{arm}_gb{gb}', m))

    print('--- X4 B48時間 ---')
    for h in [24, 36, 72, 96]:
        m = metrics(cfg, df, atr, ci, b48_override=h); show(f'X4 b48={h}h', m, base_m); rows.append((f'X4_b48_{h}', m))

    print('--- L2 ロット・ピラミッド(対照=逆テーパー) ---')
    for pmd in [1.15, 1.3]:
        m = metrics(cfg, df, atr, ci, pyramid=pmd); show(f'L2 pyramid={pmd}', m, base_m); rows.append((f'L2_pyr{pmd}', m))

    return df, atr, ci, r24, base_m, rows


def main():
    allrows = []
    # AUDCAD
    df, atr, ci, r24, base_m, rows = run_pair('AUDCAD', D.AUDCAD, D.AUDCAD['float_stop'])
    print('--- C 採用combo + 最良決済の組合せ ---')
    combo = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
    for tag, extra in [('C combo (ref)', {}),
                       ('C combo+tp0.8', {'tp_mult': 0.8}),
                       ('C combo+tp1.2', {'tp_mult': 1.2}),
                       ('C combo+bk0.25', {'basket_tp': abs(D.AUDCAD['float_stop']) * 0.25}),
                       ('C combo+b48_36', {'b48_override': 36})]:
        m = metrics(D.AUDCAD, df, atr, ci, r24, **combo, **extra)
        show(tag, m, base_m); rows.append((tag.replace(' ', '_'), m))
    allrows += [{'pair': 'AUDCAD', 'tag': t, **{k: v for k, v in mm.items() if k != 'wfo_each'}} for t, mm in rows]

    # EURGBP 構造性
    df, atr, ci, r24, base_e, rows = run_pair('EURGBP', D.EURGBP, D.EURGBP['float_stop'])
    print('--- C 採用combo + 最良決済 (EURGBP転移) ---')
    for tag, extra in [('C combo (ref)', {}),
                       ('C combo+tp0.8', {'tp_mult': 0.8}),
                       ('C combo+bk0.25', {'basket_tp': abs(D.EURGBP['float_stop']) * 0.25}),
                       ('C combo+b48_36', {'b48_override': 36})]:
        m = metrics(D.EURGBP, df, atr, ci, r24, **combo, **extra)
        show(tag, m, base_e); rows.append((tag.replace(' ', '_'), m))
    allrows += [{'pair': 'EURGBP', 'tag': t, **{k: v for k, v in mm.items() if k != 'wfo_each'}} for t, mm in rows]

    pd.DataFrame(allrows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
