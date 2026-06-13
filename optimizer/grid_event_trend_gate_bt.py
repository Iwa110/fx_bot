"""
grid_event_trend_gate_bt.py - イベント前後エントリー制限 + 長期モメンタムゲートで
「トレンドを乗り切る」案の検証。

背景(grid_yearly_pf_diag.py 2026-06-11): Gridの負け年は数週間〜数ヶ月の持続トレンド
(path_eff高)に起因し、強制決済頻度がGo/No-Goを分ける。イベント(NFP/CPI)は時間単位の
現象なので「ブラックアウトは効果薄・持続トレンドゲートが本命」を仮説として両方検証。

検証案:
  E1 NFPブラックアウト   : 第1金曜13:30UTC(決定論ルール=全期間生成可)±N時間は
                           新規建て/追加を全面見送り。N=24/48。
  E2 実カレンダー版      : data/news_events.csv(2022-02以降, USD NFP/CPI+GBP CPI)の
                           実日付±24hで同上。2022-2026窓のみで比較(IS凍結不可=診断扱い)。
  T1 長期momゲート       : ret120h(5日リターン/ATR, t-1)が不利方向>thr で見送り。
                           thr は IS=2015-21 で凍結。mom24と同型・地平線のみ長期化。
  T2 mom24+T1 二段ゲート : 短期ナイフ掴み(mom24)+持続トレンド(mom120)の併用。
  T3 採用候補combo+T1    : mom24 2.0+cull0.5+taper0.7 に T1 を重ねる(Goペアのみ)。

対象: AUDCAD/EURGBP(Go, 劣化チェック) + GBPJPY/USDJPY(No-Go, イベント感応通貨の救済可否)。
実行: .venv_dukas/bin/python optimizer/grid_event_trend_gate_bt.py
出力: grid_event_trend_gate_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_event_trend_gate_result.csv'

IS_WIN = D.IS_WIN; OOS_WIN = D.OOS_WIN


# ── イベントカレンダー ──
def nfp_calendar_utc(start='2015-01-01', end='2026-12-31'):
    """第1金曜 13:30 UTC (8:30ET, DSTで±1hズレるが±24h窓には無影響)。"""
    days = pd.date_range(start, end, freq='D', tz='UTC')
    fridays = days[days.weekday == 4]
    first_fri = fridays[fridays.day <= 7]
    return pd.DatetimeIndex(first_fri) + pd.Timedelta(hours=13, minutes=30)


def real_calendar_utc():
    d = pd.read_csv(DATA / 'news_events.csv')
    ts = pd.to_datetime(d['date'] + ' ' + d['time'])
    # 08:30=US東部(≈13:30UTC) / 07:00=ロンドン(≈07:00UTC)
    utc = ts + np.where(d['time'] == '08:30', pd.Timedelta(hours=5), pd.Timedelta(hours=0))
    return pd.DatetimeIndex(utc).tz_localize('UTC')


def blackout_mask(idx, events, hours):
    """idx の各バーがイベント±hours内なら True (=建て禁止)。"""
    m = np.zeros(len(idx), dtype=bool)
    iv = idx.values
    for ev in events.values:
        lo = ev - np.timedelta64(hours, 'h'); hi = ev + np.timedelta64(hours, 'h')
        m |= (iv >= lo) & (iv <= hi)
    return m


def retN_series(df, atr, n):
    return (((df['close'] - df['close'].shift(n)) / atr.reindex(df.index)).shift(1)).to_numpy()


# ── run_bt 拡張: blackout(非方向) と 第2momゲート(方向) を追加 ──
def run_bt2(cfg, df, atr_series, ci_series, ret24=None, mom_thr=None,
            cull_frac=None, taper=None, black=None, ret2=None, mom2_thr=None):
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']
    max_levels = cfg['max_levels']; float_stop = cfg['float_stop']
    CONTRACT = G.CONTRACT

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_ls = b48_ss = None
    tp_pnls, b48_pnls, b48_pp, fs_pnls, fs_pp, cull_pnls = [], [], [], [], [], []
    realized = peak = max_dd = worst = 0.0

    def lot_for(level):
        return base_lot if taper is None else base_lot * (taper ** (level - 1))

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
            long_pos = []; b48_ls = None
        if short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in short_pos) <= float_stop:
            pp = [pj(p['entry'] - bh, p['lot']) for p in short_pos]; ev = sum(pp)
            fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
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
        if black is not None and black[i]:
            ci_ok = False                      # イベント・ブラックアウト(非方向)
        r = ret24[i] if ret24 is not None else np.nan
        long_ok = ci_ok and (mom_thr is None or np.isnan(r) or r > -mom_thr)
        short_ok = ci_ok and (mom_thr is None or np.isnan(r) or r < mom_thr)
        if ret2 is not None and mom2_thr is not None:
            r2 = ret2[i]
            if not np.isnan(r2):
                long_ok = long_ok and (r2 > -mom2_thr)
                short_ok = short_ok and (r2 < mom2_thr)

        if len(long_pos) == 0:
            if long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': lot_for(1)})
                if len(long_pos) == max_levels: b48_ls = ts
        elif len(long_pos) < max_levels:
            if bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': lot_for(len(long_pos) + 1)})
                if len(long_pos) == max_levels: b48_ls = ts

        if len(short_pos) == 0:
            if short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': lot_for(1)})
                if len(short_pos) == max_levels: b48_ss = ts
        elif len(short_pos) < max_levels:
            if bc >= max(p['entry'] for p in short_pos) + gw and short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': lot_for(len(short_pos) + 1)})
                if len(short_pos) == max_levels: b48_ss = ts

    allp = tp_pnls + b48_pp + fs_pp + cull_pnls
    gp = sum(p for p in allp if p >= 0); gl = abs(sum(p for p in allp if p < 0))
    pf = (gp / gl) if gl > 0 else float('inf')
    return {'pf': round(pf, 4), 'total_pnl': round(realized, 0), 'n_tp': len(tp_pnls),
            'n_b48': len(b48_pnls), 'n_fstop': len(fs_pnls), 'n_cull': len(cull_pnls),
            'worst_event': round(worst, 0), 'max_dd': round(max_dd, 0)}


def metrics2(cfg, df, atr, ci, arrays, lo_hi_pairs=None, **kw):
    """arrays: {'ret24':..,'black':..,'ret2':..} 窓ごとにスライスして run_bt2。"""
    def w(lo=None, hi=None):
        m = D.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        g = dict(kw)
        for k in ('ret24', 'black', 'ret2'):
            if arrays.get(k) is not None: g[k] = arrays[k][m]
        return run_bt2(cfg, sub, atr, ci, **g)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in [2022, 2023, 2024, 2025]]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_worst': full['worst_event'], 'full_ntp': full['n_tp'],
            'is_pf': isr['pf'] if isr else np.nan,
            'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)), 'wfo_min': float(wfo.min()),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def mk(v, b, low=False):
        if base is None: return ' '
        return '+' if ((v <= b) if low else (v >= b)) else '-'
    b = base
    is_s = f'{m["is_pf"]:4.2f}' if not np.isnan(m['is_pf']) else '  — '
    print(f'{tag:24s} fPF={m["full_pf"]:5.2f} net={m["full_net"]:>11,.0f} '
          f'DD={m["full_dd"]:>9,.0f}{mk(m["full_dd"], b["full_dd"], True) if b else ""} '
          f'worst={m["full_worst"]:>10,.0f}{mk(m["full_worst"], b["full_worst"]) if b else ""} '
          f'nFS={m["full_nfs"]:2d} nTP={m["full_ntp"]:4d} | IS={is_s} '
          f'OOS={m["oos_pf"]:4.2f}{mk(m["oos_pf"], b["oos_pf"]) if b else ""} | '
          f'WFOmed={m["wfo_med"]:4.2f} min={m["wfo_min"]:4.2f} {m["wfo_each"]}')


def main():
    nfp = nfp_calendar_utc()
    real = real_calendar_utc()
    print(f'NFPルールカレンダー: {len(nfp)}件 (2015-2026, 第1金曜13:30UTC)')
    print(f'実カレンダー: {len(real)}件 ({real.min().date()} .. {real.max().date()})\n')

    pairs = {
        'AUDCAD': ('GO', D.AUDCAD),
        'EURGBP': ('GO', D.EURGBP),
        'GBPJPY': ('NOGO', {**GI.V7_CONFIG['GBPJPY']}),
        'USDJPY': ('NOGO', None),  # ATRスケールfs(転移検証と同一)
    }
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0

    rows = []
    for pair, (grp, cfg) in pairs.items():
        df = D.load_duk(pair)
        atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        if cfg is None:
            fs = round(-750_000.0 * float(atr.median()) / ref_atr_jpy, 0)
            cfg = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
                   'max_levels': 5, 'float_stop': fs, 'quote_jpy': 1.0}
        r24 = D.ret24_series(df, atr)
        r120 = retN_series(df, atr, 120)
        bk_nfp24 = blackout_mask(df.index, nfp, 24)
        bk_nfp48 = blackout_mask(df.index, nfp, 48)
        bk_real24 = blackout_mask(df.index, real, 24)

        print('=' * 134)
        print(f'{pair} [{grp}]  (IS ret120 分位: '
              f'{np.nanpercentile(np.abs(r120[D.win_mask(df, *IS_WIN)]), [50, 80, 90, 95]).round(1)})')
        print('=' * 134)
        A = lambda **a: a
        base_m = metrics2(cfg, df, atr, ci, A())
        show('baseline', base_m)
        rows.append({'pair': pair, 'tag': 'baseline', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})

        for tag, arrays, kw in [
            ('E1 NFP±24h', A(black=bk_nfp24), {}),
            ('E1 NFP±48h', A(black=bk_nfp48), {}),
            ('E2 real±24h(22-26)', A(black=bk_real24), {}),
            ('T0 mom24=2.0 (ref)', A(ret24=r24), {'mom_thr': 2.0}),
            ('T1 mom120=3', A(ret2=r120), {'mom2_thr': 3.0}),
            ('T1 mom120=4', A(ret2=r120), {'mom2_thr': 4.0}),
            ('T1 mom120=6', A(ret2=r120), {'mom2_thr': 6.0}),
            ('T1 mom120=8', A(ret2=r120), {'mom2_thr': 8.0}),
            ('T2 mom24+mom120=4', A(ret24=r24, ret2=r120), {'mom_thr': 2.0, 'mom2_thr': 4.0}),
            ('T2 mom24+mom120=6', A(ret24=r24, ret2=r120), {'mom_thr': 2.0, 'mom2_thr': 6.0}),
        ]:
            m = metrics2(cfg, df, atr, ci, arrays, **kw)
            show(tag, m, base_m)
            rows.append({'pair': pair, 'tag': tag.replace(' ', '_'), **{k: v for k, v in m.items() if k != 'wfo_each'}})

        if grp == 'GO':
            for tag, arrays, kw in [
                ('T3 combo (ref)', A(ret24=r24), {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}),
                ('T3 combo+mom120=4', A(ret24=r24, ret2=r120),
                 {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7, 'mom2_thr': 4.0}),
                ('T3 combo+mom120=6', A(ret24=r24, ret2=r120),
                 {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7, 'mom2_thr': 6.0}),
            ]:
                m = metrics2(cfg, df, atr, ci, arrays, **kw)
                show(tag, m, base_m)
                rows.append({'pair': pair, 'tag': tag.replace(' ', '_'), **{k: v for k, v in m.items() if k != 'wfo_each'}})
        print()

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
