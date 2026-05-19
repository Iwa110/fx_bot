"""
trail_redesign_bt.py - BB戦略 SL Trail再設計 グリッドサーチBT
選択肢C: BB専用TP比率ベースtrail (stage3_activate_tp_ratio / stage3_distance_tp_ratio)

BTエンジン: simulate_with_stage2()
  stage2_activate = TP距離に対する進行率 (0.0〜1.0)
  stage2_distance = trail SL位置 = 現値 - TP距離 * distance_tp_ratio

  例: activate=0.5, distance=0.10
    → TP距離の50%進んだ地点でtrail発動
    → trail SL = 現値 - TP距離*0.10 (現値近傍を追尾)

比較:
  - Baseline: trail無効 (activate=99, TP一本勝負)
  - グリッド: activate=[0.3,0.4,0.5,0.6,0.7,0.8] x distance=[0.05,0.10,0.15,0.20]

対象: GBPJPY / USDJPY / EURJPY (sl_atr_mult=2.5, tp_sl_ratio=1.5)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from backtest import simulate_with_stage2

PAIRS_CFG = {
    'GBPJPY': {
        'is_jpy': True, 'pip_unit': 0.01, 'bb_sigma': 1.5,
        'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 3, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
    'USDJPY': {
        'is_jpy': True, 'pip_unit': 0.01, 'bb_sigma': 2.0,
        'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 2, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
    'EURJPY': {
        'is_jpy': True, 'pip_unit': 0.01, 'bb_sigma': 1.5,
        'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 3, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
}

N_BARS_ALL    = 999999
N_BARS_RECENT = 30000

ACTIVATE_RATIOS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
DISTANCE_RATIOS = [0.05, 0.10, 0.15, 0.20]


def run(sym, activate, distance, n_bars):
    return simulate_with_stage2(
        sym, PAIRS_CFG[sym],
        stage2_activate=activate,
        stage2_distance=distance,
        n_bars=n_bars,
    )


def fmt(sym, label, act, dist, r_all, r_rec):
    if r_all is None:
        return f'  {sym:7s} [{label:28s}] act={act:.2f} dist={dist:.2f}: BT失敗'
    pf_a = r_all['pf']
    wr_a = r_all['win_rate']
    n_a  = r_all['trades']
    tp_a = r_all['tp_count']
    tr_a = r_all['trail_count']
    sl_a = r_all['sl_count']
    rr_a = r_all.get('rr_actual', 0)
    pf_r = f"{r_rec['pf']:.3f}" if r_rec else '-'
    n_r  = str(r_rec['trades']) if r_rec else '-'
    return (f'  {sym:7s} [{label:28s}] act={act:.2f} dist={dist:.2f}: '
            f'PF={pf_a:.3f}  WR={wr_a:.1f}%  RR={rr_a:.3f}  '
            f'n={n_a}(TP={tp_a},TR={tr_a},SL={sl_a})  '
            f'| recent: PF={pf_r} n={n_r}')


print('=' * 105)
print('BB戦略 Trail再設計 BT  [選択肢C: TP比率ベース gridsearch]')
print('act=X: TP距離X倍進んだ地点でtrail発動, dist=Y: trail SL = 現値 - TP距離*Y')
print('=' * 105)

# ── Baseline: trail無効 ──────────────────────────────────────────────────────
print('\n【Baseline: trail無効 (TP一本勝負, act=99)】')
baselines = {}
for sym in PAIRS_CFG:
    r_all = run(sym, 99.0, 0.0, N_BARS_ALL)
    r_rec = run(sym, 99.0, 0.0, N_BARS_RECENT)
    baselines[sym] = r_all
    print(fmt(sym, 'trail-off(v14)', 99.0, 0.0, r_all, r_rec))

# ── グリッドサーチ ────────────────────────────────────────────────────────────
print('\n【グリッドサーチ: TP比率ベース Trail】')
best_by_pair = {}

for sym in PAIRS_CFG:
    print(f'\n  --- {sym} ---')
    base_pf = baselines[sym]['pf'] if baselines[sym] else 0.0
    best_pf  = base_pf
    best_cfg = None
    for act in ACTIVATE_RATIOS:
        for dist in DISTANCE_RATIOS:
            r_all = run(sym, act, dist, N_BARS_ALL)
            r_rec = run(sym, act, dist, N_BARS_RECENT)
            label = f'act={act:.1f},dist={dist:.2f}'
            print(fmt(sym, label, act, dist, r_all, r_rec))
            if r_all and r_all['pf'] > best_pf and r_all['trades'] >= 30:
                best_pf  = r_all['pf']
                best_cfg = (act, dist, r_all)
    best_by_pair[sym] = (best_cfg, best_pf)

# ── サマリー ─────────────────────────────────────────────────────────────────
print('\n' + '=' * 105)
print('【最終サマリー】')
print('=' * 105)
print(f'  {"ペア":7s}  {"Baseline(trail無効)":25s}  {"最良Trail設定":40s}')
print(f'  {"-"*7}  {"-"*25}  {"-"*40}')

for sym in PAIRS_CFG:
    b        = baselines[sym]
    best, _  = best_by_pair[sym]
    base_str = (f'PF={b["pf"]:.3f}  WR={b["win_rate"]:.1f}%  n={b["trades"]}'
                if b else 'N/A')
    if best:
        act, dist, r = best
        best_str = (f'act={act:.2f} dist={dist:.2f}  '
                    f'PF={r["pf"]:.3f}  WR={r["win_rate"]:.1f}%  '
                    f'n={r["trades"]}  TP={r["tp_count"]}  TR={r["trail_count"]}  ★推奨')
    else:
        best_str = 'trail無効がベスト'
    print(f'  {sym:7s}  {base_str:25s}  {best_str}')

print()
print('  ※ BTエンジン: simulate_with_stage2 (progress=TP距離比率, trail SL=現値-tp_dist*dist)')
print('  ※ 実機trail_monitor v15でTP比率ベースを実装する際は strategy_spec.md も更新')
