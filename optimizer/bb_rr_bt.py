"""
bb_rr_bt.py - BB戦略 実RR改善 比較バックテスト
案A: SL縮小 / 案B: RR拡大 / 案C: trail無効化(TP一本勝負) / 案D: SL×RR組み合わせ

BTエンジン: simulate_with_stage2() (stage2_activate=99 = trail事実上無効 = TP一本勝負)
ATR: 5m足ATR（BT内部一貫。H1 ATRとは異なるが相対比較として有効）
フィルター: GBPJPY/USDJPY に use_htf4h=True
"""

import sys
import os
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import simulate_with_stage2, load_csv, BB_PAIRS_CFG

# ──────────────────────────────────────────
# ペア別設定（bb_monitor v22/v23 準拠）
# ──────────────────────────────────────────
PAIRS_BASE = {
    'GBPJPY': {
        'is_jpy':       True,
        'pip_unit':     0.01,
        'bb_sigma':     1.5,
        'use_htf4h':    True,
        'htf_range_sigma': 10.0,   # 実質無効（htf4h側で制御）
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'spread_pips':  3,
        'cooldown_bars': 3,
        'bb_period':    20,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'atr_period':   14,
        'filter_type':  None,
    },
    'USDJPY': {
        'is_jpy':       True,
        'pip_unit':     0.01,
        'bb_sigma':     2.0,
        'use_htf4h':    True,
        'htf_range_sigma': 10.0,
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'spread_pips':  2,
        'cooldown_bars': 3,
        'bb_period':    20,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'atr_period':   14,
        'filter_type':  None,
    },
    'EURJPY': {
        'is_jpy':       True,
        'pip_unit':     0.01,
        'bb_sigma':     1.5,
        'use_htf4h':    True,
        'htf_range_sigma': 10.0,
        'rsi_buy_max':  45,
        'rsi_sell_min': 55,
        'spread_pips':  3,
        'cooldown_bars': 3,
        'bb_period':    20,
        'htf_period':   20,
        'htf_sigma':    1.5,
        'atr_period':   14,
        'filter_type':  None,
    },
}

# 現状パラメータ（v26: GBPJPY/USDJPY sl_atr_mult 3.0→2.5）
CURRENT_PARAMS = {
    'GBPJPY': {'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5},
    'USDJPY': {'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5},
    'EURJPY': {'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5},
}

# 全データ範囲
N_BARS_ALL    = 999999   # 全データ（データファイルに依存）
N_BARS_RECENT = 30000    # 直近2年相当（5m足: 30000bars ≈ 2.5年）

# trail無効 = Stage2がTP前99倍の地点で起動 → 実質発動しない
NO_TRAIL_ACTIVATE = 99.0
NO_TRAIL_DISTANCE = 0.0


def run_single(sym, sl_mult, tp_rr, n_bars, label=''):
    cfg = PAIRS_BASE[sym].copy()
    cfg['sl_atr_mult'] = sl_mult
    cfg['tp_sl_ratio'] = tp_rr
    res = simulate_with_stage2(
        sym, cfg,
        stage2_activate=NO_TRAIL_ACTIVATE,
        stage2_distance=NO_TRAIL_DISTANCE,
        sl_atr_mult=None,   # cfg内で設定済み
        n_bars=n_bars,
    )
    return res


def run_with_trail(sym, sl_mult, tp_rr, activate, distance, n_bars):
    cfg = PAIRS_BASE[sym].copy()
    cfg['sl_atr_mult'] = sl_mult
    cfg['tp_sl_ratio'] = tp_rr
    return simulate_with_stage2(
        sym, cfg,
        stage2_activate=activate,
        stage2_distance=distance,
        sl_atr_mult=None,
        n_bars=n_bars,
    )


def format_row(sym, label, sl, rr, res_all, res_rec):
    if res_all is None:
        return f'  {sym:7s} [{label:20s}] sl={sl:.1f} rr={rr:.1f}: BT失敗'
    pf_a = res_all['pf']
    wr_a = res_all['win_rate']
    n_a  = res_all['trades']
    tp_a = res_all['tp_count']
    sl_a = res_all['sl_count']
    rr_a = res_all.get('rr_actual', 0)

    pf_r = res_rec['pf']    if res_rec else '-'
    n_r  = res_rec['trades'] if res_rec else '-'

    pf_r_str = f'{pf_r:.3f}' if isinstance(pf_r, float) else pf_r
    n_r_str  = str(n_r)

    return (f'  {sym:7s} [{label:20s}] sl={sl:.1f} rr={rr:.1f}: '
            f'PF={pf_a:.3f}  WR={wr_a:.1f}%  RR={rr_a:.3f}  '
            f'n={n_a}(TP={tp_a},SL={sl_a})  '
            f'| recent: PF={pf_r_str} n={n_r_str}')


print('=' * 90)
print('BB戦略 実RR改善 比較バックテスト (trail無効化 = TP一本勝負)')
print('=' * 90)

# ────────────────────────────────────────────────────
# 現状ベースライン
# ────────────────────────────────────────────────────
print('\n【現状ベースライン】sl_mult=現状値, tp_rr=1.5 (trail無効化でTP到達率を計測)')
baseline = {}
for sym, cp in CURRENT_PARAMS.items():
    r_all = run_single(sym, cp['sl_atr_mult'], cp['tp_sl_ratio'], N_BARS_ALL)
    r_rec = run_single(sym, cp['sl_atr_mult'], cp['tp_sl_ratio'], N_BARS_RECENT)
    baseline[sym] = r_all
    print(format_row(sym, 'current', cp['sl_atr_mult'], cp['tp_sl_ratio'], r_all, r_rec))

# ────────────────────────────────────────────────────
# 案A: SL縮小 (tp_rr=1.5固定)
# ────────────────────────────────────────────────────
print('\n【案A: SL縮小 (tp_rr=1.5固定)】')
SL_CANDIDATES = [1.5, 2.0, 2.5, 3.0, 3.5]
case_a_best = {}
for sym in PAIRS_BASE:
    print(f'  --- {sym} ---')
    best_pf   = -1
    best_cfg  = None
    for sl in SL_CANDIDATES:
        r_all = run_single(sym, sl, 1.5, N_BARS_ALL)
        r_rec = run_single(sym, sl, 1.5, N_BARS_RECENT)
        print(format_row(sym, f'A:sl={sl}', sl, 1.5, r_all, r_rec))
        if r_all and r_all['pf'] > best_pf:
            best_pf  = r_all['pf']
            best_cfg = (sl, 1.5)
    case_a_best[sym] = best_cfg
print()
for sym, cfg in case_a_best.items():
    print(f'  → {sym} 案A最良: sl={cfg[0]}, rr={cfg[1]}')

# ────────────────────────────────────────────────────
# 案B: RR引き上げ (sl_mult=現状値固定)
# ────────────────────────────────────────────────────
print('\n【案B: RR引き上げ (sl_mult=現状値固定)】')
RR_CANDIDATES = [1.5, 2.0, 2.5, 3.0]
case_b_best = {}
for sym in PAIRS_BASE:
    print(f'  --- {sym} ---')
    best_pf  = -1
    best_cfg = None
    sl_cur   = CURRENT_PARAMS[sym]['sl_atr_mult']
    for rr in RR_CANDIDATES:
        r_all = run_single(sym, sl_cur, rr, N_BARS_ALL)
        r_rec = run_single(sym, sl_cur, rr, N_BARS_RECENT)
        print(format_row(sym, f'B:rr={rr}', sl_cur, rr, r_all, r_rec))
        if r_all and r_all['pf'] > best_pf:
            best_pf  = r_all['pf']
            best_cfg = (sl_cur, rr)
    case_b_best[sym] = best_cfg
print()
for sym, cfg in case_b_best.items():
    print(f'  → {sym} 案B最良: sl={cfg[0]}, rr={cfg[1]}')

# ────────────────────────────────────────────────────
# 案C: trail完全無効化（TP一本勝負）= 現状sl/rr で BT
# ────────────────────────────────────────────────────
print('\n【案C: trail完全無効化 (TP一本勝負) ← 上記ベースラインと同一、再掲】')
print('  上記ベースラインが案Cの結果です。TP到達率が実稼働より大幅向上するか確認。')

# ────────────────────────────────────────────────────
# 案D: SL × RR 組み合わせグリッド
# ────────────────────────────────────────────────────
print('\n【案D: SL × RR 組み合わせグリッド】')
SL_D = [1.5, 2.0, 2.5]
RR_D = [1.5, 2.0, 2.5]

case_d_best    = {}
case_d_best_pf = {}
for sym in PAIRS_BASE:
    print(f'  --- {sym} ---')
    best_pf  = -1
    best_cfg = None
    for sl in SL_D:
        for rr in RR_D:
            r_all = run_single(sym, sl, rr, N_BARS_ALL)
            r_rec = run_single(sym, sl, rr, N_BARS_RECENT)
            print(format_row(sym, f'D:sl={sl},rr={rr}', sl, rr, r_all, r_rec))
            if r_all and r_all['pf'] > best_pf and r_all['trades'] >= 30:
                best_pf  = r_all['pf']
                best_cfg = (sl, rr)
    case_d_best[sym]    = best_cfg
    case_d_best_pf[sym] = best_pf

print()
print('=' * 90)
print('【最終サマリー】')
print('=' * 90)
for sym in PAIRS_BASE:
    base = baseline[sym]
    d_cfg = case_d_best.get(sym)
    a_cfg = case_a_best.get(sym)
    b_cfg = case_b_best.get(sym)
    cp    = CURRENT_PARAMS[sym]

    b_pf = base['pf'] if base else 0
    d_pf = case_d_best_pf.get(sym, 0)

    a_res = run_single(sym, a_cfg[0], a_cfg[1], N_BARS_ALL) if a_cfg else None
    b_res = run_single(sym, b_cfg[0], b_cfg[1], N_BARS_ALL) if b_cfg else None
    d_res = run_single(sym, d_cfg[0], d_cfg[1], N_BARS_ALL) if d_cfg else None

    print(f'\n  {sym}:')
    print(f'    現状(trail有): PF=実稼働で確認 WR=実稼働で確認 実RR=0.276')
    print(f'    現状(trail無): PF={b_pf:.3f}')
    if a_cfg:
        a_r = a_res
        print(f'    案A(sl={a_cfg[0]}): PF={a_r["pf"]:.3f}  WR={a_r["win_rate"]:.1f}%  n={a_r["trades"]}')
    if b_cfg:
        b_r = b_res
        print(f'    案B(rr={b_cfg[1]}): PF={b_r["pf"]:.3f}  WR={b_r["win_rate"]:.1f}%  n={b_r["trades"]}')
    if d_cfg:
        d_r = d_res
        print(f'    案D(sl={d_cfg[0]},rr={d_cfg[1]}): PF={d_r["pf"]:.3f}  WR={d_r["win_rate"]:.1f}%  n={d_r["trades"]}  ★推奨')

print('\n  採用基準: PF>1.2 かつ n>30 かつ 全期間/直近両方で改善')
print('  ※BTは5m ATRベース。実機はH1 ATRのため方向感の参考として解釈すること')

# ────────────────────────────────────────────────────
# 案E: Stage2 activate × distance スイープ（SL=2.5固定）
# ────────────────────────────────────────────────────
print('\n' + '=' * 90)
print('【案E: Stage2 activate × distance スイープ (sl=2.5 rr=1.5固定)】')
print('  目的: Stage2 trailがRRを下げている原因か・最適設定を特定')
print('=' * 90)

E_ACTIVATE  = [0.5, 1.0, 1.5, 2.0, 2.5]
E_DISTANCE  = [0.1, 0.2, 0.3]
E_SL        = 2.5
E_RR        = 1.5

case_e_results  = {}   # sym -> list of (activate, distance, res_all, res_rec)
case_e_best     = {}   # sym -> (activate, distance, res_all)

for sym in PAIRS_BASE:
    print(f'\n  --- {sym} ---')
    best_rr   = -1.0
    best_cfg  = None
    best_res  = None
    sym_rows  = []
    for act in E_ACTIVATE:
        for dist in E_DISTANCE:
            r_all = run_with_trail(sym, E_SL, E_RR, act, dist, N_BARS_ALL)
            r_rec = run_with_trail(sym, E_SL, E_RR, act, dist, N_BARS_RECENT)
            label = f'E:act={act} dist={dist}'
            print(format_row(sym, label, E_SL, E_RR, r_all, r_rec))
            sym_rows.append((act, dist, r_all, r_rec))
            if r_all and r_all.get('rr_actual', 0) > best_rr:
                best_rr  = r_all.get('rr_actual', 0)
                best_cfg = (act, dist)
                best_res = r_all
    case_e_results[sym] = sym_rows
    case_e_best[sym]    = (best_cfg, best_res)

print('\n' + '=' * 90)
print('【案E 最良設定まとめ (RR>0.5 かつ PF>1.0 を優先、なければPF最大)】')
print('=' * 90)
e_summary_line = []
for sym in PAIRS_BASE:
    rows = case_e_results[sym]
    # RR>0.5 かつ PF>1.0 フィルター
    qualified = [
        (act, dist, ra) for act, dist, ra, _ in rows
        if ra and ra.get('rr_actual', 0) > 0.5 and ra.get('pf', 0) > 1.0
    ]
    if qualified:
        best = max(qualified, key=lambda x: x[2].get('rr_actual', 0))
    else:
        valid = [(act, dist, ra) for act, dist, ra, _ in rows if ra]
        if valid:
            best = max(valid, key=lambda x: x[2].get('pf', 0))
        else:
            print(f'  {sym}: データなし')
            continue
    act, dist, ra = best
    tag = '★RR+PF合格' if qualified else '(PF最大フォールバック)'
    line = (f'  {sym}: act={act} dist={dist}  '
            f'PF={ra["pf"]:.3f}  WR={ra["win_rate"]:.1f}%  '
            f'RR={ra.get("rr_actual", 0):.3f}  n={ra["trades"]}  {tag}')
    print(line)
    e_summary_line.append(f'{sym} act={act}/dist={dist} RR={ra.get("rr_actual",0):.3f}')

print()
if e_summary_line:
    print('案E推奨一行サマリー: ' + ' | '.join(e_summary_line))
