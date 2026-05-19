"""
bb_maxpos_opt_bt.py - BB戦略 max_pos=2 前提 パラメータ最適化BT

Part A: max_pos=[1,2,3] 確認（cooldown_bars=3固定・全データ）
Part B: max_pos=2 グリッドサーチ
  cooldown_bars=[1,3,5,7,10] x sl_atr_mult=[1.5,2.0,2.5,3.0] x tp_sl_ratio=[1.5,2.0,2.5]
Part C: 2枚目エントリー条件強化（sigma_boost_2nd）
  sigma_boost_2nd=[0,0.3,0.5,0.7,1.0] x 最良パラメータ固定

前提: stage2_activate=99 (trail無効 = v14/v15確定設定)
対象: GBPJPY / USDJPY （EURJPYはPF<1.0のため参考値のみ）
"""

import sys
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
# ペア別設定（bb_monitor v24 準拠）
# ──────────────────────────────────────────
PAIRS_BASE = {
    'GBPJPY': {
        'is_jpy':          True,
        'pip_unit':        0.01,
        'bb_sigma':        1.5,
        'sl_atr_mult':     2.5,
        'tp_sl_ratio':     1.5,
        'use_htf4h':       True,
        'htf_range_sigma': 10.0,
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

MAX_HOLD_BARS = 300


# ──────────────────────────────────────────
# BTエンジン（sigma_boost_2nd対応版）
# ──────────────────────────────────────────
def simulate_multipos_opt(symbol, pair_cfg, max_pos, cooldown_bars,
                          sl_atr_mult=None, tp_sl_ratio=None,
                          sigma_boost_2nd=0.0, n_bars=999999):
    """
    複数並行ポジション対応BTエンジン（最適化用）。

    sigma_boost_2nd: 2枚目以降エントリー時に要求する追加BBシグマ幅。
      0.0 = 1枚目と同じ条件
      0.5 = 1枚目の基準σ+0.5 のバンドタッチが必要
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)
    if sl_atr_mult is not None:
        cfg['sl_atr_mult'] = sl_atr_mult
    if tp_sl_ratio is not None:
        cfg['tp_sl_ratio'] = tp_sl_ratio

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    if n_bars < 999999:
        df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close     = df_5m['close']
    bb_sigma  = cfg['bb_sigma']
    bb_u, bb_l, bb_ma, _ = calc_bb(close, cfg['bb_period'], bb_sigma)
    rsi       = calc_rsi(close, cfg['rsi_period'])
    atr       = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp   = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp = build_htf4h_ema_lookup(df_1h) if cfg.get('use_htf4h') else None

    # 2枚目用 BB バンド（sigma_boost_2nd>0 の場合のみ）
    if sigma_boost_2nd > 0:
        bb_u2, bb_l2, _, _ = calc_bb(close, cfg['bb_period'], bb_sigma + sigma_boost_2nd)
    else:
        bb_u2, bb_l2 = bb_u, bb_l

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n_rows    = len(df_5m)

    open_positions = []
    last_entry_bar = -cooldown_bars - 1
    wins = losses = tp_count = sl_count = timeout_count = 0
    gross_profit = gross_loss = 0.0
    max_concurrent = 0

    for i in range(cfg['bb_period'] + 1, n_rows):
        h = df_5m['high'].iloc[i]
        l = df_5m['low'].iloc[i]

        # ── Step 1: オープンポジション決済チェック ──
        still_open = []
        for pos in open_positions:
            if i - pos['entry_bar'] >= MAX_HOLD_BARS:
                timeout_count += 1
                continue
            if pos['direction'] == 'buy':
                sl_dist = pos['entry'] - pos['sl_price']
                tp_dist = pos['tp_price'] - pos['entry']
                if l <= pos['sl_price']:
                    losses += 1; sl_count += 1; gross_loss += sl_dist
                elif h >= pos['tp_price']:
                    wins += 1; tp_count += 1; gross_profit += tp_dist
                else:
                    still_open.append(pos)
            else:
                sl_dist = pos['sl_price'] - pos['entry']
                tp_dist = pos['entry'] - pos['tp_price']
                if h >= pos['sl_price']:
                    losses += 1; sl_count += 1; gross_loss += sl_dist
                elif l <= pos['tp_price']:
                    wins += 1; tp_count += 1; gross_profit += tp_dist
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

        # 現在の枚数に応じたBBバンド選択
        n_open = len(open_positions)
        use_bb_u = bb_u if n_open == 0 else bb_u2
        use_bb_l = bb_l if n_open == 0 else bb_l2

        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= use_bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= use_bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
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
        'avg_pnl':       round((gross_profit - gross_loss) / trades, 6),
    }


# ──────────────────────────────────────────
# Part A: max_pos=[1,2,3] 確認
# ──────────────────────────────────────────
def part_a_confirm_maxpos():
    print('\n' + '=' * 80)
    print('Part A: max_pos 最適値確認（cooldown_bars=3 / sl=2.5 / rr=1.5）')
    print('=' * 80)

    results_a = {}
    for symbol, pair_cfg in PAIRS_BASE.items():
        print(f'\n  {symbol}:')
        print(f'  {"max_pos":>7} | {"PF":>6} | {"WR":>5} | {"RR":>5} | '
              f'{"TP%":>5} | {"max_con":>7} | {"N":>5}')
        print('  ' + '-' * 55)

        rows = []
        for mp in [1, 2, 3]:
            res = simulate_multipos_opt(symbol, pair_cfg, max_pos=mp, cooldown_bars=3)
            if res is None:
                print(f'  {mp:>7} | データなし')
                continue
            rows.append({'max_pos': mp, **res})
            print(f'  {mp:>7} | {res["pf"]:>6.3f} | {res["win_rate"]:>4.1f}% | '
                  f'{res["rr_actual"]:>5.3f} | {res["tp_rate"]:>4.1f}% | '
                  f'{res["max_concurrent"]:>7} | {res["trades"]:>5}')

        results_a[symbol] = rows
        if len(rows) >= 2:
            base = next(r for r in rows if r['max_pos'] == 1)
            for r in rows:
                if r['max_pos'] > 1:
                    delta = r['pf'] - base['pf']
                    mark = ' ★' if delta > 0 else ''
                    print(f'  max_pos={r["max_pos"]}: ΔPF={delta:+.3f}{mark}')

    return results_a


# ──────────────────────────────────────────
# Part B: max_pos=2 パラメータグリッドサーチ
# ──────────────────────────────────────────
COOLDOWN_GRID   = [1, 3, 5, 7, 10]
SL_GRID         = [1.5, 2.0, 2.5, 3.0]
TP_RATIO_GRID   = [1.5, 2.0, 2.5]
PART_B_MIN_N    = 30


def part_b_grid_search():
    print('\n\n' + '=' * 80)
    print('Part B: max_pos=2 グリッドサーチ')
    print(f'  cooldown_bars: {COOLDOWN_GRID}')
    print(f'  sl_atr_mult  : {SL_GRID}')
    print(f'  tp_sl_ratio  : {TP_RATIO_GRID}')
    n_runs = len(COOLDOWN_GRID) * len(SL_GRID) * len(TP_RATIO_GRID)
    print(f'  実行数/ペア  : {n_runs} runs')
    print('=' * 80)

    results_b = {}
    best_per_pair = {}

    for symbol, pair_cfg in PAIRS_BASE.items():
        print(f'\n{"="*80}')
        print(f'  {symbol} ({n_runs} runs)')
        print(f'{"="*80}')
        hdr = (f'  {"cd":>3} | {"sl":>4} | {"rr":>4} | {"PF":>6} | '
               f'{"WR":>5} | {"RR":>5} | {"TP%":>5} | {"N":>5}')
        print(hdr)
        print('  ' + '-' * 50)

        rows = []
        for cd in COOLDOWN_GRID:
            for sl in SL_GRID:
                for tp_rr in TP_RATIO_GRID:
                    res = simulate_multipos_opt(
                        symbol, pair_cfg, max_pos=2, cooldown_bars=cd,
                        sl_atr_mult=sl, tp_sl_ratio=tp_rr,
                    )
                    if res is None:
                        continue
                    row = {
                        'cooldown_bars': cd, 'sl_atr_mult': sl, 'tp_sl_ratio': tp_rr,
                        **res,
                    }
                    rows.append(row)
                    mark = ' *' if res['trades'] < PART_B_MIN_N else ''
                    print(f'  {cd:>3} | {sl:>4.1f} | {tp_rr:>4.1f} | '
                          f'{res["pf"]:>6.3f} | {res["win_rate"]:>4.1f}% | '
                          f'{res["rr_actual"]:>5.3f} | {res["tp_rate"]:>4.1f}% | '
                          f'{res["trades"]:>5}{mark}')

        results_b[symbol] = rows

        # ペア別推奨
        valid = [r for r in rows if r['trades'] >= PART_B_MIN_N]
        if valid:
            best = max(valid, key=lambda x: x['pf'])
            best_per_pair[symbol] = best
            print(f'\n  [{symbol}] 推奨: cd={best["cooldown_bars"]} sl={best["sl_atr_mult"]} '
                  f'rr={best["tp_sl_ratio"]} → '
                  f'PF={best["pf"]} WR={best["win_rate"]}% N={best["trades"]}')

    return results_b, best_per_pair


# ──────────────────────────────────────────
# Part C: sigma_boost_2nd（2枚目厳格化）
# ──────────────────────────────────────────
SIGMA_BOOST_GRID = [0.0, 0.3, 0.5, 0.7, 1.0]


def part_c_sigma_boost(best_per_pair):
    print('\n\n' + '=' * 80)
    print('Part C: 2枚目エントリー条件強化 sigma_boost_2nd')
    print(f'  sigma_boost_2nd: {SIGMA_BOOST_GRID}')
    print('  (base_sigma + boost のバンドタッチのみ2枚目エントリー可)')
    print('=' * 80)

    results_c = {}

    for symbol, pair_cfg in PAIRS_BASE.items():
        best = best_per_pair.get(symbol)
        if best is None:
            # best がない場合はデフォルト使用
            cd, sl, tp_rr = 3, 2.5, 1.5
        else:
            cd, sl, tp_rr = best['cooldown_bars'], best['sl_atr_mult'], best['tp_sl_ratio']

        base_sigma = pair_cfg['bb_sigma']
        print(f'\n  {symbol} (cd={cd}, sl={sl}, rr={tp_rr}, base_sigma={base_sigma}):')
        print(f'  {"boost":>6} | {"2nd_sigma":>9} | {"PF":>6} | {"WR":>5} | '
              f'{"RR":>5} | {"max_con":>7} | {"timeout":>7} | {"N":>5}')
        print('  ' + '-' * 65)

        rows = []
        for boost in SIGMA_BOOST_GRID:
            res = simulate_multipos_opt(
                symbol, pair_cfg, max_pos=2, cooldown_bars=cd,
                sl_atr_mult=sl, tp_sl_ratio=tp_rr,
                sigma_boost_2nd=boost,
            )
            if res is None:
                print(f'  {boost:>6.1f} | データなし')
                continue
            row = {'sigma_boost_2nd': boost, **res}
            rows.append(row)
            sigma_2nd = base_sigma + boost
            print(f'  {boost:>6.1f} | {sigma_2nd:>9.1f} | '
                  f'{res["pf"]:>6.3f} | {res["win_rate"]:>4.1f}% | '
                  f'{res["rr_actual"]:>5.3f} | {res["max_concurrent"]:>7} | '
                  f'{res["timeout_count"]:>7} | {res["trades"]:>5}')

        results_c[symbol] = rows

        # PF最良ブーストを表示
        if rows:
            best_c = max(rows, key=lambda x: x['pf'])
            base_res = next((r for r in rows if r['sigma_boost_2nd'] == 0.0), None)
            if base_res:
                delta = best_c['pf'] - base_res['pf']
                print(f'  最良: boost={best_c["sigma_boost_2nd"]} '
                      f'PF={best_c["pf"]} (ΔPF={delta:+.3f} vs boost=0)')

    return results_c


# ──────────────────────────────────────────
# 最終サマリー
# ──────────────────────────────────────────
def print_final_summary(results_a, best_per_pair, results_c):
    print('\n\n' + '=' * 80)
    print('【最終サマリー・推奨設定】')
    print('=' * 80)

    for symbol in PAIRS_BASE:
        base_a = next((r for r in results_a.get(symbol, [])
                       if r['max_pos'] == 1), None)
        best_b = best_per_pair.get(symbol)
        if base_a is None or best_b is None:
            print(f'\n  {symbol}: データ不足のためスキップ')
            continue

        # sigma_boost_2nd 最良
        c_rows = results_c.get(symbol, [])
        best_c = max(c_rows, key=lambda x: x['pf']) if c_rows else None
        c_boost_delta = (best_c['pf'] - best_b['pf']) if best_c else 0.0

        print(f'\n  {symbol}:')
        print(f'    現状 (max_pos=1, cd=3, sl=2.5, rr=1.5): '
              f'PF={base_a["pf"]} WR={base_a["win_rate"]}% N={base_a["trades"]}')
        print(f'    推奨 (max_pos=2, cd={best_b["cooldown_bars"]}, '
              f'sl={best_b["sl_atr_mult"]}, rr={best_b["tp_sl_ratio"]}): '
              f'PF={best_b["pf"]} WR={best_b["win_rate"]}% N={best_b["trades"]} '
              f'(ΔPF={best_b["pf"]-base_a["pf"]:+.3f})')
        if best_c and best_c['sigma_boost_2nd'] > 0 and c_boost_delta > 0:
            sigma_2nd = PAIRS_BASE[symbol]['bb_sigma'] + best_c['sigma_boost_2nd']
            print(f'    +sigma_boost (boost={best_c["sigma_boost_2nd"]}, '
                  f'2nd_sigma={sigma_2nd:.1f}): '
                  f'PF={best_c["pf"]} (ΔPF={best_c["pf"]-base_a["pf"]:+.3f} vs現状) '
                  f'→ {"採用推奨" if c_boost_delta >= 0.02 else "効果小・任意"}')
        elif best_c and best_c['sigma_boost_2nd'] == 0.0:
            print(f'    sigma_boost: 効果なし (boost=0.0が最良) → 2枚目同条件が最適')

    print('\n\n  bb_monitor.py 変更対象:')
    for symbol in ['GBPJPY', 'USDJPY']:
        best_b = best_per_pair.get(symbol)
        c_rows = results_c.get(symbol, [])
        best_c = max(c_rows, key=lambda x: x['pf']) if c_rows else None

        if best_b is None:
            continue
        print(f'    {symbol}:')
        print(f'      max_pos: 1 → 2')
        print(f'      sl_atr_mult: 2.5 → {best_b["sl_atr_mult"]}')
        if best_b['cooldown_bars'] != 3:
            print(f'      COOLDOWN_MINUTES: 15 → {best_b["cooldown_bars"] * 5}'
                  f'  (cooldown_bars={best_b["cooldown_bars"]} × 5min)')
        if best_c and best_c['sigma_boost_2nd'] > 0:
            print(f'      sigma_boost_2nd: {best_c["sigma_boost_2nd"]}'
                  f'  (2nd_sigma={PAIRS_BASE[symbol]["bb_sigma"] + best_c["sigma_boost_2nd"]:.1f})')


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────
if __name__ == '__main__':
    results_a = part_a_confirm_maxpos()
    results_b, best_per_pair = part_b_grid_search()
    results_c = part_c_sigma_boost(best_per_pair)
    print_final_summary(results_a, best_per_pair, results_c)
