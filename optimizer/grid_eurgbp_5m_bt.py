"""
grid_eurgbp_5m_bt.py - EURGBP グリッド(+モメンタムゲート)を 5m データで高精度再検証。

動機: 1h BT はバー内の値動き順序を潰す(同一1hバー内で TP を先に約定→残りを float-stop、と仮定)。
実際には価格が先に float-stop まで突っ込んでから回復して TP に届く事もあり、1h BT は楽観的になりうる。
5m で bar 内の TP / float-stop / B48 の発火順序を正しく解決し、EURGBP のエッジが 1h バー由来の
アーティファクトでないかを確認する。

設計(ハイブリッド = 意思決定は1h・約定/ストップ解決は5m):
  - グリッド意思決定(新規/追加建て・CIゲート・モメンタムゲート)は各1h足の終値で評価
    (gw=H1 ATR14*mult, CI=D1, ret24=24h/ATR)。=1h BTと同一セマンティクス。
  - 各1時間内は構成する12本の5m足を時系列で走査し TP/float-stop/B48 を正しい順序で発火。
  - ATR/CI/ret24 は 5m から再生成した1h足で算出(データ内部整合)。
切り分けのため、同一データを1hに集約した上で純1hエンジン(grid_entry_filter_bt.run_bt)でも実行し、
「5m精緻化の効果」と「データ差」を分離する。

真値=Dukascopy。t-1。float_stop は quote_jpy=190 で AUDCAD と price距離一致(-1,319,444)。
実行: python3 optimizer/grid_eurgbp_5m_bt.py  出力: grid_eurgbp_5m_bt_result.csv
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_entry_filter_bt as EF

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_eurgbp_5m_bt_result.csv'
PAIR = 'EURGBP'
QJ = 190.0
FS = round(-750_000.0 * QJ / 108.0, 0)   # AUDCAD と price距離一致
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]
CONTRACT = G.CONTRACT


def cfg(atr_mult):
    return {'atr_mult': atr_mult, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
            'max_levels': 5, 'float_stop': FS, 'quote_jpy': QJ}


def load_5m(pair):
    d = pd.read_csv(DATA / f'{pair}_5m_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def resample_1h(df5):
    o = df5.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    return o.dropna()


def run_5m_hybrid(c, df5, df1h, atr1h, ci1h, ret24_1h, mom_thr=None):
    """1h意思決定 / 5m約定解決のハイブリッド。返り値は run_bt と同一スキーマ。"""
    qj = c['quote_jpy']; lot = c['lot']; atr_mult = c['atr_mult']
    ci_threshold = c['ci_threshold']; b48_hours = c['b48_hours']
    max_levels = c['max_levels']; float_stop = c['float_stop']

    def pj(d): return d * lot * CONTRACT * qj

    idx5 = df5.index
    h5 = df5['high'].to_numpy(); l5 = df5['low'].to_numpy(); c5 = df5['close'].to_numpy()
    hour = idx5.floor('h')
    # 各5m足が属する1hの値
    atr_b = atr1h.reindex(hour).to_numpy()
    ci_b = ci1h.reindex(hour).to_numpy()
    # ret24_1h は df1h.index で索引された Series であること(時刻整合に必須)
    ret_b = ret24_1h.reindex(hour).to_numpy()
    cl1h = df1h['close'].reindex(hour).to_numpy()   # その1hの終値(エントリー価格)
    # 時間境界: 次の5m足が別の1hなら、この足が1h終値
    hour_i8 = hour.asi8
    is_hclose = np.empty(len(idx5), dtype=bool)
    is_hclose[-1] = True
    is_hclose[:-1] = hour_i8[1:] != hour_i8[:-1]

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp = [], [], [], [], []
    realized = peak = max_dd = worst = 0.0

    def upd_dd():
        nonlocal peak, max_dd
        peak = max(peak, realized); max_dd = max(max_dd, peak - realized)

    for i in range(len(idx5)):
        atr = atr_b[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx5[i]; bh, bl = h5[i], l5[i]; bc5 = c5[i]

        # ── 5m足内: TP ──
        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp']-p['entry']); tp_pnls.append(v); realized += v; long_pos.remove(p); upd_dd()
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry']-p['tp']); tp_pnls.append(v); realized += v; short_pos.remove(p); upd_dd()

        # ── float-stop(5m足の逆行extreme) ──
        if long_pos and sum(pj(bl-p['entry']) for p in long_pos) <= float_stop:
            pp = [pj(bl-p['entry']) for p in long_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; upd_dd()
        if short_pos and sum(pj(p['entry']-bh) for p in short_pos) <= float_stop:
            pp = [pj(p['entry']-bh) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; upd_dd()

        # ── B48: ラダーが満杯でなければタイマー無効 / 満杯で48h超で成行決済 ──
        if b48_ls is not None and len(long_pos) < max_levels: b48_ls = None
        if b48_ss is not None and len(short_pos) < max_levels: b48_ss = None
        if b48_ls is not None and (ts-b48_ls).total_seconds()/3600.0 >= b48_hours:
            pp = [pj(bc5-p['entry']) for p in long_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            long_pos = []; b48_ls = None; upd_dd()
        if b48_ss is not None and (ts-b48_ss).total_seconds()/3600.0 >= b48_hours:
            pp = [pj(p['entry']-bc5) for p in short_pos]; ev = sum(pp)
            b48_pp.extend(pp); b48_pnls.append(ev); realized += ev; worst = min(worst, ev)
            short_pos = []; b48_ss = None; upd_dd()

        # ── 1h終値で新規/追加建て(=1h BTと同一意思決定) ──
        if is_hclose[i]:
            gw = atr * atr_mult; ci = ci_b[i]; r = ret_b[i]; bc = cl1h[i]
            if np.isnan(bc):
                continue
            ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
            long_ok = ci_ok and (mom_thr is None or np.isnan(r) or r > -mom_thr)
            short_ok = ci_ok and (mom_thr is None or np.isnan(r) or r < mom_thr)

            if len(long_pos) == 0:
                if long_ok:
                    long_pos.append({'entry': bc, 'tp': bc+gw})
                    if len(long_pos) == max_levels: b48_ls = ts
            elif len(long_pos) < max_levels:
                if bc <= min(p['entry'] for p in long_pos)-gw and long_ok:
                    long_pos.append({'entry': bc, 'tp': bc+gw})
                    if len(long_pos) == max_levels: b48_ls = ts
            if len(short_pos) == 0:
                if short_ok:
                    short_pos.append({'entry': bc, 'tp': bc-gw})
                    if len(short_pos) == max_levels: b48_ss = ts
            elif len(short_pos) < max_levels:
                if bc >= max(p['entry'] for p in short_pos)+gw and short_ok:
                    short_pos.append({'entry': bc, 'tp': bc-gw})
                    if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp/gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'worst_event': round(worst, 0),
            'max_dd': round(max_dd, 0)}


def mask5(df5, lo, hi):
    m = pd.Series(True, index=df5.index)
    if lo: m &= df5.index >= pd.Timestamp(lo, tz='UTC')
    if hi: m &= df5.index <= pd.Timestamp(hi, tz='UTC')+pd.Timedelta(days=1)
    return m.to_numpy()


def metrics_5m(c, df5, df1h, atr1h, ci1h, ret24_ser, mom_thr=None):
    def w(lo=None, hi=None):
        m = mask5(df5, lo, hi); sub = df5[m]
        if len(sub) < 3000: return None
        return run_5m_hybrid(c, sub, df1h, atr1h, ci1h, ret24_ser, mom_thr)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return pack(full, isr, oos, wfo)


def metrics_1h(c, df1h, atr1h, ci1h, ret24_1h, mom_thr=None):
    ret = np.asarray(ret24_1h)
    def w(lo=None, hi=None):
        m = EF.win_mask(df1h, lo, hi); sub = df1h[m]
        if len(sub) < 300: return None
        return EF.run_bt(c, sub, atr1h, ci1h, ret[m], mom_thr)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return pack(full, isr, oos, wfo)


def pack(full, isr, oos, wfo):
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nb48': full['n_b48'], 'full_worst': full['worst_event'],
            'full_ntp': full['n_tp'], 'is_pf': isr['pf'] if isr else float('nan'),
            'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)) if len(wfo) else float('nan'),
            'wfo_min': float(wfo.min()) if len(wfo) else float('nan'),
            'wfo_gt12': float((wfo > 1.2).mean()) if len(wfo) else float('nan'),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m):
    print(f'{tag:26s} fPF={m["full_pf"]:.2f} net={m["full_net"]:>12,.0f} DD={m["full_dd"]:>10,.0f} '
          f'nFS={m["full_nfs"]:2d} nB48={m["full_nb48"]:2d} worst={m["full_worst"]:>11,.0f} nTP={m["full_ntp"]:4d} | '
          f'IS={m["is_pf"]:.2f} OOS={m["oos_pf"]:.2f} | WFOmed={m["wfo_med"]:.2f} min={m["wfo_min"]:.2f} '
          f'>1.2={m["wfo_gt12"]:.2f} {m["wfo_each"]}')


def main():
    df5 = load_5m(PAIR)
    print(f'{PAIR} 5m: {df5.index[0]} ~ {df5.index[-1]}  {len(df5):,}本')
    df1h = resample_1h(df5)
    atr1h = G.compute_atr_series(df1h); ci1h = G.compute_ci_series(df1h)
    ret24 = EF.ret24_series(df1h, atr1h)               # array(1h行整列, 純1hエンジン用)
    ret24_ser = pd.Series(ret24, index=df1h.index)     # Series(時刻索引, 5mハイブリッド用)
    print(f'1h(集約): {len(df1h):,}本   fs={FS:,.0f}(qj{QJ:.0f})')

    rows = []
    for am in [1.5, 2.0]:
        c = cfg(am)
        print(f'\n========== EURGBP atr_mult={am} ==========')
        for tag, mom in [('baseline', None), ('+mom2.0', 2.0)]:
            m1 = metrics_1h(c, df1h, atr1h, ci1h, ret24, mom)
            m5 = metrics_5m(c, df5, df1h, atr1h, ci1h, ret24_ser, mom)
            show(f'1h(from5m) {tag}', m1)
            show(f'5m-hybrid  {tag}', m5)
            rows.append({'atr': am, 'engine': '1h_from5m', 'variant': tag, **{k: v for k, v in m1.items() if k != 'wfo_each'}})
            rows.append({'atr': am, 'engine': '5m_hybrid', 'variant': tag, **{k: v for k, v in m5.items() if k != 'wfo_each'}})
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')
    print('判定: 5m-hybrid が 1h(from5m) 比で PF/OOS/WFO を大きく落とさなければ EURGBP エッジは'
          'バー内順序に頑健(1hアーティファクトでない)。モメンタムゲートも5mで効くか確認。')


if __name__ == '__main__':
    main()
