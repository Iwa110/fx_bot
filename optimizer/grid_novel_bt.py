"""
grid_novel_bt.py - これまで未検証の3観点で Grid PF 改善を狙う。

過去の検証はすべて long/short 対称・全時間帯・即再エントリーを前提にしていた。本検証は
その前提自体を崩す3つの新次元を IS=2015-21 凍結 → OOS/WFO + ペア転移で評価する。

  N1 方向バイアス : long-only / short-only / both を比較。診断で long側PFが全ペアで
                    高い(AUDCAD L1.77/S1.32, EURGBP L1.43/S1.11, GBPJPY S=-9.4M, NZDJPY
                    L+5.2M/S-2.4M)と判明。**IS/OOS両方で long>short が保たれるか**=
                    構造的バイアスか単なるサンプル・ドリフトかを判定。
  N2 強制決済後クールダウン : FS/B48 発火後、その方向の新ラダー再開を cooldown_h 時間禁止。
                    診断の核心「トレンドが焼く→即再ラダー→また焼かれる」を直接遮断
                    (BB戦略のクールダウン・バグ修正と同型の発想)。
  N3 セッション・ゲート : 新規建てを流動性の高いUTC時間帯(London/NY)に限定。
                    薄商いのアジア時間のラダー起動を抑止。

エンジンは grid_floatstop_bt を1:1踏襲(全機能OFFで静的一致をassert)。
合否=IS-selectable ∧ OOS非悪化 ∧ リスク改善 ∧ ペア転移(AUDCAD↔EURGBP方向一致)。

実行: .venv_dukas/bin/python optimizer/grid_novel_bt.py
出力: grid_novel_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_novel_result.csv'
IS_WIN = D.IS_WIN; OOS_WIN = D.OOS_WIN
CONTRACT = G.CONTRACT


def run_bt(cfg, df, atr_series, ci_series, *, allow_long=True, allow_short=True,
           cooldown_h=None, session_hours=None):
    """全引数デフォルトで grid_floatstop_bt.run_backtest と完全一致。
    cooldown_h: FS/B48後その方向の新ラダー(len==0)起動を cooldown_h 時間禁止。
    session_hours: set[int] 新規建て許可UTC時。None=全時間。"""
    qj = cfg.get('quote_jpy', 1.0); lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']
    max_levels = cfg['max_levels']; float_stop = cfg['float_stop']

    def pj(d): return d * lot * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    hours = idx.hour.to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    cd_long = cd_short = None        # 直近の強制決済時刻(クールダウン基点)
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp = [], [], [], [], []
    realized = peak = max_dd = worst = 0.0

    for i in range(len(df)):
        atr = av[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult; ci = cv[i]; hr = hours[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= max_levels; swm = len(short_pos) >= max_levels

        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry']); tp_pnls.append(v); realized += v; long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp']); tp_pnls.append(v); realized += v; short_pos.remove(p)

        if long_pos and sum(pj(bl - p['entry']) for p in long_pos) <= float_stop:
            pp = [pj(bl - p['entry']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; cd_long = ts
        if short_pos and sum(pj(p['entry'] - bh) for p in short_pos) <= float_stop:
            pp = [pj(p['entry'] - bh) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; cd_short = ts

        if lwm and len(long_pos) < max_levels: b48_ls = None
        if swm and len(short_pos) < max_levels: b48_ss = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(bc - p['entry']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; cd_long = ts
        if b48_ss is not None and (ts - b48_ss).total_seconds() / 3600.0 >= b48_hours:
            pp = [pj(p['entry'] - bc) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; cd_short = ts

        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        sess_ok = (session_hours is None) or (hr in session_hours)

        def cd_ok(cd):
            return cooldown_h is None or cd is None or (ts - cd).total_seconds() / 3600.0 >= cooldown_h

        # long
        if allow_long:
            if len(long_pos) == 0:
                if ci_ok and sess_ok and cd_ok(cd_long):
                    long_pos.append({'entry': bc, 'tp': bc + gw})
                    if len(long_pos) == max_levels: b48_ls = ts
            elif len(long_pos) < max_levels:
                if bc <= min(p['entry'] for p in long_pos) - gw and ci_ok and sess_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw})
                    if len(long_pos) == max_levels: b48_ls = ts
        # short
        if allow_short:
            if len(short_pos) == 0:
                if ci_ok and sess_ok and cd_ok(cd_short):
                    short_pos.append({'entry': bc, 'tp': bc - gw})
                    if len(short_pos) == max_levels: b48_ss = ts
            elif len(short_pos) < max_levels:
                if bc >= max(p['entry'] for p in short_pos) + gw and ci_ok and sess_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw})
                    if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'worst_event': round(worst, 0),
            'max_dd': round(max_dd, 0)}


def metrics(cfg, df, atr, ci, **kw):
    def w(lo=None, hi=None):
        m = D.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        return run_bt(cfg, sub, atr, ci, **kw)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in [2022, 2023, 2024, 2025]]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_worst': full['worst_event'], 'full_ntp': full['n_tp'],
            'is_pf': isr['pf'] if isr else np.nan, 'is_net': isr['total_pnl'] if isr else np.nan,
            'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)) if len(wfo) else np.nan,
            'wfo_min': float(wfo.min()) if len(wfo) else np.nan,
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None or np.isnan(v) or np.isnan(b): return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    iss = f'{m["is_pf"]:4.2f}' if not np.isnan(m['is_pf']) else '  - '
    print(f'{tag:20s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>10,.0f}{mk(m["full_worst"], b["full_worst"]) if b else ""} '
          f'nFS={m["full_nfs"]:2d} nTP={m["full_ntp"]:4d} | IS={iss}{mk(m["is_pf"], b["is_pf"]) if b else ""} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} | '
          f'WFOmed={m["wfo_med"]:4.2f} min={m["wfo_min"]:4.2f} {m["wfo_each"]}')


# London(7-16) + NY(12-21) UTC オーバーラップ重視。アジア薄商い(22-6)を除外。
SESSION_LDN_NY = set(range(7, 21))
SESSION_OVERLAP = set(range(12, 17))


def run_pair(pair, cfg, rows, transfer=False):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    print('=' * 134); print(f'{pair}  fs={cfg["float_stop"]:,.0f}'); print('=' * 134)
    base_m = metrics(cfg, df, atr, ci); show('baseline', base_m)
    rows.append((pair, 'baseline', base_m))
    ref = G.run_backtest(pair, cfg, df, atr, ci)
    assert abs(ref['total_pnl'] - base_m['full_net']) < 1.0, f'{pair} mismatch'

    print('--- N1 方向バイアス ---')
    for tag, kw in [('N1 long-only', {'allow_short': False}),
                    ('N1 short-only', {'allow_long': False})]:
        m = metrics(cfg, df, atr, ci, **kw); show(tag, m, base_m); rows.append((pair, tag, m))

    print('--- N2 強制決済後クールダウン ---')
    for h in [24, 48, 96, 168]:
        m = metrics(cfg, df, atr, ci, cooldown_h=h); show(f'N2 cd={h}h', m, base_m); rows.append((pair, f'N2_cd{h}', m))

    print('--- N3 セッション・ゲート ---')
    for tag, sh in [('N3 LDN+NY(7-20)', SESSION_LDN_NY), ('N3 overlap(12-16)', SESSION_OVERLAP)]:
        m = metrics(cfg, df, atr, ci, session_hours=sh); show(tag, m, base_m); rows.append((pair, tag, m))

    print('--- C 有望案の組合せ (long-only + cd48) ---')
    for tag, kw in [('C Lonly+cd48', {'allow_short': False, 'cooldown_h': 48}),
                    ('C Lonly+cd96', {'allow_short': False, 'cooldown_h': 96})]:
        m = metrics(cfg, df, atr, ci, **kw); show(tag, m, base_m); rows.append((pair, tag, m))
    return df, atr, ci


def main():
    rows = []
    run_pair('AUDCAD', D.AUDCAD, rows)
    run_pair('EURGBP', D.EURGBP, rows)
    # No-Goペアで long-only が救済になるか(NZDJPY long単独+5.2M, GBPJPY short壊滅)
    run_pair('NZDJPY', GI.V7_CONFIG['NZDJPY'], rows)
    run_pair('GBPJPY', GI.V7_CONFIG['GBPJPY'], rows)

    out = [{'pair': p, 'tag': t, **{k: v for k, v in m.items() if k != 'wfo_each'}} for p, t, m in rows]
    pd.DataFrame(out).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
