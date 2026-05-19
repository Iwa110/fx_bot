"""
bb_maxpos_bt.py - BB戦略 max_pos（同一ペア複数ポジション）有効性検証BT

仮説: BBタッチは独立した平均回帰イベント。max_pos=2,3 が PF を改善するか検証。
固定条件: sl_atr_mult=2.5, tp_sl_ratio=1.5, stage2_activate=99 (trail無効、v14/v15確定設定)
グリッド: max_pos=[1,2,3] x cooldown_bars=[0,3,5] x GBPJPY/USDJPY/EURJPY
"""

import sys
import os
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    get_base_params, load_csv,
    calc_bb, calc_rsi, calc_atr,
    build_htf_lookup, build_htf4h_ema_lookup,
)

# ──────────────────────────────────────────
# ペア別設定（bb_monitor v24 / bb_rr_bt.py 準拠）
# ──────────────────────────────────────────
PAIRS_BASE = {
    'GBPJPY': {
        'is_jpy':          True,
        'pip_unit':        0.01,
        'bb_sigma':        1.5,
        'sl_atr_mult':     2.5,
        'tp_sl_ratio':     1.5,
        'use_htf4h':       True,
        'htf_range_sigma': 10.0,   # htf4h側で制御するため実質無効
        'rsi_buy_max':     45,
        'rsi_sell_min':    55,
        'spread_pips':     3,
        'bb_period':       20,
        'htf_period':      20,
        'htf_sigma':       1.5,
        'atr_period':      14,
        'filter_type':     None,
    },
    'USDJPY': {
        'is_jpy':          True,
        'pip_unit':        0.01,
        'bb_sigma':        2.0,
        'sl_atr_mult':     2.5,
        'tp_sl_ratio':     1.5,
        'use_htf4h':       True,
        'htf_range_sigma': 10.0,
        'rsi_buy_max':     45,
        'rsi_sell_min':    55,
        'spread_pips':     2,
        'bb_period':       20,
        'htf_period':      20,
        'htf_sigma':       1.5,
        'atr_period':      14,
        'filter_type':     None,
    },
    'EURJPY': {
        'is_jpy':          True,
        'pip_unit':        0.01,
        'bb_sigma':        1.5,
        'sl_atr_mult':     2.5,
        'tp_sl_ratio':     1.5,
        'htf_range_sigma': 1.0,
        'rsi_buy_max':     45,
        'rsi_sell_min':    55,
        'spread_pips':     3,
        'bb_period':       20,
        'htf_period':      20,
        'htf_sigma':       1.5,
        'atr_period':      14,
        'filter_type':     None,
    },
}

# trail無効（v14/v15確定設定）
NO_TRAIL_ACTIVATE = 99.0

# グリッドサーチパラメータ
MAX_POS_CANDIDATES    = [1, 2, 3]
COOLDOWN_CANDIDATES   = [0, 3, 5]
MAX_HOLD_BARS         = 300   # タイムアウト上限（バー数）


def simulate_multipos(symbol, pair_cfg, max_pos, cooldown_bars, n_bars=999999):
    """
    複数並行ポジション対応BTエンジン。
    max_pos=1, cooldown_bars=3 は simulate_with_stage2(stage2_activate=99) と等価。

    戻り値: dict or None
      trades, win_rate, pf, rr_actual, tp_rate, sl_rate,
      max_concurrent, timeout_count, avg_pnl
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    if n_bars < 999999:
        df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close    = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi      = calc_rsi(close, cfg['rsi_period'])
    atr      = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp  = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp = build_htf4h_ema_lookup(df_1h) if cfg.get('use_htf4h') else None

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n_rows    = len(df_5m)

    open_positions  = []   # list of position dicts
    last_entry_bar  = -cooldown_bars - 1
    wins = losses = tp_count = sl_count = timeout_count = 0
    gross_profit = gross_loss = 0.0
    max_concurrent = 0

    for i in range(cfg['bb_period'] + 1, n_rows):
        h = df_5m['high'].iloc[i]
        l = df_5m['low'].iloc[i]

        # ── Step 1: 全オープンポジションの決済チェック ──
        still_open = []
        for pos in open_positions:
            if i - pos['entry_bar'] >= MAX_HOLD_BARS:
                timeout_count += 1
                continue

            if pos['direction'] == 'buy':
                sl_dist = pos['entry'] - pos['sl_price']
                tp_dist = pos['tp_price'] - pos['entry']
                if l <= pos['sl_price']:
                    losses += 1
                    sl_count += 1
                    gross_loss += sl_dist
                elif h >= pos['tp_price']:
                    wins += 1
                    tp_count += 1
                    gross_profit += tp_dist
                else:
                    still_open.append(pos)
            else:  # sell
                sl_dist = pos['sl_price'] - pos['entry']
                tp_dist = pos['entry'] - pos['tp_price']
                if h >= pos['sl_price']:
                    losses += 1
                    sl_count += 1
                    gross_loss += sl_dist
                elif l <= pos['tp_price']:
                    wins += 1
                    tp_count += 1
                    gross_profit += tp_dist
                else:
                    still_open.append(pos)

        open_positions = still_open

        # ── Step 2: 新規エントリー判定 ──
        if len(open_positions) >= max_pos:
            continue
        if cooldown_bars > 0 and i - last_entry_bar < cooldown_bars:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # HTF sigma フィルター
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # エントリー方向（BBタッチ + RSI）
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        # HTF 4h EMA20 フィルター
        if htf4h_lkp is not None:
            htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if htf4h_idx < 0:
                continue
            htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
            if direction == 'buy'  and htf4h_sig != 1:
                continue
            if direction == 'sell' and htf4h_sig != -1:
                continue

        # ポジション追加
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl

        open_positions.append({
            'entry':     entry,
            'tp_price':  tp_price,
            'sl_price':  sl_price,
            'direction': direction,
            'entry_bar': i,
        })
        last_entry_bar = i
        if len(open_positions) > max_concurrent:
            max_concurrent = len(open_positions)

    trades = wins + losses
    if trades == 0:
        return None

    avg_pnl = (gross_profit - gross_loss) / trades if trades > 0 else 0.0

    return {
        'trades':        trades,
        'win_rate':      round(wins / trades * 100, 1),
        'pf':            round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual':     round(gross_profit / wins / (gross_loss / losses), 3)
                         if wins > 0 and losses > 0 else 0.0,
        'tp_rate':       round(tp_count / trades * 100, 1),
        'sl_rate':       round(sl_count / trades * 100, 1),
        'max_concurrent': max_concurrent,
        'timeout_count': timeout_count,
        'avg_pnl':       round(avg_pnl, 6),
    }


def run_maxpos_bt():
    print('=' * 85)
    print('BB戦略 max_pos 有効性検証BT')
    print('固定条件: sl_atr_mult=2.5, tp_sl_ratio=1.5, trail無効 (stage2_activate=99)')
    print(f'グリッド: max_pos={MAX_POS_CANDIDATES} x cooldown_bars={COOLDOWN_CANDIDATES}')
    print('=' * 85)

    all_rows = []

    for symbol, pair_cfg in PAIRS_BASE.items():
        print(f'\n{"="*85}')
        print(f'  {symbol}')
        print(f'{"="*85}')
        hdr = (f'  {"max_pos":>7} | {"cooldown":>8} | {"PF":>6} | {"WR":>5} | '
               f'{"RR":>5} | {"TP%":>5} | {"SL%":>5} | {"max_con":>7} | '
               f'{"timeout":>7} | {"N":>5}')
        print(hdr)
        print('  ' + '-' * 80)

        pair_rows = []
        for mp in MAX_POS_CANDIDATES:
            for cd in COOLDOWN_CANDIDATES:
                res = simulate_multipos(symbol, pair_cfg, max_pos=mp, cooldown_bars=cd)
                if res is None:
                    print(f'  {mp:>7} | {cd:>8} | データなし')
                    continue

                row = {
                    'symbol':        symbol,
                    'max_pos':       mp,
                    'cooldown_bars': cd,
                    **res,
                }
                all_rows.append(row)
                pair_rows.append(row)

                print(f'  {mp:>7} | {cd:>8} | '
                      f'{res["pf"]:>6.3f} | '
                      f'{res["win_rate"]:>4.1f}% | '
                      f'{res["rr_actual"]:>5.3f} | '
                      f'{res["tp_rate"]:>4.1f}% | '
                      f'{res["sl_rate"]:>4.1f}% | '
                      f'{res["max_concurrent"]:>7} | '
                      f'{res["timeout_count"]:>7} | '
                      f'{res["trades"]:>5}')

        # ペア別サマリー
        base = next((r for r in pair_rows
                     if r['max_pos'] == 1 and r['cooldown_bars'] == 3), None)
        if base:
            print(f'\n  [{symbol}] ベースライン (max_pos=1, cd=3): '
                  f'PF={base["pf"]} WR={base["win_rate"]}% N={base["trades"]}')
            better = [r for r in pair_rows
                      if r['max_pos'] > 1 and r['pf'] > base['pf'] and r['trades'] >= 20]
            if better:
                best = max(better, key=lambda x: x['pf'])
                dpf = best['pf'] - base['pf']
                print(f'  [{symbol}] 改善案: max_pos={best["max_pos"]} cd={best["cooldown_bars"]} '
                      f'→ PF={best["pf"]} ({dpf:+.3f}) N={best["trades"]}')
            else:
                print(f'  [{symbol}] max_pos>1 での PF 改善なし → max_pos=1 維持推奨')

    # ──────────────────────────────────────────
    # 全体サマリー
    # ──────────────────────────────────────────
    print('\n\n' + '=' * 85)
    print('【最終サマリー: ベースライン (max_pos=1, cd=3) vs max_pos拡大案】')
    print('=' * 85)

    for symbol in PAIRS_BASE:
        base = next((r for r in all_rows
                     if r['symbol'] == symbol
                     and r['max_pos'] == 1 and r['cooldown_bars'] == 3), None)
        if base is None:
            continue

        print(f'\n  {symbol} (base: PF={base["pf"]} WR={base["win_rate"]}% N={base["trades"]})')
        print(f'  {"max_pos":>7} | {"cd":>3} | {"PF":>6} | {"ΔPF":>6} | '
              f'{"WR":>5} | {"ΔWR":>5} | {"N":>5} | {"ΔN":>5}')
        print('  ' + '-' * 57)

        cands = [r for r in all_rows
                 if r['symbol'] == symbol and r['max_pos'] > 1]
        cands_sorted = sorted(cands, key=lambda x: x['pf'], reverse=True)
        for r in cands_sorted:
            dpf = r['pf'] - base['pf']
            dwr = r['win_rate'] - base['win_rate']
            dn  = r['trades'] - base['trades']
            mark = ' ★' if dpf > 0 else ''
            print(f'  {r["max_pos"]:>7} | {r["cooldown_bars"]:>3} | '
                  f'{r["pf"]:>6.3f} | {dpf:>+6.3f} | '
                  f'{r["win_rate"]:>4.1f}% | {dwr:>+4.1f}% | '
                  f'{r["trades"]:>5} | {dn:>+5}{mark}')

    print('\n\n' + '=' * 85)
    print('【推奨設定】')
    print('=' * 85)
    for symbol in PAIRS_BASE:
        base = next((r for r in all_rows
                     if r['symbol'] == symbol
                     and r['max_pos'] == 1 and r['cooldown_bars'] == 3), None)
        if base is None:
            continue

        # N>=20 かつ PF改善 かつ 全期間で優位な組み合わせを選定
        candidates = [r for r in all_rows
                      if r['symbol'] == symbol
                      and r['trades'] >= 20
                      and r['pf'] > base['pf']]
        if candidates:
            best = max(candidates, key=lambda x: x['pf'])
            dpf = best['pf'] - base['pf']
            print(f'  {symbol}: max_pos={best["max_pos"]} cooldown_bars={best["cooldown_bars"]} '
                  f'→ PF={best["pf"]} ({dpf:+.3f}) WR={best["win_rate"]}% N={best["trades"]} '
                  f'[max_pos=1から変更推奨]')
        else:
            print(f'  {symbol}: max_pos=1 cooldown_bars=3 を維持 '
                  f'(PF={base["pf"]} WR={base["win_rate"]}% N={base["trades"]})')


if __name__ == '__main__':
    run_maxpos_bt()
