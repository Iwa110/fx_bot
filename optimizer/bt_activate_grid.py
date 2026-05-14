"""
bt_activate_grid.py - USDJPY/GBPJPY activate x distance グリッドサーチBT
出力: optimizer/activate_grid_result.csv
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    load_csv, calc_bb, calc_rsi, calc_atr,
    build_htf_lookup, build_htf4h_ema_lookup,
    get_base_params, BB_PAIRS_CFG,
)

# ===== グリッド設定 =====
GRID_CONFIG = {
    'USDJPY': {
        'activates':   [0.70, 0.85, 0.90, 0.95],
        'distances':   [0.3, 0.5],
        'hour_filter': [21, 22, 5],
    },
    'GBPJPY': {
        'activates':   [0.70, 0.80, 0.85, 0.90],
        'distances':   [0.3, 0.5],
        'hour_filter': None,
    },
}

N_BARS = 5000


def simulate(symbol, pair_cfg, stage2_activate, stage2_distance, hour_filter, n_bars=N_BARS):
    """
    BB戦略シミュレーター。
    stage2_activate=None の場合は固定TP（Stage2なし）モード。
    戻り値: {'pf', 'wr', 'n', 'avg_win_pips', 'avg_loss_pips'} or None
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close     = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi       = calc_rsi(close, cfg['rsi_period'])
    atr       = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp   = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp = build_htf4h_ema_lookup(df_1h) if cfg.get('use_htf4h') else None

    pip_unit  = cfg['pip_unit']
    spread    = 2 * pip_unit
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    win_pnls  = []
    loss_pnls = []
    last_bar  = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        if hour_filter is not None:
            if dt.hour not in hour_filter:
                continue

        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        direction = None
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
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

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = abs(tp_price - entry)

        hit        = None
        exit_price = None

        if stage2_activate is None:
            for j in range(i + 1, min(i + 300, n)):
                h = df_5m['high'].iloc[j]
                l = df_5m['low'].iloc[j]
                if direction == 'buy':
                    if l <= sl_price:
                        hit = 'sl'; exit_price = sl_price; break
                    if h >= tp_price:
                        hit = 'tp'; exit_price = tp_price; break
                else:
                    if h >= sl_price:
                        hit = 'sl'; exit_price = sl_price; break
                    if l <= tp_price:
                        hit = 'tp'; exit_price = tp_price; break
        else:
            trail_sl  = sl_price
            activated = False
            for j in range(i + 1, min(i + 300, n)):
                h   = df_5m['high'].iloc[j]
                l   = df_5m['low'].iloc[j]
                mid = (h + l) / 2.0
                if direction == 'buy':
                    progress = (mid - entry) / tp_dist if tp_dist > 0 else 0
                    if progress >= stage2_activate:
                        activated = True
                    if activated:
                        new_trail = mid - tp_dist * stage2_distance
                        if new_trail > trail_sl:
                            trail_sl = new_trail
                    if l <= trail_sl:
                        hit = 'trail_sl' if activated else 'sl'
                        exit_price = trail_sl; break
                    if h >= tp_price:
                        hit = 'tp'; exit_price = tp_price; break
                else:
                    progress = (entry - mid) / tp_dist if tp_dist > 0 else 0
                    if progress >= stage2_activate:
                        activated = True
                    if activated:
                        new_trail = mid + tp_dist * stage2_distance
                        if new_trail < trail_sl:
                            trail_sl = new_trail
                    if h >= trail_sl:
                        hit = 'trail_sl' if activated else 'sl'
                        exit_price = trail_sl; break
                    if l <= tp_price:
                        hit = 'tp'; exit_price = tp_price; break

        if hit is None or exit_price is None:
            continue

        pnl      = exit_price - entry if direction == 'buy' else entry - exit_price
        pnl_pips = pnl / pip_unit

        if pnl > 0:
            wins         += 1
            gross_profit += pnl
            win_pnls.append(pnl_pips)
        else:
            losses      += 1
            gross_loss  += abs(pnl)
            loss_pnls.append(abs(pnl_pips))

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'pf':            round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'wr':            round(wins / trades * 100, 1),
        'n':             trades,
        'avg_win_pips':  round(float(np.mean(win_pnls)),  2) if win_pnls  else 0.0,
        'avg_loss_pips': round(float(np.mean(loss_pnls)), 2) if loss_pnls else 0.0,
    }


def main():
    rows = []

    for symbol, gcfg in GRID_CONFIG.items():
        pair_cfg    = BB_PAIRS_CFG[symbol].copy()
        hour_filter = gcfg['hour_filter']

        print(f'\n{"="*68}')
        print(f'{symbol}  (sl_atr_mult={pair_cfg["sl_atr_mult"]} '
              f'tp_sl_ratio={pair_cfg["tp_sl_ratio"]} '
              f'htf4h={pair_cfg.get("use_htf4h", False)} '
              f'hour_filter={hour_filter})')
        print(f'{"="*68}')
        hdr = (f'  {"mode":>10} | {"act":>5} | {"dist":>5} | '
               f'{"PF":>6} | {"WR":>5} | {"N":>5} | '
               f'{"avgWin":>8} | {"avgLoss":>8}')
        print(hdr)
        print('  ' + '-' * 66)

        # ベースライン（固定TP）
        res = simulate(symbol, pair_cfg,
                       stage2_activate=None, stage2_distance=None,
                       hour_filter=hour_filter)
        if res:
            rows.append({
                'pair': symbol, 'mode': 'fixed_tp',
                'activate': '', 'distance': '',
                'PF': res['pf'], 'WR': res['wr'], 'N': res['n'],
                'avgWin': res['avg_win_pips'], 'avgLoss': res['avg_loss_pips'],
            })
            print(f'  {"fixed_tp":>10} | {"--":>5} | {"--":>5} | '
                  f'{res["pf"]:>6.3f} | {res["wr"]:>4.1f}% | {res["n"]:>5} | '
                  f'{res["avg_win_pips"]:>8.2f} | {res["avg_loss_pips"]:>8.2f}')
        else:
            print(f'  fixed_tp: データなし')

        # Stage2グリッド
        for act in gcfg['activates']:
            for dist in gcfg['distances']:
                res = simulate(symbol, pair_cfg,
                               stage2_activate=act, stage2_distance=dist,
                               hour_filter=hour_filter)
                if res is None:
                    print(f'  {"stage2":>10} | {act:>5.2f} | {dist:>5.2f} | データなし')
                    continue
                rows.append({
                    'pair': symbol, 'mode': 'stage2',
                    'activate': act, 'distance': dist,
                    'PF': res['pf'], 'WR': res['wr'], 'N': res['n'],
                    'avgWin': res['avg_win_pips'], 'avgLoss': res['avg_loss_pips'],
                })
                print(f'  {"stage2":>10} | {act:>5.2f} | {dist:>5.2f} | '
                      f'{res["pf"]:>6.3f} | {res["wr"]:>4.1f}% | {res["n"]:>5} | '
                      f'{res["avg_win_pips"]:>8.2f} | {res["avg_loss_pips"]:>8.2f}')

    if not rows:
        print('[ERROR] 結果なし')
        return

    out_csv = Path(__file__).parent / 'activate_grid_result.csv'
    cols    = ['pair', 'mode', 'activate', 'distance', 'PF', 'WR', 'N', 'avgWin', 'avgLoss']
    df_out  = pd.DataFrame(rows, columns=cols)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    print('\n=== activate_grid_result.csv ===')
    print(df_out.to_string(index=False))


if __name__ == '__main__':
    main()
