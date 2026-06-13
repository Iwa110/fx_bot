"""
grid_dd_reduction_bt.py - Grid 負けパターン(テール=強制決済集中)に対する DD/worst 削減案の検証。

背景: モメンタム・ゲート(grid_entry_filter_bt.py F1)は PF 1.51→2.48 を達成したが
「テール/worst単発(-811k)と maxDD(880k)は不変」が残課題。本検証は損失の構造
(全損失が float-stop/B48 の83ポジに集中・FS時は最古レッグが最大損)を踏まえ、
**ポジション側のリスク構造**を変える3案 + モメンタムゲートとの併用を検証する。

  D1 worst-leg cull : ラダー含み損(close基準)が cull_frac×float_stop を超えたら
                      最悪レッグ1本だけを close で切る(全玉一斉FSの前に段階的減量)。
  D2 lot taper      : レベル k のロット = lot×taper^(k-1)。診断済み負けパターン
                      「不利トレンドへの深い追加=ナイフ掴み」の重みを構造的に削減。
                      level1(勝ち越し)はフルロット維持。
  D3 spacing widen  : 追加レベルの間隔を gw×(1+widen×(k-1)) に拡大(TP距離は据置)。
                      ラダーが同じ価格逆行で浅くしか深化しない=FS到達を遅延。

ガードレール(過去検証と同一規律):
  - エンジンは grid_entry_filter2_bt の run_bt を踏襲。全機能OFFで静的baselineと完全一致を検証。
  - しきい値は IS=2015-2021 のみで選択 → OOS=2022-2026・WFO(2022-25年次)で評価。
  - 合否 = IS-selectable(IS PFがbase以上) ∧ OOS PF非悪化 ∧ maxDD/worst/nFS の有意改善。
  - 構造性チェック: 勝ち構成を EURGBP(atr1.5テンプレ, 再チューニング無し)へ転移。

実行: .venv_dukas/bin/python optimizer/grid_dd_reduction_bt.py
出力: grid_dd_reduction_bt_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_dd_reduction_bt_result.csv'
CONTRACT = G.CONTRACT
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]

AUDCAD = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
          'lot': 1.0, 'max_levels': 5, 'float_stop': -750_000.0, 'quote_jpy': 108.0}
# EURGBP: AUDCADテンプレ(grid_newpairs_bt.py準拠, fsはquote_jpy比でprice距離一致)
EURGBP = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
          'lot': 1.0, 'max_levels': 5,
          'float_stop': round(-750_000.0 * 190.0 / 108.0, 0), 'quote_jpy': 190.0}


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def ret24_series(df, atr):
    return (((df['close'] - df['close'].shift(24)) / atr.reindex(df.index)).shift(1)).to_numpy()


def run_bt(cfg, df, atr_series, ci_series, ret24=None, mom_thr=None,
           cull_frac=None, taper=None, widen=None, collect_fs=False,
           allow_long=True, allow_short=True):
    """全機能OFF(=None)で grid_floatstop_bt.run_backtest と完全一致。
    per-position lot 対応。cull は TP/FS/B48 処理後・建て前に close 基準で判定。
    allow_long/allow_short=False で方向バイアス(片側のみ)。"""
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']
    max_levels = cfg['max_levels']; float_stop = cfg['float_stop']

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp, cull_pnls = [], [], [], [], [], []
    fs_detail = []   # (ts, n_legs, per-leg pnl list) 診断用
    realized = peak = max_dd = worst = 0.0

    def lot_for(level):                       # level: 1-origin
        if taper is None: return base_lot
        return base_lot * (taper ** (level - 1))

    def add_gap(nlev):                        # nlev: 現在の保有レベル数
        if widen is None: return 1.0
        return 1.0 + widen * nlev

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult; ci = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= max_levels; swm = len(short_pos) >= max_levels

        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); tp_pnls.append(v); realized += v; long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); tp_pnls.append(v); realized += v; short_pos.remove(p)

        if long_pos and sum(pj(bl - p['entry'], p['lot']) for p in long_pos) <= float_stop:
            pp = [pj(bl - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            if collect_fs: fs_detail.append((ts, len(pp), sorted(pp)))
            long_pos = []; b48_ls = None
        if short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in short_pos) <= float_stop:
            pp = [pj(p['entry'] - bh, p['lot']) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            if collect_fs: fs_detail.append((ts, len(pp), sorted(pp)))
            short_pos = []; b48_ss = None

        if lwm and len(long_pos) < max_levels: b48_ls = None
        if swm and len(short_pos) < max_levels: b48_ss = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(bc - p['entry'], p['lot']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None
        if b48_ss is not None and (ts - b48_ss).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(p['entry'] - bc, p['lot']) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None

        # ── D1 worst-leg cull (close基準・最悪1本のみ・FS未満の段階的減量) ──
        if cull_frac is not None:
            if len(long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0])
                    cull_pnls.append(v); realized += v; worst = min(worst, v)
                    long_pos.remove(p)
                    if len(long_pos) < max_levels: b48_ls = None
            if len(short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in short_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0])
                    cull_pnls.append(v); realized += v; worst = min(worst, v)
                    short_pos.remove(p)
                    if len(short_pos) < max_levels: b48_ss = None

        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        r = ret24[i] if ret24 is not None else np.nan
        long_ok = allow_long and ci_ok and (mom_thr is None or np.isnan(r) or r > -mom_thr)
        short_ok = allow_short and ci_ok and (mom_thr is None or np.isnan(r) or r < mom_thr)

        if len(long_pos) == 0:
            if long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': lot_for(1)})
                if len(long_pos) == max_levels: b48_ls = ts
        elif len(long_pos) < max_levels:
            if bc <= min(p['entry'] for p in long_pos) - gw * add_gap(len(long_pos)) and long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': lot_for(len(long_pos) + 1)})
                if len(long_pos) == max_levels: b48_ls = ts

        if len(short_pos) == 0:
            if short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': lot_for(1)})
                if len(short_pos) == max_levels: b48_ss = ts
        elif len(short_pos) < max_levels:
            if bc >= max(p['entry'] for p in short_pos) + gw * add_gap(len(short_pos)) and short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': lot_for(len(short_pos) + 1)})
                if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp + cull_pnls
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'n_cull': len(cull_pnls),
            'cull_total': round(sum(cull_pnls), 0), 'worst_event': round(worst, 0),
            'max_dd': round(max_dd, 0), 'fs_detail': fs_detail}


def win_mask(df, lo, hi):
    m = pd.Series(True, index=df.index)
    if lo: m &= df.index >= pd.Timestamp(lo, tz='UTC')
    if hi: m &= df.index <= pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)
    return m.to_numpy()


def metrics(cfg, df, atr, ci, ret24=None, **kw):
    def w(lo=None, hi=None):
        m = win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        r24 = ret24[m] if ret24 is not None else None
        return run_bt(cfg, sub, atr, ci, r24, **kw)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nb48': full['n_b48'], 'full_ncull': full['n_cull'],
            'full_worst': full['worst_event'], 'full_ntp': full['n_tp'],
            'is_pf': isr['pf'], 'is_dd': isr['max_dd'], 'is_worst': isr['worst_event'],
            'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'oos_worst': oos['worst_event'],
            'wfo_med': float(np.median(wfo)), 'wfo_min': float(wfo.min()),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None: return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    print(f'{tag:22s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>9,.0f}{mk(m["full_worst"], b["full_worst"]) if b else ""} '
          f'nFS={m["full_nfs"]:2d} nCull={m["full_ncull"]:3d} nTP={m["full_ntp"]:4d} | '
          f'IS={m["is_pf"]:4.2f}{mk(m["is_pf"], b["is_pf"]) if b else ""} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} '
          f'oosDD={m["oos_dd"]:>9,.0f}{mk(m["oos_dd"], b["oos_dd"], True) if b else ""} | '
          f'WFOmed={m["wfo_med"]:4.2f} min={m["wfo_min"]:4.2f} {m["wfo_each"]}')


def diagnose_fs(cfg, df, atr, ci):
    """FSイベントの per-leg 構造を診断: 最悪レッグが総損失に占める割合。"""
    r = run_bt(cfg, df, atr, ci, collect_fs=True)
    print('--- FSイベント診断 (baseline) ---')
    shares = []
    for ts, n, legs in r['fs_detail']:
        total = sum(legs); worst_leg = legs[0]
        shares.append(worst_leg / total)
        print(f'  {ts.date()}  legs={n}  total={total:>11,.0f}  worst_leg={worst_leg:>11,.0f} '
              f'({worst_leg/total*100:.0f}%)  legs={[round(x/1000) for x in legs]}k')
    if shares:
        print(f'  worst-leg share: mean={np.mean(shares)*100:.0f}%  '
              f'median={np.median(shares)*100:.0f}%')
    return r


def main():
    rows = []
    print('=' * 130)
    print('AUDCAD (baseline = 静的最良 atr1.5/ci65/lv5/fs-750k)')
    print('=' * 130)
    df = load_duk('AUDCAD'); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = ret24_series(df, atr)

    base_m = metrics(AUDCAD, df, atr, ci)
    show('baseline', base_m)
    rows.append({'pair': 'AUDCAD', 'tag': 'baseline', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})
    # 静的一致検証
    ref = G.run_backtest('AUDCAD', AUDCAD, df, atr, ci)
    assert abs(ref['total_pnl'] - base_m['full_net']) < 1.0, 'engine mismatch vs grid_floatstop_bt'
    print(f'  [engine check] grid_floatstop_bt 一致 OK (net={ref["total_pnl"]:,.0f})')

    diagnose_fs(AUDCAD, df, atr, ci)

    mom_m = metrics(AUDCAD, df, atr, ci, r24, mom_thr=2.0)
    show('ref F1 mom2.0', mom_m, base_m)
    rows.append({'pair': 'AUDCAD', 'tag': 'F1_mom2.0', **{k: v for k, v in mom_m.items() if k != 'wfo_each'}})

    print('\n=== D1: worst-leg cull (含み損 > cull_frac×FS予算 で最悪レッグ1本減量) ===')
    for f in [0.4, 0.5, 0.6]:
        m = metrics(AUDCAD, df, atr, ci, cull_frac=f)
        show(f'D1 cull={f}', m, base_m)
        rows.append({'pair': 'AUDCAD', 'tag': f'D1_cull{f}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n=== D2: lot taper (level k ロット = taper^(k-1)) ===')
    for t in [0.85, 0.7, 0.6, 0.5]:
        m = metrics(AUDCAD, df, atr, ci, taper=t)
        show(f'D2 taper={t}', m, base_m)
        rows.append({'pair': 'AUDCAD', 'tag': f'D2_taper{t}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n=== D3: spacing widen (追加間隔 = gw×(1+widen×k), TP据置) ===')
    for wd in [0.3, 0.5]:
        m = metrics(AUDCAD, df, atr, ci, widen=wd)
        show(f'D3 widen={wd}', m, base_m)
        rows.append({'pair': 'AUDCAD', 'tag': f'D3_widen{wd}', **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n=== E: 併用 (mom2.0 + IS最良のD案) ===')
    for tag, kw in [('E mom2.0+cull0.5', {'mom_thr': 2.0, 'cull_frac': 0.5}),
                    ('E mom2.0+taper0.7', {'mom_thr': 2.0, 'taper': 0.7}),
                    ('E mom2.0+cull0.5+tap0.7', {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7})]:
        m = metrics(AUDCAD, df, atr, ci, r24, **kw)
        show(tag, m, base_m)
        rows.append({'pair': 'AUDCAD', 'tag': tag.replace(' ', '_'), **{k: v for k, v in m.items() if k != 'wfo_each'}})

    print('\n' + '=' * 130)
    print('EURGBP 構造性チェック (テンプレ atr1.5/ci65/lv5/fs-1.32M, 再チューニング無し)')
    print('=' * 130)
    dfe = load_duk('EURGBP'); atre = G.compute_atr_series(dfe); cie = G.compute_ci_series(dfe)
    r24e = ret24_series(dfe, atre)
    base_e = metrics(EURGBP, dfe, atre, cie)
    show('EURGBP baseline', base_e)
    rows.append({'pair': 'EURGBP', 'tag': 'baseline', **{k: v for k, v in base_e.items() if k != 'wfo_each'}})
    for tag, kw in [('EURGBP mom2.0', {'mom_thr': 2.0}),
                    ('EURGBP cull0.5', {'cull_frac': 0.5}),
                    ('EURGBP taper0.7', {'taper': 0.7}),
                    ('EURGBP mom+cull', {'mom_thr': 2.0, 'cull_frac': 0.5}),
                    ('EURGBP mom+cull+tap', {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7})]:
        m = metrics(EURGBP, dfe, atre, cie, r24e, **kw)
        show(tag, m, base_e)
        rows.append({'pair': 'EURGBP', 'tag': tag.replace(' ', '_'), **{k: v for k, v in m.items() if k != 'wfo_each'}})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
