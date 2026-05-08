"""
corr_bt.py - CORR戦略（AUDNZD Zスコア平均回帰）バックテスト
Stage1: corr_window x z_entry x z_exit x hold_period (240組, tp=2.5/sl=1.5固定)
Stage2: tp_mult x sl_mult (12組, Stage1最優固定)
出力: optimizer/corr_bt_results.csv
"""

import argparse
import itertools
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

BT_START    = '2021-01-01'  # window=120日分の余裕
BT_FROM     = '2022-01-01'
BT_TO       = '2024-12-31'
SPREAD_COST = 0.0003        # AUDNZD 3pips往復

OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'corr_bt_results.csv')


def fetch_data(start, end):
    print(f'[INFO] yfinance取得: AUDNZD=X  {start} - {end}')
    raw = yf.download('AUDNZD=X', start=start, end=end,
                      interval='1d', auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        df = raw.xs('AUDNZD=X', axis=1, level=1).copy()
    else:
        df = raw.copy()
    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close']].dropna(subset=['close']).sort_index()
    print(f'  AUDNZD: {len(df)}本')
    return df


def calc_atr_series(df, span=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()


def calc_max_dd(equity):
    eq = np.array(equity, dtype=float)
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float((eq - peak).min())


def run_corr_bt(df, atr_series, corr_window, z_entry, z_exit,
                hold_period, tp_mult, sl_mult):
    bt_df = df.loc[
        (df.index >= pd.Timestamp(BT_FROM)) &
        (df.index <= pd.Timestamp(BT_TO))
    ]
    if bt_df.empty:
        return None

    closes       = df['close'].values
    dates_all    = df.index.tolist()
    dates_bt     = bt_df.index.tolist()
    d2full       = {d: i for i, d in enumerate(dates_all)}

    trades         = []
    open_positions = []  # {entry_bt_idx, direction, entry, tp, sl}

    for bt_i, today in enumerate(dates_bt):
        full_i = d2full.get(today)
        if full_i is None or full_i < corr_window:
            continue

        # ローリングZスコア（過去corr_window本のclose）
        window = closes[full_i - corr_window + 1: full_i + 1]
        mean_w = window.mean()
        std_w  = window.std(ddof=0)
        if std_w == 0:
            continue
        z_today = (closes[full_i] - mean_w) / std_w

        if today not in atr_series.index:
            continue
        atr_val = float(atr_series.loc[today])
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        bar  = bt_df.loc[today]
        h, l = bar['high'], bar['low']
        mid  = (h + l) / 2.0

        # ---- 既存ポジション決済チェック（優先順位: TP/SL > Zスコア回帰 > hold_period）----
        still_open = []
        for pos in open_positions:
            hit     = None
            pnl_raw = 0.0

            # (1) TP/SL（intra-bar high/low）
            if pos['direction'] == 'buy':
                if l <= pos['sl']:
                    hit = 'sl'; pnl_raw = pos['sl'] - pos['entry']
                elif h >= pos['tp']:
                    hit = 'tp'; pnl_raw = pos['tp'] - pos['entry']
            else:
                if h >= pos['sl']:
                    hit = 'sl'; pnl_raw = pos['entry'] - pos['sl']
                elif l <= pos['tp']:
                    hit = 'tp'; pnl_raw = pos['entry'] - pos['tp']

            # (2) Zスコア回帰（当日close Z値が z_exit 以内）
            if hit is None and abs(z_today) <= z_exit:
                hit = 'z_exit'
                pnl_raw = (mid - pos['entry']) if pos['direction'] == 'buy' \
                          else (pos['entry'] - mid)

            # (3) hold_period 強制クローズ
            if hit is None and (bt_i - pos['entry_bt_idx']) >= hold_period:
                hit = 'hold_expire'
                pnl_raw = (mid - pos['entry']) if pos['direction'] == 'buy' \
                          else (pos['entry'] - mid)

            if hit is not None:
                trades.append({
                    'entry_date': dates_bt[pos['entry_bt_idx']].strftime('%Y-%m-%d'),
                    'exit_date':  today.strftime('%Y-%m-%d'),
                    'direction':  pos['direction'],
                    'exit_type':  hit,
                    'pnl_price':  round(pnl_raw - SPREAD_COST, 6),
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        # ---- エントリー判定 ----
        if open_positions:
            continue
        if abs(z_today) < z_entry:
            continue

        direction = 'sell' if z_today > 0 else 'buy'
        tp_dist   = atr_val * tp_mult
        sl_dist   = atr_val * sl_mult
        entry_p   = bar['close']
        if direction == 'buy':
            tp_p = entry_p + tp_dist
            sl_p = entry_p - sl_dist
        else:
            tp_p = entry_p - tp_dist
            sl_p = entry_p + sl_dist

        open_positions.append({
            'entry_bt_idx': bt_i,
            'direction':    direction,
            'entry':        entry_p,
            'tp':           tp_p,
            'sl':           sl_p,
        })

    # 未決済を最終日中値でクローズ
    if dates_bt and open_positions:
        last_date = dates_bt[-1]
        last_bar  = bt_df.loc[last_date]
        mid_last  = (last_bar['high'] + last_bar['low']) / 2.0
        for pos in open_positions:
            pnl_raw = (mid_last - pos['entry']) if pos['direction'] == 'buy' \
                      else (pos['entry'] - mid_last)
            trades.append({
                'entry_date': dates_bt[pos['entry_bt_idx']].strftime('%Y-%m-%d'),
                'exit_date':  last_date.strftime('%Y-%m-%d'),
                'direction':  pos['direction'],
                'exit_type':  'bt_end',
                'pnl_price':  round(pnl_raw - SPREAD_COST, 6),
            })

    if not trades:
        return None

    df_t     = pd.DataFrame(trades)
    n        = len(df_t)
    wins     = int((df_t['pnl_price'] > 0).sum())
    gp       = df_t.loc[df_t['pnl_price'] > 0, 'pnl_price'].sum()
    gl       = (-df_t.loc[df_t['pnl_price'] <= 0, 'pnl_price']).sum()
    pf       = round(gp / gl, 3) if gl > 0 else 99.0
    win_rate = round(wins / n * 100, 1)

    df_t['hd'] = (pd.to_datetime(df_t['exit_date']) -
                  pd.to_datetime(df_t['entry_date'])).dt.days
    avg_hold   = round(float(df_t['hd'].mean()), 1)

    cum    = [0.0] + df_t.sort_values('exit_date')['pnl_price'].cumsum().tolist()
    max_dd = round(calc_max_dd(cum), 6)

    return {
        'corr_window':  corr_window,
        'z_entry':      z_entry,
        'z_exit':       z_exit,
        'hold_period':  hold_period,
        'tp_mult':      tp_mult,
        'sl_mult':      sl_mult,
        'n':            n,
        'wins':         wins,
        'losses':       n - wins,
        'win_rate':     win_rate,
        'pf':           pf,
        'avg_hold':     avg_hold,
        'max_dd':       max_dd,
        'gross_profit': round(float(gp), 6),
        'gross_loss':   round(float(gl), 6),
    }


def is_qualified(r):
    return r['pf'] > 1.2 and r['win_rate'] > 45.0 and r['n'] >= 30


def best_of(rows):
    q = [r for r in rows if is_qualified(r)]
    pool = q if q else rows
    return max(pool, key=lambda x: (x['pf'], x['n'], x['win_rate']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=OUTPUT_CSV)
    args = parser.parse_args()

    df = fetch_data(BT_START, BT_TO)
    if df is None or df.empty:
        print('[ERROR] データ取得失敗')
        return

    atr_s = calc_atr_series(df)

    TP_FIXED, SL_FIXED = 2.5, 1.5

    stage1_grid = list(itertools.product(
        [30, 60, 90, 120],
        [0.8, 1.0, 1.2, 1.5, 2.0],
        [0.0, 0.3, 0.5],
        [5, 10, 15, 20],
    ))

    print(f'\n=== CORR Stage1 ({BT_FROM}-{BT_TO}) ===')
    print(f'グリッド数: {len(stage1_grid)}  tp={TP_FIXED} sl={SL_FIXED}')
    print(f'{"win":>4}|{"ze":>5}|{"zx":>5}|{"hp":>4}|{"N":>5}|{"PF":>6}|{"WR":>6}|{"hold":>6}')
    print('-' * 52)

    s1_rows = []
    for cw, ze, zx, hp in stage1_grid:
        res = run_corr_bt(df, atr_s, cw, ze, zx, hp, TP_FIXED, SL_FIXED)
        if res is None:
            continue
        s1_rows.append(res)
        m = '*' if is_qualified(res) else ' '
        print(f'{m}{cw:>3}|{ze:>5.1f}|{zx:>5.1f}|{hp:>4}|'
              f'{res["n"]:>5}|{res["pf"]:>6.3f}|{res["win_rate"]:>5.1f}%|'
              f'{res["avg_hold"]:>5.1f}d')

    if not s1_rows:
        print('[ERROR] Stage1結果なし')
        return

    bs1 = best_of(s1_rows)
    print(f'\nStage1最良: window={bs1["corr_window"]} z_entry={bs1["z_entry"]} '
          f'z_exit={bs1["z_exit"]} hold={bs1["hold_period"]} '
          f'PF={bs1["pf"]} WR={bs1["win_rate"]}% n={bs1["n"]}')

    stage2_grid = list(itertools.product(
        [1.5, 2.0, 2.5, 3.0],
        [1.0, 1.5, 2.0],
    ))

    print(f'\n=== CORR Stage2 (window={bs1["corr_window"]} ze={bs1["z_entry"]} '
          f'zx={bs1["z_exit"]} hp={bs1["hold_period"]}) ===')
    print(f'グリッド数: {len(stage2_grid)}')
    print(f'{"tp":>5}|{"sl":>5}|{"N":>5}|{"PF":>6}|{"WR":>6}|{"hold":>6}')
    print('-' * 38)

    s2_rows = []
    for tm, sm in stage2_grid:
        res = run_corr_bt(df, atr_s,
                          bs1['corr_window'], bs1['z_entry'],
                          bs1['z_exit'], bs1['hold_period'],
                          tm, sm)
        if res is None:
            continue
        s2_rows.append(res)
        m = '*' if is_qualified(res) else ' '
        print(f'{m}{tm:>5.1f}|{sm:>5.1f}|{res["n"]:>5}|{res["pf"]:>6.3f}|'
              f'{res["win_rate"]:>5.1f}%|{res["avg_hold"]:>5.1f}d')

    # CSV出力
    all_rows = s1_rows + s2_rows
    df_out = pd.DataFrame(all_rows)
    df_out.to_csv(args.output, index=False, encoding='utf-8')
    print(f'\n出力: {args.output}')

    # 上位10件
    all_q = [r for r in all_rows if is_qualified(r)]
    top10 = sorted(all_q if all_q else all_rows,
                   key=lambda x: (x['pf'], x['n'], x['win_rate']), reverse=True)[:10]
    print('\n=== 上位10件 (採択基準: PF>1.2 WR>45% n>=30) ===')
    print(f'{"win":>4}|{"ze":>5}|{"zx":>5}|{"hp":>4}|{"tp":>5}|{"sl":>5}|'
          f'{"N":>5}|{"PF":>6}|{"WR":>6}')
    print('-' * 58)
    for r in top10:
        print(f'  {r["corr_window"]:>3}|{r["z_entry"]:>5.1f}|{r["z_exit"]:>5.1f}|'
              f'{r["hold_period"]:>4}|{r["tp_mult"]:>5.1f}|{r["sl_mult"]:>5.1f}|'
              f'{r["n"]:>5}|{r["pf"]:>6.3f}|{r["win_rate"]:>5.1f}%')

    if s2_rows:
        bfinal = best_of(s2_rows)
        print(f'\n=== 最終推奨パラメータ ===')
        print(f'  CORR_P = {{\'corr_window\': {bfinal["corr_window"]}, '
              f'\'z_entry\': {bfinal["z_entry"]}, '
              f'\'z_exit\': {bfinal["z_exit"]}, '
              f'\'hold_period\': {bfinal["hold_period"]}}}')
        print(f'  MULTIPLIERS CORR: tp={bfinal["tp_mult"]}  sl={bfinal["sl_mult"]}')
        print(f'  PF={bfinal["pf"]}  WR={bfinal["win_rate"]}%  n={bfinal["n"]}')


if __name__ == '__main__':
    main()
