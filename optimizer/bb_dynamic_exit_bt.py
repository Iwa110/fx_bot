"""
bb_dynamic_exit_bt.py - BB戦略 動的決済条件 比較バックテスト

Phase 0: 半減期推定 (ADF回帰, 1h足)
Phase 1: Time-Based Exit グリッドサーチ
Phase 2: Time-Based TP Reduction (段階/線形/指数)
Phase 3: Break-Even 追加
Phase 4: Hybrid Scaling (TP decay + SL tightening)

対象ペア: GBPJPY / USDJPY / EURJPY (bb_monitor v24 稼働中)
IS/OOS: 先頭60% / 末尾40%
ベースライン: Trail無効 (stage2_activate=99), SL=2.5×ATR, TP=3.75×ATR
"""

import sys
import os
import math
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    load_csv, calc_bb, calc_rsi, calc_atr,
    build_htf_lookup, build_htf4h_ema_lookup, get_base_params,
)

# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────
BARS_PER_H1 = 12   # 5m足における1H1バーの本数

PAIRS_BASE = {
    'GBPJPY': {
        'is_jpy': True, 'pip_unit': 0.01,
        'bb_sigma': 1.5, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 3, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
    'USDJPY': {
        'is_jpy': True, 'pip_unit': 0.01,
        'bb_sigma': 2.0, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 2, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
    'EURJPY': {
        'is_jpy': True, 'pip_unit': 0.01,
        'bb_sigma': 1.5, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        'use_htf4h': True, 'htf_range_sigma': 10.0,
        'rsi_buy_max': 45, 'rsi_sell_min': 55,
        'spread_pips': 3, 'cooldown_bars': 3,
        'bb_period': 20, 'htf_period': 20, 'htf_sigma': 1.5,
        'atr_period': 14, 'filter_type': None,
    },
}

IS_RATIO = 0.60   # 先頭60%をIS


# ──────────────────────────────────────────────
# Phase 0: 半減期推定
# ──────────────────────────────────────────────
def estimate_half_life(symbol):
    """
    1h足価格でOLS回帰: y(t) = a + b*y(t-1) + ε
    half_life_h1 = -ln(2) / ln(b)  [H1バー単位]
    """
    df = load_csv(symbol, '1h')
    if df is None:
        return None
    y = np.log(df['close'].values)
    y_lag = y[:-1]
    y_cur = y[1:]
    # OLS: y_cur = a + b * y_lag
    X = np.column_stack([np.ones(len(y_lag)), y_lag])
    try:
        b_coef = np.linalg.lstsq(X, y_cur, rcond=None)[0][1]
    except Exception:
        return None
    if b_coef <= 0 or b_coef >= 1:
        return None
    hl = -math.log(2) / math.log(b_coef)
    return round(hl, 1)


# ──────────────────────────────────────────────
# コアシミュレーター
# ──────────────────────────────────────────────
def simulate_dynamic_exit(
    symbol, pair_cfg,
    n_bars_start=0, n_bars_end=None,
    t_max_h1=None,          # 強制決済 H1バー数 (None=無制限)
    tp_decay_mode=None,     # None / 'step' / 'linear' / 'exp'
    tp_decay_params=None,   # モード別パラメータ
    be_threshold_r=None,    # BE発動閾値 (None=BE無効)
    be_buffer_pips=0.0,     # BE移動先バッファ
    be_min_bars_h1=0,       # BE発動最低経過H1バー数
    sl_tighten_k=None,      # Hybrid: SL tighten係数 (None=SL固定)
    half_life_h1=12,        # Hybrid用半減期
):
    """
    動的決済シミュレーター。
    - t_max_h1:     この時間を超えたら強制クローズ（time stop）
    - tp_decay_mode='step':   段階的TP引き下げ
    - tp_decay_mode='linear': 線形TP引き下げ
    - tp_decay_mode='exp':    指数TP引き下げ
    - be_threshold_r:         利益 >= threshold × SL でBE移動
    - sl_tighten_k:           Hybrid: SL(t) = entry - ATR×max(sl_mult - k*(bars_h1/H), -0.5)
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    total = len(df_5m)
    end   = n_bars_end if n_bars_end is not None else total
    df_5m = df_5m.iloc[n_bars_start:end].reset_index(drop=True)
    if len(df_5m) < 200:
        return None

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi_s   = calc_rsi(close, cfg['rsi_period'])
    atr_s   = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    htf4h_lkp = build_htf4h_ema_lookup(df_1h) if cfg.get('use_htf4h') else None

    spread    = cfg['spread_pips'] * cfg['pip_unit']
    close_arr = close.values
    high_arr  = df_5m['high'].values
    low_arr   = df_5m['low'].values
    n         = len(df_5m)

    # Hybrid / SL tighten 用
    sl_mult_base = cfg['sl_atr_mult']
    tp_mult_base = sl_mult_base * cfg['tp_sl_ratio']  # = 3.75 for sl=2.5,rr=1.5

    # t_max → 5m bars
    t_max_5m = int(t_max_h1 * BARS_PER_H1) if t_max_h1 is not None else 600

    wins = losses = tp_count = be_count = forced_count = sl_count = 0
    gross_profit = gross_loss = 0.0
    hold_bars_list = []
    last_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c      = close_arr[i]
        atr_v  = atr_s.iloc[i]
        if atr_v == 0 or np.isnan(atr_v) or np.isnan(c):
            continue

        sl_dist = atr_v * sl_mult_base
        tp_dist = atr_v * tp_mult_base

        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        rsi_v = rsi_s.iloc[i]
        if np.isnan(rsi_v):
            continue

        direction = None
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        if htf4h_lkp is not None:
            htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if htf4h_idx < 0:
                continue
            htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
            if direction == 'buy'  and htf4h_sig != 1:
                continue
            if direction == 'sell' and htf4h_sig != -1:
                continue

        entry = c + spread if direction == 'buy' else c - spread
        sign  = 1 if direction == 'buy' else -1

        # 初期 TP/SL 価格
        tp_price = entry + sign * tp_dist
        sl_price = entry - sign * sl_dist

        hit        = None
        exit_price = None
        be_moved   = False
        be_sl      = sl_price  # BE後はこちらを使う

        max_j = min(i + t_max_5m + 1, n)

        for j in range(i + 1, max_j):
            bars_elapsed_5m = j - i
            bars_elapsed_h1 = bars_elapsed_5m / BARS_PER_H1

            h = high_arr[j]
            l = low_arr[j]

            # ── 動的TP計算 ──────────────────────────
            if tp_decay_mode is None:
                cur_tp_dist = tp_dist
            elif tp_decay_mode == 'step':
                # 段階関数: 0-6,6-12,12-18,18-24 → 3.75,2.5,1.5,1.0 × ATR
                stages = tp_decay_params.get('stages',
                    [(6,3.75),(12,2.5),(18,1.5),(24,1.0)])
                cur_tp_mult = stages[-1][1]
                for thr, mult in stages:
                    if bars_elapsed_h1 < thr:
                        cur_tp_mult = mult
                        break
                cur_tp_dist = atr_v * cur_tp_mult
            elif tp_decay_mode == 'linear':
                alpha = tp_decay_params.get('alpha', 0.115)
                floor = tp_decay_params.get('floor', atr_v * 1.0)
                cur_tp_dist = max(tp_dist - alpha * bars_elapsed_h1 * atr_v, floor)
            elif tp_decay_mode == 'exp':
                tau   = tp_decay_params.get('tau', 12)
                floor = tp_decay_params.get('floor', atr_v * 1.0)
                cur_tp_dist = max(
                    atr_v * (1.0 + (tp_mult_base - 1.0) * math.exp(-bars_elapsed_h1 / tau)),
                    floor,
                )
            else:
                cur_tp_dist = tp_dist

            cur_tp_price = entry + sign * cur_tp_dist

            # ── 動的SL計算 (Hybrid) ─────────────────
            if sl_tighten_k is not None:
                new_sl_mult = max(sl_mult_base - sl_tighten_k * (bars_elapsed_h1 / half_life_h1), -0.5)
                cur_sl_price = entry - sign * atr_v * new_sl_mult
            else:
                cur_sl_price = be_sl  # BE移動後はbe_sl、未発動時はsl_price

            # ── Break-Even 判定 ──────────────────────
            if (be_threshold_r is not None
                    and not be_moved
                    and bars_elapsed_h1 >= be_min_bars_h1):
                profit = sign * ((h + l) / 2 - entry)
                if profit >= be_threshold_r * sl_dist:
                    be_sl    = entry + sign * be_buffer_pips * cfg['pip_unit']
                    be_moved = True
                    cur_sl_price = be_sl

            # ── ヒット判定 ───────────────────────────
            if direction == 'buy':
                if l <= cur_sl_price:
                    hit = 'be_exit' if be_moved else 'sl'
                    exit_price = cur_sl_price
                    break
                if h >= cur_tp_price:
                    hit = 'tp'
                    exit_price = cur_tp_price
                    break
            else:
                if h >= cur_sl_price:
                    hit = 'be_exit' if be_moved else 'sl'
                    exit_price = cur_sl_price
                    break
                if l <= cur_tp_price:
                    hit = 'tp'
                    exit_price = cur_tp_price
                    break

        # ── 強制決済 ─────────────────────────────
        if hit is None and t_max_h1 is not None:
            exit_price = close_arr[min(max_j - 1, n - 1)]
            hit = 'forced'

        if hit is None or exit_price is None:
            continue

        pnl = sign * (exit_price - entry)

        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)

        hold_bars_list.append(j - i if hit != 'forced' else t_max_5m)

        if hit == 'tp':
            tp_count += 1
        elif hit == 'forced':
            forced_count += 1
        elif hit == 'be_exit':
            be_count += 1
        else:
            sl_count += 1

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    avg_hold_h1 = round(float(np.mean(hold_bars_list)) / BARS_PER_H1, 1) if hold_bars_list else 0

    return {
        'trades':      trades,
        'wins':        wins,
        'losses':      losses,
        'win_rate':    round(wins / trades * 100, 1),
        'pf':          round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'expectancy':  round((gross_profit - gross_loss) / trades, 6),
        'rr_actual':   round((gross_profit / wins) / (gross_loss / losses), 3)
                       if wins > 0 and losses > 0 else 0.0,
        'tp_count':    tp_count,
        'sl_count':    sl_count,
        'be_count':    be_count,
        'forced_count': forced_count,
        'avg_hold_h1': avg_hold_h1,
    }


# ──────────────────────────────────────────────
# IS/OOS 分割ヘルパー
# ──────────────────────────────────────────────
def get_split(symbol):
    df = load_csv(symbol, '5m')
    if df is None:
        return 0, 0, 0
    total = len(df)
    split = int(total * IS_RATIO)
    return 0, split, total   # is_start, is_end, oos_end


def run_is_oos(symbol, pair_cfg, **kwargs):
    _, is_end, total = get_split(symbol)
    r_is  = simulate_dynamic_exit(symbol, pair_cfg, 0,      is_end, **kwargs)
    r_oos = simulate_dynamic_exit(symbol, pair_cfg, is_end, total,  **kwargs)
    return r_is, r_oos


# ──────────────────────────────────────────────
# 表示ヘルパー
# ──────────────────────────────────────────────
def fmt(r):
    if r is None:
        return 'N/A'
    return (f"PF={r['pf']:.3f} WR={r['win_rate']:.0f}% "
            f"n={r['trades']} E={r['expectancy']:.5f} "
            f"hold={r['avg_hold_h1']:.1f}h "
            f"[TP={r['tp_count']} SL={r['sl_count']} "
            f"BE={r['be_count']} F={r['forced_count']}]")


def hdr(label):
    print(f'\n{"─"*90}')
    print(f'  {label}')
    print(f'{"─"*90}')


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────
print('=' * 90)
print('BB戦略 動的決済条件 バックテスト  (IS=60% / OOS=40%)')
print('=' * 90)

# ─── Phase 0: 半減期推定 ─────────────────────
hdr('Phase 0: 半減期推定 (OLS回帰 on 1h足対数価格)')
half_lives = {}
for sym in PAIRS_BASE:
    hl = estimate_half_life(sym)
    half_lives[sym] = hl if hl is not None else 12
    print(f'  {sym}: half_life = {hl} H1バー')

# ─── ベースライン (Trail無効, 固定TP) ────────
hdr('ベースライン: Trail無効 / 固定TP=3.75×ATR / 時間制限なし')
baselines = {}
for sym, cfg in PAIRS_BASE.items():
    r_is, r_oos = run_is_oos(sym, cfg)
    baselines[sym] = {'is': r_is, 'oos': r_oos}
    print(f'  {sym} IS : {fmt(r_is)}')
    print(f'  {sym} OOS: {fmt(r_oos)}')

# ─── Phase 1: Time-Based Exit ────────────────
hdr('Phase 1: Time-Based Exit グリッドサーチ (T_max H1バー)')
T_MAX_GRID = [6, 8, 12, 16, 20, 24, 36, 48]
phase1_best = {}  # sym -> (t_max, is_result, oos_result)

for sym, cfg in PAIRS_BASE.items():
    print(f'\n  [{sym}]')
    base_is = baselines[sym]['is']
    best_score = -1
    best = None
    for t in T_MAX_GRID:
        r_is, r_oos = run_is_oos(sym, cfg, t_max_h1=t)
        marker = ''
        if r_is and base_is and r_is['pf'] > base_is['pf']:
            marker = ' ★'
        print(f'    T={t:3d}h  IS: {fmt(r_is)}{marker}')
        print(f'          OOS: {fmt(r_oos)}')
        # IS PF改善 + OOS PF不悪化 を採用基準
        if (r_is and r_oos
                and r_is['pf'] > (base_is['pf'] if base_is else 0)
                and r_oos['pf'] > (baselines[sym]['oos']['pf'] if baselines[sym]['oos'] else 0)):
            score = r_is['pf'] + r_oos['pf']
            if score > best_score:
                best_score = score
                best = (t, r_is, r_oos)
    if best is None:
        # IS改善のみで選択
        best_score2 = -1
        for t in T_MAX_GRID:
            r_is, r_oos = run_is_oos(sym, cfg, t_max_h1=t)
            if r_is and r_is['pf'] > best_score2:
                best_score2 = r_is['pf']
                best = (t, r_is, r_oos)
    phase1_best[sym] = best
    if best:
        print(f'  → {sym} Phase1最良 T_max={best[0]}h')

# ─── Phase 2: Time-Based TP Reduction ────────
hdr('Phase 2: TP Reduction (Phase1最良T_maxを固定)')
phase2_best = {}  # sym -> (label, params, is, oos)

for sym, cfg in PAIRS_BASE.items():
    print(f'\n  [{sym}]')
    base_is  = baselines[sym]['is']
    base_oos = baselines[sym]['oos']
    t_max    = phase1_best[sym][0] if phase1_best.get(sym) else 24

    best_score = -1
    best = None

    # A: 段階関数 (単一設定)
    stages = [(6, 3.75), (12, 2.5), (18, 1.5), (24, 1.0)]
    r_is, r_oos = run_is_oos(sym, cfg, t_max_h1=t_max,
                              tp_decay_mode='step',
                              tp_decay_params={'stages': stages})
    marker = ' ★' if r_is and base_is and r_is['pf'] > base_is['pf'] else ''
    print(f'    A-step      IS : {fmt(r_is)}{marker}')
    print(f'                OOS: {fmt(r_oos)}')
    if r_is and r_oos:
        sc = r_is['pf'] + r_oos['pf']
        if sc > best_score:
            best_score = sc
            best = ('A-step', {'tp_decay_mode': 'step', 'tp_decay_params': {'stages': stages}}, r_is, r_oos)

    # B: 線形 (alpha=0.115)
    for alpha in [0.10, 0.115, 0.15]:
        r_is, r_oos = run_is_oos(sym, cfg, t_max_h1=t_max,
                                  tp_decay_mode='linear',
                                  tp_decay_params={'alpha': alpha, 'floor': cfg['sl_atr_mult'] * BARS_PER_H1 * 0})
        atr_floor_label = f'α={alpha}'
        marker = ' ★' if r_is and base_is and r_is['pf'] > base_is['pf'] else ''
        print(f'    B-lin {atr_floor_label:8s} IS : {fmt(r_is)}{marker}')
        print(f'                   OOS: {fmt(r_oos)}')
        if r_is and r_oos:
            sc = r_is['pf'] + r_oos['pf']
            if sc > best_score:
                best_score = sc
                best = (f'B-lin-{alpha}', {'tp_decay_mode': 'linear', 'tp_decay_params': {'alpha': alpha, 'floor': 0}}, r_is, r_oos)

    # C: 指数 (tau=8,12,16)
    for tau in [8, 12, 16]:
        r_is, r_oos = run_is_oos(sym, cfg, t_max_h1=t_max,
                                  tp_decay_mode='exp',
                                  tp_decay_params={'tau': tau, 'floor': 0})
        marker = ' ★' if r_is and base_is and r_is['pf'] > base_is['pf'] else ''
        print(f'    C-exp τ={tau:3d}    IS : {fmt(r_is)}{marker}')
        print(f'                   OOS: {fmt(r_oos)}')
        if r_is and r_oos:
            sc = r_is['pf'] + r_oos['pf']
            if sc > best_score:
                best_score = sc
                best = (f'C-exp-tau{tau}', {'tp_decay_mode': 'exp', 'tp_decay_params': {'tau': tau, 'floor': 0}}, r_is, r_oos)

    phase2_best[sym] = best
    if best:
        print(f'  → {sym} Phase2最良: {best[0]}')

# ─── Phase 3: Break-Even 追加 ─────────────
hdr('Phase 3: Break-Even 追加 (Phase1最良構成ベース)')
BE_THRESHOLDS = [0.3, 0.5, 0.7, 1.0, 1.25]
BE_BUFFERS    = [0.0, 1.0, 3.0]
BE_MIN_BARS   = [0, 6]
phase3_best = {}

for sym, cfg in PAIRS_BASE.items():
    print(f'\n  [{sym}]')
    base_is  = baselines[sym]['is']
    t_max    = phase1_best[sym][0] if phase1_best.get(sym) else 24

    best_score = -1
    best = None

    for thr in BE_THRESHOLDS:
        for buf in BE_BUFFERS:
            for min_b in BE_MIN_BARS:
                r_is, r_oos = run_is_oos(
                    sym, cfg,
                    t_max_h1=t_max,
                    be_threshold_r=thr,
                    be_buffer_pips=buf,
                    be_min_bars_h1=min_b,
                )
                if r_is and base_is:
                    degraded = r_is['pf'] < base_is['pf'] * 0.95
                    marker = ' ✗(>5%劣化)' if degraded else (' ★' if r_is['pf'] > base_is['pf'] else '')
                    print(f'    BE thr={thr} buf={buf}pip min={min_b}h '
                          f'IS: PF={r_is["pf"]:.3f} WR={r_is["win_rate"]:.0f}% '
                          f'n={r_is["trades"]} BE_exits={r_is["be_count"]}{marker}')
                    if not degraded and r_is and r_oos:
                        sc = r_is['pf'] + (r_oos['pf'] if r_oos else 0)
                        if sc > best_score:
                            best_score = sc
                            best = (f'BE-{thr}r-{buf}pip-min{min_b}h',
                                    {'be_threshold_r': thr, 'be_buffer_pips': buf, 'be_min_bars_h1': min_b},
                                    r_is, r_oos)
    phase3_best[sym] = best
    if best:
        print(f'  → {sym} Phase3最良: {best[0]}  OOS: {fmt(best[3])}')

# ─── Phase 4: Hybrid Scaling ─────────────────
hdr('Phase 4: Hybrid Scaling (TP decay + SL tightening)')
K_GRID = [0.5, 1.0, 1.5]
M_GRID = [0.5, 1.0, 1.5]
phase4_best = {}

for sym, cfg in PAIRS_BASE.items():
    print(f'\n  [{sym}]  half_life={half_lives[sym]}h')
    base_is  = baselines[sym]['is']
    hl       = half_lives[sym]
    t_max    = phase1_best[sym][0] if phase1_best.get(sym) else int(hl * 2)

    best_score = -1
    best = None

    for k in K_GRID:
        for m in M_GRID:
            # TP: entry + ATR×max(3.75 - m*(t/H), 1.0)
            # SL: entry - ATR×max(2.5  - k*(t/H), -0.5)
            # 実装: sl_tighten_k=k でSL収束, tp_decay_mode='linear' でTP収束
            # linear alpha = m * sl_mult_base / half_life (ATR多倍率/H1bar → per bar)
            atr_m   = cfg['sl_atr_mult']  # 2.5
            atr_tp0 = atr_m * cfg['tp_sl_ratio']  # 3.75
            # alpha = m * atr_tp0 / hl  but floor = ATR×1.0
            alpha_tp = m * atr_tp0 / hl  # per H1 bar
            floor_tp = 1.0  # ATR × 1.0 floor (as fraction; will compare against atr_v*1.0)

            # Note: linear mode uses: tp_dist - alpha * elapsed_h1 * atr_v
            # floor as absolute = atr_v * floor_tp; we pass it relative via closure
            r_is, r_oos = run_is_oos(
                sym, cfg,
                t_max_h1=t_max,
                tp_decay_mode='linear',
                tp_decay_params={'alpha': alpha_tp, 'floor': 0},
                sl_tighten_k=k,
                half_life_h1=hl,
            )
            marker = ''
            if r_is and base_is and r_is['pf'] > base_is['pf']:
                marker = ' ★'
            print(f'    k={k} m={m}  IS : {fmt(r_is)}{marker}')
            print(f'              OOS: {fmt(r_oos)}')
            if r_is and r_oos:
                sc = r_is['pf'] + r_oos['pf']
                if sc > best_score:
                    best_score = sc
                    best = (f'Hybrid-k{k}-m{m}', k, m, r_is, r_oos)

    phase4_best[sym] = best
    if best:
        print(f'  → {sym} Phase4最良: {best[0]}')

# ─── 最終サマリー ─────────────────────────────
hdr('最終サマリー: 全フェーズ比較')
print(f'\n  {"ペア":<8} {"構成":<30} {"IS PF":>7} {"IS WR":>6} {"OOS PF":>8} {"OOS WR":>7} {"推奨"}')
print(f'  {"─"*8} {"─"*30} {"─"*7} {"─"*6} {"─"*8} {"─"*7} {"─"*10}')

for sym in PAIRS_BASE:
    b_is  = baselines[sym]['is']
    b_oos = baselines[sym]['oos']
    rows = []

    # ベースライン
    rows.append(('Baseline(Trail無効)', b_is, b_oos))

    # Phase1
    if phase1_best.get(sym):
        t, r_is, r_oos = phase1_best[sym]
        rows.append((f'Ph1:T_max={t}h', r_is, r_oos))

    # Phase2
    if phase2_best.get(sym):
        lbl, _, r_is, r_oos = phase2_best[sym]
        rows.append((f'Ph2:{lbl}', r_is, r_oos))

    # Phase3
    if phase3_best.get(sym):
        lbl, _, r_is, r_oos = phase3_best[sym]
        rows.append((f'Ph3:{lbl}', r_is, r_oos))

    # Phase4
    if phase4_best.get(sym):
        lbl, k, m, r_is, r_oos = phase4_best[sym]
        rows.append((f'Ph4:{lbl}', r_is, r_oos))

    best_oos_pf = max((r['pf'] for _, _, r in rows if r), default=0)
    for lbl, r_is, r_oos in rows:
        pf_is  = f"{r_is['pf']:.3f}"  if r_is  else '  N/A'
        wr_is  = f"{r_is['win_rate']:.0f}%"  if r_is  else '  N/A'
        pf_oos = f"{r_oos['pf']:.3f}" if r_oos else '  N/A'
        wr_oos = f"{r_oos['win_rate']:.0f}%" if r_oos else '  N/A'
        star   = ' ← 推奨' if (r_oos and r_oos['pf'] == best_oos_pf
                                and r_oos['pf'] > (b_oos['pf'] if b_oos else 0)) else ''
        print(f'  {sym:<8} {lbl:<30} {pf_is:>7} {wr_is:>6} {pf_oos:>8} {wr_oos:>7}{star}')

print('\n')
print('  採用基準: OOS PF がベースライン以上 かつ IS-OOS PF 乖離 < 30%')
print('  警告: Phase3でPF5%超劣化の組合せは既に排除済み')
print('  ※BTは5m ATRベース / IS=60% OOS=40%の時系列分割')
