"""
time_filter_bt.py
BB戦略 時間外フィルター有効性検証

4条件 x 4ペア のバックテスト:
  A: htf4h=OFF, time_filter=OFF  ... ベースライン（全エントリー）
  B: htf4h=OFF, time_filter=ON   ... 時間フィルターのみ
  C: htf4h=ON,  time_filter=OFF  ... 4hフィルターのみ（仮説）
  D: htf4h=ON,  time_filter=ON   ... 現行相当

評価基準:
  条件C vs D の PF差 < 0.05 → 時間フィルターは不要と判断
"""
import re
import ast
import importlib.util
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

VPS_DIR  = Path(r'C:\Users\Administrator\fx_bot\vps')
OPT_DIR  = Path(r'C:\Users\Administrator\fx_bot\optimizer')
DATA_DIR = Path(r'C:\Users\Administrator\fx_bot\data')
OUT_CSV  = OPT_DIR / 'time_filter_bt_result.csv'

PAIRS = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']

CONDITIONS = [
    {'label': 'A', 'htf4h': False, 'time_filter': False, 'desc': 'baseline'},
    {'label': 'B', 'htf4h': False, 'time_filter': True,  'desc': 'time_only'},
    {'label': 'C', 'htf4h': True,  'time_filter': False, 'desc': 'htf4h_only'},
    {'label': 'D', 'htf4h': True,  'time_filter': True,  'desc': 'current'},
]

STAGE2_ACTIVATE = 0.70

# EURUSD=0.1 はCLAUDE.mdの実稼働設定に合わせる（backtest.pyの0.3より優先）
STAGE2_DISTANCE = {
    'GBPJPY': 0.3,
    'USDJPY': 0.3,
    'EURUSD': 0.1,
    'GBPUSD': 0.3,
}


def log_print(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}')


def read_allowed_hours():
    """bb_monitor.pyからALLOWED_HOURS_UTCを動的に読み込む（ハードコード禁止）"""
    bb_path = VPS_DIR / 'bb_monitor.py'
    if not bb_path.exists():
        raise FileNotFoundError(f'bb_monitor.py not found: {bb_path}')
    src = bb_path.read_text(encoding='utf-8')
    m = re.search(r'ALLOWED_HOURS_UTC\s*=\s*(\{[^}]*\})', src, re.DOTALL)
    if not m:
        raise ValueError('ALLOWED_HOURS_UTC not found in bb_monitor.py')
    return ast.literal_eval(m.group(1))


def load_bt_module():
    """backtest.pyをモジュールとしてロード（MT5不要・純粋計算関数のみ使用）"""
    bt_path = OPT_DIR / 'backtest.py'
    if not bt_path.exists():
        raise FileNotFoundError(f'backtest.py not found: {bt_path}')
    spec = importlib.util.spec_from_file_location('backtest', bt_path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    return bt


def simulate_condition(symbol, pair_cfg, allowed_hours, use_htf4h, use_time_filter, bt):
    """
    1条件のシミュレーション。
    backtest.pyのsimulate_with_filters()を参考に再実装。
    max_ddの計算を追加、stage2_distanceをCLAUDE.mdの実稼働値に修正。
    戻り値: dict(pf, win_rate, n_trades, avg_rr, max_dd) or None
    """
    stage2_dist = STAGE2_DISTANCE.get(symbol, 0.3)

    cfg = bt.get_base_params()
    cfg.update(pair_cfg)

    df_5m = bt.load_csv(symbol, '5m')
    df_1h = bt.load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        log_print(f'[WARN] {symbol}: CSVなし')
        return None

    close   = df_5m['close']
    bb_u, bb_l, _, _ = bt.calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = bt.calc_rsi(close, cfg['rsi_period'])
    atr     = bt.calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = bt.build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    htf4h_lkp = bt.build_htf4h_ema_lookup(df_1h) if use_htf4h else None

    # 時間フィルター: None(制限なし)またはlist(空でない)のみ有効
    hour_set = None
    if use_time_filter and isinstance(allowed_hours, list) and len(allowed_hours) > 0:
        hour_set = set(allowed_hours)

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    win_pnl_sum = loss_pnl_sum = 0.0
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    last_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # 時間フィルター
        if hour_set is not None and dt.hour not in hour_set:
            continue

        # 1h HTFレンジフィルター
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # BBエントリー + RSIフィルター
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

        # 4h EMA20フィルター
        if htf4h_lkp is not None:
            idx4h = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if idx4h < 0:
                continue
            sig4h = htf4h_lkp.iloc[idx4h]
            if direction == 'buy'  and sig4h != 1:
                continue
            if direction == 'sell' and sig4h != -1:
                continue

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = abs(tp_price - entry)

        trail_sl  = sl_price
        activated = False
        hit       = None
        exit_price = None

        for j in range(i + 1, min(i + 300, n)):
            h   = df_5m['high'].iloc[j]
            l   = df_5m['low'].iloc[j]
            mid = (h + l) / 2.0

            if direction == 'buy':
                progress = (mid - entry) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid - tp_dist * stage2_dist
                    if new_trail > trail_sl:
                        trail_sl = new_trail
                if l <= trail_sl:
                    hit = 'trail_sl' if activated else 'sl'
                    exit_price = trail_sl
                    break
                if h >= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break
            else:
                progress = (entry - mid) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid + tp_dist * stage2_dist
                    if new_trail < trail_sl:
                        trail_sl = new_trail
                if h >= trail_sl:
                    hit = 'trail_sl' if activated else 'sl'
                    exit_price = trail_sl
                    break
                if l <= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break

        if hit is None or exit_price is None:
            continue

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price
        cum_pnl += pnl

        if pnl > 0:
            wins += 1
            gross_profit += pnl
            win_pnl_sum += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)
            loss_pnl_sum += abs(pnl)

        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd:
            max_dd = dd

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    avg_rr = round(
        (win_pnl_sum / wins) / (loss_pnl_sum / losses), 3
    ) if wins > 0 and losses > 0 else 0.0

    return {
        'pf':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'win_rate': round(wins / trades * 100, 1),
        'n_trades': trades,
        'avg_rr':   avg_rr,
        'max_dd':   round(max_dd, 5),
    }


def print_summary(rows):
    log_print('\n' + '=' * 60)
    log_print('=== サマリー ===')

    # [1] 条件C vs D: 4hフィルター有効時の時間フィルター追加効果
    log_print('\n[1] 条件C vs D (4hフィルター有効 + 時間フィルター追加効果):')
    for pair in PAIRS:
        c_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'C'), None)
        d_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'D'), None)
        if c_row is None or d_row is None:
            continue
        diff = d_row['pf'] - c_row['pf']
        verdict = '時間フィルター不要（差<0.05）' if abs(diff) < 0.05 else '時間フィルター有効'
        log_print(f'  条件C vs D: {pair} PF差={diff:+.3f} ({verdict})')

    # [2] 条件A vs B: 時間フィルター単体効果
    log_print('\n[2] 条件A vs B (時間フィルター単体効果):')
    for pair in PAIRS:
        a_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'A'), None)
        b_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'B'), None)
        if a_row is None or b_row is None:
            continue
        diff = b_row['pf'] - a_row['pf']
        n_loss = a_row['n_trades'] - b_row['n_trades']
        log_print(f'  条件A vs B: {pair} PF差={diff:+.3f} 機会損失={n_loss}件 (A={a_row["n_trades"]}, B={b_row["n_trades"]})')

    # [3] 機会損失分析
    log_print('\n[3] 機会損失分析 (条件A - 条件D):')
    for pair in PAIRS:
        a_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'A'), None)
        c_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'C'), None)
        d_row = next((r for r in rows if r['pair'] == pair and r['condition'] == 'D'), None)
        if a_row is None or d_row is None:
            continue
        loss_total = a_row['n_trades'] - d_row['n_trades']
        if c_row is not None and loss_total > 0:
            loss_by_4h = a_row['n_trades'] - c_row['n_trades']
            pct = loss_by_4h / loss_total * 100
            log_print(f'  {pair}: 機会損失合計={loss_total}件 | 4hフィルター起因={loss_by_4h}件({pct:.1f}%) | 時間フィルター起因={loss_total - loss_by_4h}件')
        else:
            log_print(f'  {pair}: 機会損失合計={loss_total}件')

    # [4] 全結果テーブル
    log_print('\n[4] 全結果テーブル:')
    log_print(f'  {"pair":>6} {"cond":>5} {"PF":>7} {"WR%":>6} {"N":>5} {"RR":>6} {"maxDD":>10}')
    log_print(f'  {"-"*50}')
    for r in rows:
        log_print(f'  {r["pair"]:>6} {r["condition"]:>5} {r["pf"]:>7.3f} {r["win_rate"]:>6.1f} {r["n_trades"]:>5} {r["avg_rr"]:>6.3f} {r["max_dd"]:>10.5f}')


def main():
    log_print('=== BB戦略 時間外フィルター有効性検証 ===')
    log_print(f'対象ペア: {PAIRS}')
    log_print(f'条件数: {len(CONDITIONS)}')

    log_print('\nALLOWED_HOURS_UTC 読み込み中...')
    allowed_hours_map = read_allowed_hours()
    log_print(f'  {allowed_hours_map}')

    log_print('\nbacktest.py 読み込み中...')
    bt = load_bt_module()
    log_print('  OK')

    rows = []

    for pair in PAIRS:
        pair_cfg = bt.BB_PAIRS_CFG.get(pair)
        if pair_cfg is None:
            log_print(f'[WARN] {pair}: BB_PAIRS_CFGに未定義 → スキップ')
            continue

        allowed = allowed_hours_map.get(pair)
        log_print(f'\n--- {pair} | ALLOWED_HOURS={allowed} ---')

        for cond in CONDITIONS:
            label = cond['label']
            desc  = cond['desc']
            log_print(f'  条件{label} ({desc}) htf4h={cond["htf4h"]} time={cond["time_filter"]} ...')

            res = simulate_condition(
                pair, pair_cfg, allowed,
                cond['htf4h'], cond['time_filter'], bt
            )

            if res is None:
                log_print(f'  条件{label}: 結果なし（トレード0）')
                continue

            row = {
                'pair':      pair,
                'condition': label,
                'pf':        res['pf'],
                'win_rate':  res['win_rate'],
                'n_trades':  res['n_trades'],
                'avg_rr':    res['avg_rr'],
                'max_dd':    res['max_dd'],
            }
            rows.append(row)
            log_print(
                f'  条件{label}: PF={res["pf"]:.3f} WR={res["win_rate"]}% '
                f'N={res["n_trades"]} RR={res["avg_rr"]:.3f} DD={res["max_dd"]:.5f}'
            )

    if not rows:
        log_print('[ERROR] 全結果なし。CSVデータを確認してください。')
        return

    df_out = pd.DataFrame(rows, columns=['pair', 'condition', 'pf', 'win_rate', 'n_trades', 'avg_rr', 'max_dd'])
    df_out.to_csv(OUT_CSV, index=False, encoding='utf-8')
    log_print(f'\n出力: {OUT_CSV}')

    print_summary(rows)
    log_print('\n完了')


if __name__ == '__main__':
    main()
