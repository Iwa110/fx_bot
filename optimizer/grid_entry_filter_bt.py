"""
grid_entry_filter_bt.py - エントリー条件の改善案を検証(静的最良 AUDCAD atr1.5 が基準)。

負けパターン診断(grid_entry_analysis.py)で判明した2点を狙い撃つ:
  (P1) ラダー不利方向に24hモメンタムが強い時の追加建て(adv_mom>2)=PF1.02・最大gross_loss。
  (P2) CIが65ギリギリ(65-67)のエントリー=PF1.06。
改善案:
  F1 モメンタム・ゲート: 新規建て(新ラダー&追加レベル両方)を、その方向の24hリターン(ATR正規化,t-1)が
     不利側に thr 超のとき抑止。long は ret24<=-thr で抑止, short は ret24>=+thr で抑止。
  F2 CIファーム化: ci_threshold 65→67。
  F3 F1+F2 併用。

ガードレール: thr/ci は IS=2015-2021 で凍結 → OOS=2022-2026・WFO(純OOS年2022-25)で評価。
合否=baseline(atr1.5)を OOS PF・WFO中央・maxDD で悪化させず、リスク(maxDD/nFS/worst)を有意改善。
特徴量は t-1。エンジンは grid_floatstop_bt を1:1踏襲+方向別エントリーゲートのみ追加(gate無効で静的一致)。

実行: python3 optimizer/grid_entry_filter_bt.py  出力: grid_entry_filter_bt_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G, grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_entry_filter_bt_result.csv'
PAIR = 'AUDCAD'
BASE = {**GI.V7_CONFIG[PAIR], 'atr_mult': 1.5}
CONTRACT = G.CONTRACT
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def ret24_series(df, atr):
    return (((df['close'] - df['close'].shift(24)) / atr.reindex(df.index)).shift(1)).to_numpy()


def run_bt(cfg, df, atr_series, ci_series, ret24, mom_thr=None):
    """mom_thr=None → ゲート無効(=静的)。それ以外で方向別モメンタム・ゲート。"""
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

        if long_pos:
            if sum(pj(bl-p['entry']) for p in long_pos) <= float_stop:
                pp = [pj(bl-p['entry']) for p in long_pos]; ev = sum(pp)
                fs_pp.extend(pp); fs_pnls.append(ev); realized += ev; worst = min(worst, ev)
                long_pos = []; b48_ls = None
        if short_pos:
            if sum(pj(p['entry']-bh) for p in short_pos) <= float_stop:
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
        r = ret24[i]
        # 方向別モメンタム・ゲート(t-1). NaN は通す。
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


def win_mask(df, lo, hi):
    m = pd.Series(True, index=df.index)
    if lo: m &= df.index >= pd.Timestamp(lo, tz='UTC')
    if hi: m &= df.index <= pd.Timestamp(hi, tz='UTC')+pd.Timedelta(days=1)
    return m.to_numpy()


def metrics(cfg, df, atr, ci, ret24, mom_thr=None):
    def w(lo=None, hi=None):
        m = win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        return run_bt(cfg, sub, atr, ci, ret24[m], mom_thr)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = []
    for y in WFO_YEARS:
        rr = w(f'{y}-01-01', f'{y}-12-31')
        if rr and rr['n_tp'] >= 10: wfo.append(rr['pf'])
    wfo = np.array(wfo)
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nb48': full['n_b48'], 'full_worst': full['worst_event'],
            'is_pf': isr['pf'], 'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)), 'wfo_min': float(wfo.min()),
            'wfo_gt12': float((wfo > 1.2).mean()), 'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m, base=None):
    def s(v, b, lower_better=False):
        if base is None: return ' '
        return ('+' if ((v <= b) if lower_better else (v >= b)) else '-')
    print(f'{tag:20s} full PF={m["full_pf"]:.2f} net={m["full_net"]:>11,.0f} DD={m["full_dd"]:>9,.0f}'
          f'{s(m["full_dd"], base["full_dd"] if base else 0, True) if base else ""} '
          f'nFS={m["full_nfs"]:2d} nB48={m["full_nb48"]:2d} worst={m["full_worst"]:>10,.0f} | '
          f'IS={m["is_pf"]:.2f} OOS={m["oos_pf"]:.2f}{s(m["oos_pf"], base["oos_pf"] if base else 0) if base else ""} '
          f'OOSdd={m["oos_dd"]:>9,.0f} | WFOmed={m["wfo_med"]:.2f}{s(m["wfo_med"], base["wfo_med"] if base else 0) if base else ""} '
          f'min={m["wfo_min"]:.2f} >1.2={m["wfo_gt12"]:.2f} {m["wfo_each"]}')


def main():
    df = load_duk(PAIR); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    ret24 = ret24_series(df, atr)
    rows = []

    base_m = metrics(BASE, df, atr, ci, ret24, None)
    print('BASELINE = 静的最良 atr1.5/ci65/lv5/fs-750k')
    show('baseline', base_m)
    rows.append({'tag': 'baseline', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})

    print('\n=== F1: モメンタム・ゲート (方向別 24hリターン/ATR が不利側 thr 超で新規建て抑止) ===')
    f1 = {}
    for thr in [1.5, 2.0, 2.5, 3.0]:
        m = metrics(BASE, df, atr, ci, ret24, thr); f1[thr] = m
        show(f'F1 mom_thr={thr}', m, base_m)
        rows.append({'tag': f'F1_mom{thr}', **{k: v for k, v in m.items() if k != 'wfo_each'}})
    best_thr = max(f1, key=lambda t: f1[t]['is_pf'])
    print(f'  IS最良 mom_thr={best_thr} (IS PF={f1[best_thr]["is_pf"]:.2f})')

    print('\n=== F2: CIファーム化 (ci 65→67) ===')
    cfg67 = {**BASE, 'ci_threshold': 67.0}
    m2 = metrics(cfg67, df, atr, ci, ret24, None)
    show('F2 ci=67', m2, base_m)
    rows.append({'tag': 'F2_ci67', **{k: v for k, v in m2.items() if k != 'wfo_each'}})

    print('\n=== F3: F1(IS最良) + F2 併用 ===')
    m3 = metrics(cfg67, df, atr, ci, ret24, best_thr)
    show(f'F3 mom{best_thr}+ci67', m3, base_m)
    rows.append({'tag': f'F3_mom{best_thr}_ci67', **{k: v for k, v in m3.items() if k != 'wfo_each'}})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
