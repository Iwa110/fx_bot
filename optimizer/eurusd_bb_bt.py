"""
eurusd_bb_bt.py - EURUSD BB戦略 改善案グリッドサーチBT
4軸: SL/RR / bb_sigma / RSI+bb_width_th / 時間帯フィルター

Period_A: 2026-02-02 ~ 2026-03-31 (前半)
Period_B: 2026-04-01 ~ 2026-05-13 (後半)
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# backtest.pyのヘルパー関数を流用
sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    load_csv, calc_bb, calc_rsi, calc_atr,
    build_htf4h_ema_lookup, build_htf_lookup,
)

# ===== パス設定 =====
DATA_DIR   = str(Path(__file__).parent.parent / 'data')
OUTPUT_CSV = str(Path(__file__).parent / 'eurusd_bb_results.csv')

# ===== 固定設定 =====
SYMBOL       = 'EURUSD'
BB_PERIOD    = 20
RSI_PERIOD   = 14
ATR_PERIOD   = 14
HTF_PERIOD   = 20
HTF_SIGMA    = 1.5
HTF_RANGE    = 1.0
COOLDOWN     = 3
PIP_UNIT     = 0.0001
SPREAD_PIPS  = 1.5
SPREAD       = SPREAD_PIPS * PIP_UNIT

PERIOD_A_END = pd.Timestamp('2026-03-31 23:59:59')
PERIOD_B_START = pd.Timestamp('2026-04-01 00:00:00')

# ===== グリッド定義 =====
SL_CANDIDATES  = [1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
RR_CANDIDATES  = [1.5, 2.0, 2.5, 3.0]
BB_SIGMA_CANDIDATES = [1.5, 2.0, 2.5]
RSI_CONFIGS = [
    {'label': 'current',  'rsi_buy_max': 45, 'rsi_sell_min': 55},
    {'label': 'strict',   'rsi_buy_max': 35, 'rsi_sell_min': 65},
    {'label': 'strictest','rsi_buy_max': 30, 'rsi_sell_min': 70},
]
BW_TH_CANDIDATES = [0.0015, 0.002, 0.0025, 0.003]
HOUR_CONFIGS = [
    {'label': 'no_filter',    'hours': []},
    {'label': 'eu_session',   'hours': list(range(8, 18))},
    {'label': 'eu_ny_overlap','hours': [13, 14, 15, 16, 17]},
    {'label': 'asia',         'hours': [21, 22, 23, 0, 1, 2, 3]},
]


def run_bt(df5m_period, df1h, sl_atr_mult, tp_sl_ratio,
           bb_sigma=1.5, rsi_buy_max=45, rsi_sell_min=55,
           bb_width_th=0.002, hour_list=None):
    """
    単一パラメータセットでBT実行。
    戻り値: {'pf', 'win_rate', 'rr_actual', 'trades'} or None
    """
    df = df5m_period.reset_index(drop=True)
    if len(df) < BB_PERIOD + 10:
        return None

    close = df['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, BB_PERIOD, bb_sigma)
    rsi  = calc_rsi(close, RSI_PERIOD)
    atr  = calc_atr(df, ATR_PERIOD)

    htf_lkp   = build_htf_lookup(df1h, HTF_PERIOD, HTF_SIGMA)
    htf4h_lkp = build_htf4h_ema_lookup(df1h)

    close_arr = close.values
    n = len(df)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    last_bar = -COOLDOWN - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * sl_atr_mult
        tp = sl * tp_sl_ratio
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        # bb_width_th フィルター
        if bb_width_th is not None:
            bw = (bb_std.iloc[i] * 2) / bb_ma.iloc[i] if bb_ma.iloc[i] != 0 else 0
            if bw < bb_width_th:
                continue

        dt = df['datetime'].iloc[i]

        # 時間帯フィルター
        if hour_list:
            if dt.hour not in hour_list:
                continue

        # HTF sigma フィルター（既存レンジフィルター）
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE:
            continue

        # エントリー方向
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < rsi_buy_max:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > rsi_sell_min:
            direction = 'sell'
        if direction is None:
            continue

        # HTF 4h EMA20 フィルター
        htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        entry    = c + SPREAD if direction == 'buy' else c - SPREAD
        tp_price = entry + tp if direction == 'buy' else entry - tp
        sl_price = entry - sl if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 300, n)):
            h = df['high'].iloc[j]
            l = df['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'; break
                if h >= tp_price:
                    hit = 'tp'; break
            else:
                if h >= sl_price:
                    hit = 'sl'; break
                if l <= tp_price:
                    hit = 'tp'; break

        if hit == 'tp':
            wins += 1
            gross_profit += tp
        elif hit == 'sl':
            losses += 1
            gross_loss += sl
        else:
            continue

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':    trades,
        'win_rate':  round(wins / trades * 100, 1),
        'pf':        round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual': round(gross_profit / wins / (gross_loss / losses), 3)
                     if wins > 0 and losses > 0 else 0.0,
    }


def split_periods(df5m):
    """5mデータをPeriod_A/Bに分割"""
    df = df5m.copy()
    df_a = df[df['datetime'] <= PERIOD_A_END].copy()
    df_b = df[df['datetime'] >= PERIOD_B_START].copy()
    return df_a, df_b


def fmt_result(res):
    if res is None:
        return 'n/a'
    return f'PF={res["pf"]:.3f} WR={res["win_rate"]:.1f}% n={res["trades"]}'


def run_axis1(df5m_a, df5m_b, df1h):
    """軸1: SL×RR グリッドサーチ"""
    print('\n' + '='*60)
    print('軸1: SL × RR グリッドサーチ (bb_sigma=1.5, rsi=45/55, bw=0.002)')
    print('='*60)
    rows = []
    best_full = None
    best_pf_full = -1

    for sl in SL_CANDIDATES:
        for rr in RR_CANDIDATES:
            res_a = run_bt(df5m_a, df1h, sl, rr)
            res_b = run_bt(df5m_b, df1h, sl, rr)
            pf_a = res_a['pf'] if res_a else 0
            pf_b = res_b['pf'] if res_b else 0
            n_a  = res_a['trades'] if res_a else 0
            n_b  = res_b['trades'] if res_b else 0

            # 両期間合算
            df5m_full = pd.concat([df5m_a, df5m_b]).drop_duplicates().sort_values('datetime')
            res_full  = run_bt(df5m_full, df1h, sl, rr)
            pf_full   = res_full['pf'] if res_full else 0

            row = {
                'axis': 1, 'sl': sl, 'rr': rr, 'bb_sigma': 1.5,
                'rsi_buy_max': 45, 'rsi_sell_min': 55, 'bw_th': 0.002,
                'hours': 'all',
                'pf_a': pf_a, 'wr_a': res_a['win_rate'] if res_a else 0, 'n_a': n_a,
                'pf_b': pf_b, 'wr_b': res_b['win_rate'] if res_b else 0, 'n_b': n_b,
                'pf_full': pf_full,
                'n_full': res_full['trades'] if res_full else 0,
            }
            rows.append(row)

            if pf_full > best_pf_full and (res_full and res_full['trades'] >= 10):
                best_pf_full = pf_full
                best_full = row

            print(f'  sl={sl:.1f} rr={rr:.1f} | A:{fmt_result(res_a)} | B:{fmt_result(res_b)}')

    if best_full:
        print(f'\n  [軸1 ベスト] sl={best_full["sl"]} rr={best_full["rr"]} PF_full={best_full["pf_full"]:.3f}')
    return rows, best_full


def run_axis2(df5m_a, df5m_b, df1h, best_sl, best_rr):
    """軸2: bb_sigma 強化"""
    print('\n' + '='*60)
    print(f'軸2: bb_sigma (固定 sl={best_sl} rr={best_rr})')
    print('='*60)
    rows = []
    best_full = None
    best_pf_full = -1

    df5m_full = pd.concat([df5m_a, df5m_b]).drop_duplicates().sort_values('datetime')
    baseline_n = run_bt(df5m_full, df1h, best_sl, best_rr, bb_sigma=1.5)

    for sigma in BB_SIGMA_CANDIDATES:
        res_a    = run_bt(df5m_a, df1h, best_sl, best_rr, bb_sigma=sigma)
        res_b    = run_bt(df5m_b, df1h, best_sl, best_rr, bb_sigma=sigma)
        res_full = run_bt(df5m_full, df1h, best_sl, best_rr, bb_sigma=sigma)

        n_full = res_full['trades'] if res_full else 0
        pf_full = res_full['pf'] if res_full else 0

        # N<10はスキップ
        if n_full < 10:
            print(f'  sigma={sigma} | n<10 スキップ')
            continue

        base_n = baseline_n['trades'] if baseline_n else 1
        reduction = round((1 - n_full / base_n) * 100, 1) if base_n > 0 else 0

        row = {
            'axis': 2, 'sl': best_sl, 'rr': best_rr, 'bb_sigma': sigma,
            'rsi_buy_max': 45, 'rsi_sell_min': 55, 'bw_th': 0.002,
            'hours': 'all',
            'pf_a': res_a['pf'] if res_a else 0,
            'wr_a': res_a['win_rate'] if res_a else 0,
            'n_a':  res_a['trades'] if res_a else 0,
            'pf_b': res_b['pf'] if res_b else 0,
            'wr_b': res_b['win_rate'] if res_b else 0,
            'n_b':  res_b['trades'] if res_b else 0,
            'pf_full': pf_full, 'n_full': n_full,
        }
        rows.append(row)

        if pf_full > best_pf_full:
            best_pf_full = pf_full
            best_full = row

        print(f'  sigma={sigma} | A:{fmt_result(res_a)} | B:{fmt_result(res_b)} | 頻度削減={reduction}%')

    if best_full:
        print(f'\n  [軸2 ベスト] sigma={best_full["bb_sigma"]} PF_full={best_full["pf_full"]:.3f}')
    return rows, best_full


def run_axis3(df5m_a, df5m_b, df1h, best_sl, best_rr):
    """軸3: RSI閾値 × bb_width_th"""
    print('\n' + '='*60)
    print(f'軸3: RSI × bb_width_th (固定 sl={best_sl} rr={best_rr} sigma=1.5)')
    print('='*60)
    rows = []
    best_full = None
    best_pf_full = -1

    df5m_full = pd.concat([df5m_a, df5m_b]).drop_duplicates().sort_values('datetime')

    for rsi_cfg in RSI_CONFIGS:
        for bw in BW_TH_CANDIDATES:
            res_a    = run_bt(df5m_a, df1h, best_sl, best_rr,
                              rsi_buy_max=rsi_cfg['rsi_buy_max'],
                              rsi_sell_min=rsi_cfg['rsi_sell_min'],
                              bb_width_th=bw)
            res_b    = run_bt(df5m_b, df1h, best_sl, best_rr,
                              rsi_buy_max=rsi_cfg['rsi_buy_max'],
                              rsi_sell_min=rsi_cfg['rsi_sell_min'],
                              bb_width_th=bw)
            res_full = run_bt(df5m_full, df1h, best_sl, best_rr,
                              rsi_buy_max=rsi_cfg['rsi_buy_max'],
                              rsi_sell_min=rsi_cfg['rsi_sell_min'],
                              bb_width_th=bw)

            n_full  = res_full['trades'] if res_full else 0
            pf_full = res_full['pf'] if res_full else 0

            row = {
                'axis': 3, 'sl': best_sl, 'rr': best_rr, 'bb_sigma': 1.5,
                'rsi_buy_max': rsi_cfg['rsi_buy_max'],
                'rsi_sell_min': rsi_cfg['rsi_sell_min'],
                'bw_th': bw, 'hours': 'all',
                'pf_a': res_a['pf'] if res_a else 0,
                'wr_a': res_a['win_rate'] if res_a else 0,
                'n_a':  res_a['trades'] if res_a else 0,
                'pf_b': res_b['pf'] if res_b else 0,
                'wr_b': res_b['win_rate'] if res_b else 0,
                'n_b':  res_b['trades'] if res_b else 0,
                'pf_full': pf_full, 'n_full': n_full,
            }
            rows.append(row)

            if pf_full > best_pf_full and n_full >= 10:
                best_pf_full = pf_full
                best_full = row

            label = f'rsi={rsi_cfg["rsi_buy_max"]}/{rsi_cfg["rsi_sell_min"]} bw={bw}'
            print(f'  {label:30s} | A:{fmt_result(res_a)} | B:{fmt_result(res_b)}')

    if best_full:
        print(f'\n  [軸3 ベスト] rsi={best_full["rsi_buy_max"]}/{best_full["rsi_sell_min"]} '
              f'bw={best_full["bw_th"]} PF_full={best_full["pf_full"]:.3f}')
    return rows, best_full


def run_axis4(df5m_a, df5m_b, df1h, best_sl, best_rr):
    """軸4: 時間帯フィルター"""
    print('\n' + '='*60)
    print(f'軸4: 時間帯フィルター (固定 sl={best_sl} rr={best_rr} sigma=1.5)')
    print('='*60)
    rows = []
    best_full = None
    best_pf_full = -1

    df5m_full = pd.concat([df5m_a, df5m_b]).drop_duplicates().sort_values('datetime')

    for hcfg in HOUR_CONFIGS:
        hours = hcfg['hours']
        res_a    = run_bt(df5m_a, df1h, best_sl, best_rr, hour_list=hours if hours else None)
        res_b    = run_bt(df5m_b, df1h, best_sl, best_rr, hour_list=hours if hours else None)
        res_full = run_bt(df5m_full, df1h, best_sl, best_rr, hour_list=hours if hours else None)

        n_full  = res_full['trades'] if res_full else 0
        pf_full = res_full['pf'] if res_full else 0

        row = {
            'axis': 4, 'sl': best_sl, 'rr': best_rr, 'bb_sigma': 1.5,
            'rsi_buy_max': 45, 'rsi_sell_min': 55, 'bw_th': 0.002,
            'hours': hcfg['label'],
            'pf_a': res_a['pf'] if res_a else 0,
            'wr_a': res_a['win_rate'] if res_a else 0,
            'n_a':  res_a['trades'] if res_a else 0,
            'pf_b': res_b['pf'] if res_b else 0,
            'wr_b': res_b['win_rate'] if res_b else 0,
            'n_b':  res_b['trades'] if res_b else 0,
            'pf_full': pf_full, 'n_full': n_full,
        }
        rows.append(row)

        if pf_full > best_pf_full and n_full >= 10:
            best_pf_full = pf_full
            best_full = row

        print(f'  {hcfg["label"]:20s} | A:{fmt_result(res_a)} | B:{fmt_result(res_b)}')

    if best_full:
        print(f'\n  [軸4 ベスト] hours={best_full["hours"]} PF_full={best_full["pf_full"]:.3f}')
    return rows, best_full


def print_summary(best1, best2, best3, best4, baseline_full):
    print('\n\n' + '='*60)
    print('=== EURUSD BB 改善案サマリー ===')
    print('='*60)

    def safe_pf(r):
        return f'PF={r["pf_full"]:.3f} n={r["n_full"]}' if r else 'n/a'

    base_pf = baseline_full['pf'] if baseline_full else '?'
    base_n  = baseline_full['trades'] if baseline_full else '?'
    base_wr = baseline_full['win_rate'] if baseline_full else '?'

    print(f'\n[現状] sl=1.2 rr=1.5 sigma=1.5 rsi=45/55 bw=0.002 hours=all')
    print(f'  全期間: PF={base_pf} WR={base_wr}% n={base_n}')

    if best1:
        print(f'\n[案1: SL/RR最適化] sl={best1["sl"]} rr={best1["rr"]}')
        print(f'  Period_A: PF={best1["pf_a"]:.3f} n={best1["n_a"]}')
        print(f'  Period_B: PF={best1["pf_b"]:.3f} n={best1["n_b"]}')
        print(f'  全期間:   {safe_pf(best1)}')
        stab = abs(best1["pf_a"] - best1["pf_b"])
        print(f'  安定性(A-B差): {stab:.3f} {"→ 安定" if stab < 0.3 else "→ 過学習注意"}')

    if best2:
        print(f'\n[案2: bb_sigma強化] sigma={best2["bb_sigma"]}')
        print(f'  Period_A: PF={best2["pf_a"]:.3f} n={best2["n_a"]}')
        print(f'  Period_B: PF={best2["pf_b"]:.3f} n={best2["n_b"]}')
        print(f'  全期間:   {safe_pf(best2)}')

    if best3:
        print(f'\n[案3: RSI厳格化] rsi={best3["rsi_buy_max"]}/{best3["rsi_sell_min"]} bw={best3["bw_th"]}')
        print(f'  Period_A: PF={best3["pf_a"]:.3f} n={best3["n_a"]}')
        print(f'  Period_B: PF={best3["pf_b"]:.3f} n={best3["n_b"]}')
        print(f'  全期間:   {safe_pf(best3)}')

    if best4:
        print(f'\n[案4: 時間帯フィルター] hours={best4["hours"]}')
        print(f'  Period_A: PF={best4["pf_a"]:.3f} n={best4["n_a"]}')
        print(f'  Period_B: PF={best4["pf_b"]:.3f} n={best4["n_b"]}')
        print(f'  全期間:   {safe_pf(best4)}')

    # 推奨案
    candidates = []
    for label, best in [('案1 SL/RR', best1), ('案2 sigma', best2),
                         ('案3 RSI', best3), ('案4 時間帯', best4)]:
        if best and best['n_full'] >= 10:
            candidates.append((label, best))

    if candidates:
        top_label, top = max(candidates, key=lambda x: x[1]['pf_full'])
        print(f'\n[推奨案] {top_label}')
        print(f'  理由: 全期間PF={top["pf_full"]:.3f}が最高、n={top["n_full"]}で統計的に最も信頼性あり')
        print(f'  注意: 5mデータが3.5ヶ月(n={base_n})と少なく、BT結果の信頼性は限定的')
    else:
        print('\n[推奨案] 全案でn<10につき推奨なし。データ蓄積後に再実施を推奨')

    print('\n[重要] 5mデータは2026-02-02〜2026-05-13の約3.5ヶ月のみ')
    print('  Period_A: 2026-02-02〜2026-03-31 / Period_B: 2026-04-01〜2026-05-13')
    print('  サンプル数が少ないため、BT結果は参考値として扱い、実稼働で検証すること')


def main():
    print('=== EURUSD BB 改善案グリッドサーチBT ===')
    print(f'データ: {DATA_DIR}')

    df5m = load_csv(SYMBOL, '5m')
    df1h = load_csv(SYMBOL, '1h')
    if df5m is None or df1h is None:
        print('[ERROR] データ読み込み失敗')
        return

    print(f'5m: {df5m["datetime"].iloc[0]} ~ {df5m["datetime"].iloc[-1]} ({len(df5m)}行)')
    print(f'1h: {df1h["datetime"].iloc[0]} ~ {df1h["datetime"].iloc[-1]} ({len(df1h)}行)')

    df5m_a, df5m_b = split_periods(df5m)
    print(f'\nPeriod_A: {len(df5m_a)}行 / Period_B: {len(df5m_b)}行')

    df5m_full = pd.concat([df5m_a, df5m_b]).drop_duplicates().sort_values('datetime')
    baseline = run_bt(df5m_full, df1h, sl_atr_mult=1.2, tp_sl_ratio=1.5)
    print(f'\n[ベースライン(現状)] {fmt_result(baseline)}')

    all_rows = []

    rows1, best1 = run_axis1(df5m_a, df5m_b, df1h)
    all_rows.extend(rows1)

    best_sl = best1['sl'] if best1 else 1.2
    best_rr = best1['rr'] if best1 else 1.5

    rows2, best2 = run_axis2(df5m_a, df5m_b, df1h, best_sl, best_rr)
    all_rows.extend(rows2)

    rows3, best3 = run_axis3(df5m_a, df5m_b, df1h, best_sl, best_rr)
    all_rows.extend(rows3)

    rows4, best4 = run_axis4(df5m_a, df5m_b, df1h, best_sl, best_rr)
    all_rows.extend(rows4)

    # CSV出力
    if all_rows:
        df_out = pd.DataFrame(all_rows)
        df_out.to_csv(OUTPUT_CSV, index=False, encoding='utf-8')
        print(f'\n[出力] {OUTPUT_CSV} ({len(df_out)}行)')

    print_summary(best1, best2, best3, best4, baseline)


if __name__ == '__main__':
    main()
