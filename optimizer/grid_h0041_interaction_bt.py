"""
grid_h0041_interaction_bt.py - H0041(CADCHF 部分利確 ptp_frac=0.35/ptp_mult=0.5)と
現行デモ構成(vps/grid_monitor.py v8 CADCHF: regime_short SMA1200 + cull_frac0.6)の相互作用BT。

H0041 は regime/cull 無しの core baseline で検証されたため、現行構成に足した場合の
非劣化を確認する。エンジン = grid_dirbias_improve_bt.run_bt (ptp 移植済, 回帰ガード
test_dirbias_ptp_port.py)。

構成:
  A 現行            : regime_short(1200) + cull0.6
  B A + ptp         : A + ptp(0.35/0.5)   [+ plateau ptp_frac 0.25/0.45]
  C regimeのみ + ptp: regime_short(1200) + ptp(0.35/0.5)
  D H0041単体       : ptp(0.35/0.5) のみ(再現確認: IS1.3978/OOS1.5032/wfoMin1.3626/req_cap 3.70M)

窓: IS 2015-21 / OOS 2022-26 / WFO = OOS内の暦年fold(60日未満の端数年は除外, loop と同一)。
req_cap_99:
  req99_cal  = 暦月basis・block3/60mo/20000・seed42 の DD99 (grid_capheavy_ddcompress と同一,
               vps コメント 'CADCHF cull0.6 2.27M' と同basis)
  req99_loop = loop/mc_capital.required_capital (DD99 と 最悪単発強制決済×gap_buffer の max,
               H0041 カードの 3.70M と同basis)
判定(B vs A): OOS PF 非劣化 ∧ WFO最小fold > 1.0 ∧ req99_cal 非増加 → 反映可。

実行: python optimizer/grid_h0041_interaction_bt.py
出力: grid_h0041_interaction_result.csv + console
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'loop'))
import grid_floatstop_bt as G          # noqa: E402
import grid_dd_reduction_bt as D       # noqa: E402
import grid_dirbias_improve_bt as DB   # noqa: E402
from grid_capheavy_ddcompress import req_cap_calendar  # noqa: E402
import mc_capital as MC                # noqa: E402

OUT = HERE / 'grid_h0041_interaction_result.csv'
PAIR = 'CADCHF'
CFG = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
       'max_levels': 5, 'float_stop': -943000.0, 'quote_jpy': 170.0}   # loop known_baselines と同一
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_LO = '2022-01-01'
MIN_FOLD_DAYS = 60
GATE_CFG = json.load(open(HERE / 'loop' / 'gate_config.json'))
PTP = {'ptp_frac': 0.35, 'ptp_mult': 0.5}


def n_trades(r):
    return r['n_tp'] + r.get('n_ptp', 0) + r['n_b48'] + r['n_fstop'] + len(r.get('cull_events', []))


def run(df, atr, ci, reg, kw, mask=None):
    k = dict(kw)
    sub = df
    if mask is not None:
        sub = df[mask]
        if 'short_block_up' in k:
            k['short_block_up'] = reg[mask.to_numpy()]
    return DB.run_bt(CFG, sub, atr, ci, collect=True, **k)


def evaluate(label, kw, df, atr, ci, reg):
    idx = df.index
    is_m = pd.Series((idx >= pd.Timestamp(IS_WIN[0], tz='UTC')) & (idx <= pd.Timestamp(IS_WIN[1], tz='UTC')), index=idx)
    oos_m = pd.Series(idx >= pd.Timestamp(OOS_LO, tz='UTC'), index=idx)
    full = run(df, atr, ci, reg, kw)
    isr = run(df, atr, ci, reg, kw, is_m)
    oos = run(df, atr, ci, reg, kw, oos_m)
    folds = []
    for y in sorted(set(idx[oos_m.to_numpy()].year)):
        ym = pd.Series(idx.year == y, index=idx)
        dy = idx[ym.to_numpy()]
        if (dy[-1] - dy[0]).days < MIN_FOLD_DAYS:
            continue
        folds.append((int(y), run(df, atr, ci, reg, kw, ym)['pf']))
    # req_cap
    rc_kw = dict(kw)
    r99_cal, netyr, _ = req_cap_calendar(PAIR, CFG, rc_kw)
    singles = list(full['fs_events']) + list(full['b48_events']) + list(full['cull_events'])
    mc = MC.required_capital(full['monthly'], singles, GATE_CFG)
    pf_is, pf_oos = isr['pf'], oos['pf']
    return {'label': label, 'is_pf': pf_is, 'oos_pf': pf_oos,
            'decay': round(1 - pf_oos / pf_is, 4), 'wfo_min': min(p for _, p in folds),
            'wfo_folds': ' '.join(f'{y}:{p:.2f}' for y, p in folds),
            'full_pf': full['pf'], 'n_is': n_trades(isr), 'n_oos': n_trades(oos), 'n_full': n_trades(full),
            'net_yr': round(netyr, 0), 'req99_cal': round(r99_cal, 0), 'req99_loop': mc['req_cap_99'],
            'p_loss_5yr': mc['p_loss_5yr'],
            'n_cull': len(full['cull_events']), 'n_fs': full['n_fstop'], 'n_b48': full['n_b48'],
            'n_ptp': full.get('n_ptp', 0)}


def main():
    df = D.load_duk(PAIR); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    reg = DB.sma_regime(df, 1200)
    A = {'short_block_up': reg, 'cull_frac': 0.6}
    specs = [
        ('A current(reg+cull0.6)', A),
        ('B A+ptp0.35', {**A, **PTP}),
        ('C reg+ptp0.35', {'short_block_up': reg, **PTP}),
        ('D H0041 alone', dict(PTP)),
        ('B plateau ptp0.25', {**A, 'ptp_frac': 0.25, 'ptp_mult': 0.5}),
        ('B plateau ptp0.45', {**A, 'ptp_frac': 0.45, 'ptp_mult': 0.5}),
        ('ref core baseline', {}),
    ]
    rows = []
    for label, kw in specs:
        r = evaluate(label, kw, df, atr, ci, reg); rows.append(r)
        print(f"{label:24s} IS={r['is_pf']:.4f} OOS={r['oos_pf']:.4f} decay={r['decay']:+.3f} "
              f"wfoMin={r['wfo_min']:.3f} [{r['wfo_folds']}] n={r['n_is']}/{r['n_oos']} "
              f"req99cal={r['req99_cal']:,.0f} req99loop={r['req99_loop']:,.0f} net/yr={r['net_yr']:,.0f} "
              f"cull={r['n_cull']} FS={r['n_fs']} B48={r['n_b48']} ptp={r['n_ptp']}")
    pd.DataFrame(rows).to_csv(OUT, index=False)
    a, b = rows[0], rows[1]
    ok = (b['oos_pf'] >= a['oos_pf'] - 1e-9, b['wfo_min'] > 1.0, b['req99_cal'] <= a['req99_cal'])
    print(f"verdict B vs A: OOS非劣化={ok[0]} wfoMin>1.0={ok[1]} req99非増加={ok[2]} -> "
          f"{'反映可' if all(ok) else '反映不可'}")
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
