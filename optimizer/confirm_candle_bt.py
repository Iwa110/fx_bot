"""
confirm_candle_bt.py - BB戦略 確認足フィルター バックテスト

現行エントリー（BBタッチ足で即エントリー）に「確認足」を追加した場合の
PF・WR・N数への影響を検証する。

パターン:
  baseline : 現行（bar[i]でBBタッチ即エントリー）
  A        : bar[i-1]タッチ → bar[i]でBB内側に戻ったことを確認してエントリー
  B        : bar[i-1]タッチ → bar[i]でも同方向継続（CONF_TH_B範囲内）を確認

対象ペア: GBPJPY, USDJPY
固定条件: HTF 4h EMA20フィルター有効 / sl_atr_mult=3.0 / tp_sl_ratio=1.5 / 全期間

採用基準（run後コメント）:
  - N数が baseline比 -30%以内 → 採用候補
  - PF改善幅 +0.1以上 → v21への組み込みを検討
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# backtest.pyから共通関数・設定をインポート
sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    load_csv, calc_bb, calc_atr, calc_rsi,
    build_htf_lookup, build_htf4h_ema_lookup,
    get_base_params, BB_PAIRS_CFG,
)

# ===== 設定 =====
CC_BT_PAIRS = ['GBPJPY', 'USDJPY']

# パターンBのバンド近接しきい値（sell: close >= upper * CONF_TH_B）
CONF_TH_B = 0.998

STAGE2_ACTIVATE = 0.70
STAGE2_DIST_MAP = {'GBPJPY': 0.3, 'USDJPY': 0.3}

# ===== エントリー条件（パターン別） =====
def entry_condition(i, close_arr, bb_u_arr, bb_l_arr, rsi_arr,
                    pattern, rsi_buy_max, rsi_sell_min, conf_th=CONF_TH_B):
    """
    Returns 'buy', 'sell', or None.
    i    : 現在のバーインデックス（bar[i]のcloseでエントリー）
    pattern: 'baseline' / 'A' / 'B'

    baseline: bar[i]がBBタッチ
    A       : bar[i-1]がBBタッチ、bar[i]でBB内側に戻ったことを確認
    B       : bar[i-1]がBBタッチ、bar[i]でも同方向継続（band ±conf_th以内）
              sell: close[-2] >= upper[-2] * conf_th (0.2%以内)
              buy : close[-2] <= lower[-2] * (2 - conf_th) (0.2%以内)
    """
    if i < 2:
        return None

    c     = close_arr[i]
    rsi_v = rsi_arr[i]
    if np.isnan(rsi_v) or np.isnan(c):
        return None

    if pattern == 'baseline':
        if c <= bb_l_arr[i] and rsi_v < rsi_buy_max:
            return 'buy'
        if c >= bb_u_arr[i] and rsi_v > rsi_sell_min:
            return 'sell'

    elif pattern == 'A':
        # bar[i-1]タッチ → bar[i]でBB内側に戻ったことを確認
        c_prev  = close_arr[i - 1]
        bu_prev = bb_u_arr[i - 1]
        bl_prev = bb_l_arr[i - 1]
        if np.isnan(c_prev) or np.isnan(bu_prev) or np.isnan(bl_prev):
            return None
        if c_prev <= bl_prev and c > bb_l_arr[i] and rsi_v < rsi_buy_max:
            return 'buy'
        if c_prev >= bu_prev and c < bb_u_arr[i] and rsi_v > rsi_sell_min:
            return 'sell'

    elif pattern == 'B':
        # bar[i-1]タッチ → bar[i]でも同方向継続（conf_th範囲内）
        c_prev  = close_arr[i - 1]
        bu_prev = bb_u_arr[i - 1]
        bl_prev = bb_l_arr[i - 1]
        if np.isnan(c_prev) or np.isnan(bu_prev) or np.isnan(bl_prev):
            return None
        # buy: 前足が下BBタッチ、現足も下BBの0.2%以内（lower * 1.002以下）
        if c_prev <= bl_prev and c <= bb_l_arr[i] * (2 - conf_th) and rsi_v < rsi_buy_max:
            return 'buy'
        # sell: 前足が上BBタッチ、現足も上BBの0.2%以内（upper * 0.998以上）
        if c_prev >= bu_prev and c >= bb_u_arr[i] * conf_th and rsi_v > rsi_sell_min:
            return 'sell'

    return None


# ===== シミュレーター =====
def simulate_confirm_candle(symbol, pair_cfg, pattern, conf_th=CONF_TH_B):
    """
    確認足フィルターBT（HTF 4h EMA20フィルター固定有効、全期間使用）。
    戻り値: dict or None
    """
    stage2_distance = STAGE2_DIST_MAP.get(symbol, 0.3)

    cfg = get_base_params()
    cfg.update(pair_cfg)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None:
        print(f'[SKIP] {symbol}: 5m CSVなし')
        return None
    if df_1h is None:
        print(f'[SKIP] {symbol}: 1h CSVなし')
        return None

    # 全期間使用（n_bars制限なし）
    df_5m = df_5m.reset_index(drop=True)

    close             = df_5m['close']
    bb_u, bb_l, _, _  = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi               = calc_rsi(close, cfg['rsi_period'])
    atr               = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp           = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])
    htf4h_lkp         = build_htf4h_ema_lookup(df_1h)

    spread       = 2 * cfg['pip_unit']
    pip_unit     = cfg['pip_unit']
    close_arr    = close.values
    bb_u_arr     = bb_u.values
    bb_l_arr     = bb_l.values
    rsi_arr      = rsi.values
    n            = len(df_5m)
    rsi_buy_max  = cfg['rsi_buy_max']
    rsi_sell_min = cfg['rsi_sell_min']

    wins = losses = tp_count = trail_count = sl_count = 0
    gross_profit = gross_loss = 0.0
    win_pips     = []
    loss_pips    = []
    trade_pips   = []
    last_bar     = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 2, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # HTF sigmaフィルター（既存）
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # エントリー条件（パターン別）
        direction = entry_condition(
            i, close_arr, bb_u_arr, bb_l_arr, rsi_arr,
            pattern, rsi_buy_max, rsi_sell_min, conf_th,
        )
        if direction is None:
            continue

        # HTF 4h EMA20フィルター（固定有効）
        htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        # 決済シミュレーション（Stage2トレーリングSL）
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = abs(tp_price - entry)

        trail_sl  = sl_price
        activated = False
        hit        = None
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
                    new_trail = mid - tp_dist * stage2_distance
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
                    new_trail = mid + tp_dist * stage2_distance
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

        pnl      = exit_price - entry if direction == 'buy' else entry - exit_price
        pnl_pip  = pnl / pip_unit

        if pnl > 0:
            wins += 1
            gross_profit += pnl
            win_pips.append(pnl_pip)
        else:
            losses += 1
            gross_loss += abs(pnl)
            loss_pips.append(abs(pnl_pip))

        if hit == 'tp':
            tp_count += 1
        elif hit == 'trail_sl':
            trail_count += 1
        elif hit == 'sl':
            sl_count += 1

        trade_pips.append(pnl_pip)
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    # 最大ドローダウン（累積pips基準 / peakからの乖離率）
    cumulative = np.cumsum(trade_pips)
    peak       = np.maximum.accumulate(cumulative)
    dd_series  = peak - cumulative
    max_dd_pip = float(np.max(dd_series)) if len(dd_series) > 0 else 0.0
    peak_max   = float(np.max(peak))      if len(peak) > 0 else 1.0
    max_dd_pct = round(max_dd_pip / peak_max * 100, 2) if peak_max > 0 else 0.0

    # Sharpe比（per-trade pips: mean/std）
    arr    = np.array(trade_pips)
    sharpe = round(float(np.mean(arr) / np.std(arr)), 3) if np.std(arr) > 0 else 0.0

    return {
        'total_trades':     trades,
        'win_rate':         round(wins / trades * 100, 1),
        'profit_factor':    round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'avg_win_pips':     round(float(np.mean(win_pips)),  2) if win_pips  else 0.0,
        'avg_loss_pips':    round(float(np.mean(loss_pips)), 2) if loss_pips else 0.0,
        'max_drawdown_pct': max_dd_pct,
        'sharpe_ratio':     sharpe,
        'tp_count':         tp_count,
        'trail_count':      trail_count,
        'sl_count':         sl_count,
    }


# ===== メインBT実行 =====
def run_confirm_candle_bt():
    """
    確認足フィルターBT。
    3パターン(baseline/A/B) x 2ペア(GBPJPY/USDJPY) = 6ケース実行。
    出力: optimizer/confirm_candle_bt_result.csv
    """
    print('=== BB戦略 確認足フィルターBT ===')
    print(f'対象ペア  : {CC_BT_PAIRS}')
    print(f'パターン  : baseline / A(反転確認) / B(継続確認 conf_th={CONF_TH_B})')
    print(f'HTF 4h    : EMA20フィルター固定有効')
    print(f'Stage2    : activate={STAGE2_ACTIVATE} / distance={STAGE2_DIST_MAP}')
    print()

    PATTERNS = ['baseline', 'A', 'B']
    rows     = []

    for symbol in CC_BT_PAIRS:
        pair_cfg = BB_PAIRS_CFG.get(symbol)
        if pair_cfg is None:
            print(f'[WARN] {symbol} not in BB_PAIRS_CFG, skip')
            continue

        print(f'--- {symbol} ---')
        print(f'  {"pattern":>10} | {"PF":>6} | {"WR":>6} | {"N":>5} | '
              f'{"avg_win":>8} | {"avg_loss":>9} | {"max_DD%":>7} | {"sharpe":>7} | '
              f'TP/Trail/SL')
        print(f'  {"-"*95}')

        base_n  = None
        base_pf = None

        for pattern in PATTERNS:
            res = simulate_confirm_candle(symbol, pair_cfg, pattern)
            if res is None:
                print(f'  {pattern:>10} | データなし / 取引なし')
                continue

            n = res['total_trades']
            if pattern == 'baseline':
                base_n  = n
                base_pf = res['profit_factor']

            n_ratio = n / base_n  if (base_n and base_n > 0) else None
            dpf     = res['profit_factor'] - base_pf if base_pf is not None else None

            # 採用候補判定
            n_ok  = n_ratio is not None and n_ratio >= 0.70
            pf_ok = dpf     is not None and dpf     >= 0.10
            marks = ''
            if pattern != 'baseline':
                marks += ' N-OK' if n_ok else ' N-NG'
                if pf_ok:
                    marks += ' ★v21候補'

            print(f'  {pattern:>10} | '
                  f'{res["profit_factor"]:>6.3f} | '
                  f'{res["win_rate"]:>5.1f}% | '
                  f'{n:>5} | '
                  f'{res["avg_win_pips"]:>7.1f}p | '
                  f'{res["avg_loss_pips"]:>8.1f}p | '
                  f'{res["max_drawdown_pct"]:>6.1f}% | '
                  f'{res["sharpe_ratio"]:>7.3f} | '
                  f'{res["tp_count"]}/{res["trail_count"]}/{res["sl_count"]}'
                  f'{marks}')

            entry_type_map = {
                'baseline': 'touch_only',
                'A':        'reversal_confirm',
                'B':        f'continuation_confirm(th={CONF_TH_B})',
            }
            rows.append({
                'pair':             symbol,
                'pattern':          pattern,
                'entry_type':       entry_type_map[pattern],
                'total_trades':     n,
                'win_rate':         res['win_rate'],
                'profit_factor':    res['profit_factor'],
                'avg_win_pips':     res['avg_win_pips'],
                'avg_loss_pips':    res['avg_loss_pips'],
                'max_drawdown_pct': res['max_drawdown_pct'],
                'sharpe_ratio':     res['sharpe_ratio'],
                'tp_count':         res['tp_count'],
                'trail_count':      res['trail_count'],
                'sl_count':         res['sl_count'],
                'n_vs_baseline':    round(n_ratio, 3) if n_ratio is not None else None,
                'pf_vs_baseline':   round(dpf, 3)     if dpf     is not None else None,
            })
        print()

    if not rows:
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    # CSV出力（VPS / local 自動切替）
    _VPS_OUT   = r'C:\Users\Administrator\fx_bot\optimizer\confirm_candle_bt_result.csv'
    _LOCAL_OUT = str(Path(__file__).parent / 'confirm_candle_bt_result.csv')
    out_csv    = _VPS_OUT if os.path.isdir(r'C:\Users\Administrator\fx_bot') else _LOCAL_OUT

    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'出力: {out_csv}')

    # 採用判定サマリー
    print('\n=== 採用判定サマリー ===')
    print('基準: N数≥baseline×0.70 かつ PF改善≥+0.10 → v21組み込み検討')
    for symbol in CC_BT_PAIRS:
        pair_rows = [r for r in rows if r['pair'] == symbol]
        base_row  = next((r for r in pair_rows if r['pattern'] == 'baseline'), None)
        if base_row is None:
            continue
        print(f'\n  {symbol} (baseline: PF={base_row["profit_factor"]} N={base_row["total_trades"]}):')
        for r in pair_rows:
            if r['pattern'] == 'baseline':
                continue
            n_ok  = r['n_vs_baseline'] is not None and r['n_vs_baseline'] >= 0.70
            pf_ok = r['pf_vs_baseline'] is not None and r['pf_vs_baseline'] >= 0.10
            if n_ok and pf_ok:
                verdict = '★採用候補'
            elif not n_ok:
                verdict = 'NG: N不足'
            else:
                verdict = 'NG: PF改善不足'
            print(f'    Pattern {r["pattern"]}: '
                  f'PF={r["profit_factor"]}({r["pf_vs_baseline"]:+.3f}) '
                  f'N={r["total_trades"]}(baseline比{r["n_vs_baseline"]:.0%}) '
                  f'→ {verdict}')


if __name__ == '__main__':
    run_confirm_candle_bt()
