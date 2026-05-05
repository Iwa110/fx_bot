"""
str_backtest.py - STR（通貨強弱）戦略バックテスト
期間: 2022-01-01 - 2024-12-31  足: D1  データ: yfinance
グリッドサーチ: lookback x min_spread
出力: str_bt_results.csv
"""

import argparse
import itertools
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

# ===== 定数 =====
BT_START = '2021-12-01'   # lookback分の余裕を含めた取得開始
BT_FROM  = '2022-01-01'   # バックテスト評価開始
BT_TO    = '2024-12-31'   # バックテスト評価終了

TRADE_PAIRS = [
    'EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'EURGBP',
    'USDCAD', 'USDCHF', 'NZDUSD', 'EURJPY', 'GBPJPY',
]

STR_TICKERS = {
    'EURUSD': ('EUR', 'USD'), 'GBPUSD': ('GBP', 'USD'), 'AUDUSD': ('AUD', 'USD'),
    'USDJPY': ('USD', 'JPY'), 'EURGBP': ('EUR', 'GBP'), 'USDCAD': ('USD', 'CAD'),
    'USDCHF': ('USD', 'CHF'), 'NZDUSD': ('NZD', 'USD'), 'EURJPY': ('EUR', 'JPY'),
    'GBPJPY': ('GBP', 'JPY'),
}

YFINANCE_MAP = {
    'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'AUDUSD': 'AUDUSD=X',
    'USDJPY': 'USDJPY=X', 'EURGBP': 'EURGBP=X', 'USDCAD': 'USDCAD=X',
    'USDCHF': 'USDCHF=X', 'NZDUSD': 'NZDUSD=X', 'EURJPY': 'EURJPY=X',
    'GBPJPY': 'GBPJPY=X',
}

# risk_manager準拠: STR tp_mult=2.5, sl_mult=1.5
TP_MULT = 2.5
SL_MULT = 1.5
LOT     = 0.01

# グリッドサーチ候補
LOOKBACK_CANDIDATES  = [5, 10, 20]
MINSPREAD_CANDIDATES = [0.010, 0.015, 0.020]

OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'str_bt_results.csv')


# ===== データ取得 =====
def fetch_all(start: str, end: str) -> dict:
    """全ペアのD1データをyfinanceで一括取得。"""
    tickers = list(YFINANCE_MAP.values())
    print(f'[INFO] yfinance取得: {len(tickers)}ペア  {start} - {end}')
    raw = yf.download(tickers, start=start, end=end, interval='1d',
                      auto_adjust=True, progress=False)

    data = {}
    for sym, yf_sym in YFINANCE_MAP.items():
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(yf_sym, axis=1, level=1)[['Open', 'High', 'Low', 'Close']].copy()
            else:
                df = raw[['Open', 'High', 'Low', 'Close']].copy()
            df.columns = ['open', 'high', 'low', 'close']
            df = df.dropna(subset=['close']).sort_index()
            data[sym] = df
            print(f'  {sym}: {len(df)}本')
        except Exception as e:
            print(f'  [WARN] {sym}: 取得失敗 ({e})')
    return data


# ===== ATR計算（EWM span=14、risk_manager準拠） =====
def calc_atr_series(df: pd.DataFrame, span: int = 14) -> pd.Series:
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()


# ===== DD計算 =====
def calc_max_dd(equity: list) -> float:
    eq = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd   = (eq - peak)
    return float(dd.min()) if len(dd) > 0 else 0.0


# ===== コアBTロジック =====
def run_str_bt(data: dict, lookback: int, min_spread: float,
               hold_period: int = 5) -> dict:
    """
    STR戦略バックテスト。
    シグナル判定: lookback日前比リターンで通貨強弱スコア計算。
    TP/SL: ATRベース(tp_mult=2.5, sl_mult=1.5)。
    hold_period: 強制クローズ日数。
    """
    # 全ペアのATR事前計算
    atrs = {}
    for sym, df in data.items():
        atrs[sym] = calc_atr_series(df)

    # バックテスト期間のインデックス（全ペア共通の日付）
    all_dates = sorted(set.intersection(*[set(df.index) for df in data.values()]))
    bt_dates  = [d for d in all_dates
                 if pd.Timestamp(BT_FROM) <= d <= pd.Timestamp(BT_TO)]

    if not bt_dates:
        return None

    trades = []
    open_positions = []  # {'entry_date', 'exit_date_max', 'symbol', 'direction', 'entry', 'tp', 'sl', 'atr'}

    for today in bt_dates:
        # ---- 既存ポジション決済チェック ----
        still_open = []
        for pos in open_positions:
            df = data.get(pos['symbol'])
            if df is None or today not in df.index:
                still_open.append(pos)
                continue

            bar   = df.loc[today]
            h, l  = bar['high'], bar['low']
            hit   = None
            pnl_p = 0.0  # pips相当(価格差)

            if pos['direction'] == 'buy':
                if l <= pos['sl']:
                    hit = 'sl'; pnl_p = pos['sl'] - pos['entry']
                elif h >= pos['tp']:
                    hit = 'tp'; pnl_p = pos['tp'] - pos['entry']
            else:
                if h >= pos['sl']:
                    hit = 'sl'; pnl_p = pos['entry'] - pos['sl']
                elif l <= pos['tp']:
                    hit = 'tp'; pnl_p = pos['entry'] - pos['tp']

            # 強制クローズ
            if hit is None and today >= pos['exit_date_max']:
                mid   = (h + l) / 2.0
                hit   = 'hold_expire'
                pnl_p = (mid - pos['entry']) if pos['direction'] == 'buy' else (pos['entry'] - mid)

            if hit is not None:
                trades.append({
                    'entry_date': pos['entry_date'].strftime('%Y-%m-%d'),
                    'exit_date':  today.strftime('%Y-%m-%d'),
                    'symbol':     pos['symbol'],
                    'direction':  pos['direction'],
                    'exit_type':  hit,
                    'pnl_price':  round(pnl_p, 6),
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        # ---- STRシグナル判定 ----
        # lookback日前のインデックス取得
        today_pos = all_dates.index(today)
        if today_pos < lookback:
            continue
        lb_date = all_dates[today_pos - lookback]

        # 通貨スコア計算
        scores = {}
        for sym in TRADE_PAIRS:
            df = data.get(sym)
            if df is None:
                continue
            if today not in df.index or lb_date not in df.index:
                continue
            close_now  = df.loc[today, 'close']
            close_past = df.loc[lb_date, 'close']
            if close_past == 0:
                continue
            ret = (close_now - close_past) / close_past
            base, quote = STR_TICKERS[sym]
            scores[base]  = scores.get(base, 0.0) + ret
            scores[quote] = scores.get(quote, 0.0) - ret

        if not scores:
            continue

        sc       = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        strongest = sc[0][0]
        weakest   = sc[-1][0]
        spread    = sc[0][1] - sc[-1][1]

        if spread < min_spread:
            continue

        # 最良ペア選択
        best_pair  = None
        best_score = -99.0
        for sym in TRADE_PAIRS:
            if sym not in STR_TICKERS:
                continue
            base, quote = STR_TICKERS[sym]
            score = 0.0
            if base  == strongest: score += scores.get(strongest, 0.0)
            if quote == weakest:   score += abs(scores.get(weakest, 0.0))
            if base  == weakest:   score -= abs(scores.get(weakest, 0.0))
            if quote == strongest: score -= scores.get(strongest, 0.0)
            if score > best_score:
                best_score = score
                best_pair  = sym

        norm_denom = abs(sc[0][1]) + abs(sc[-1][1])
        if norm_denom > 0 and best_score > 0:
            best_score = best_score / norm_denom

        if not best_pair or best_score <= 0:
            continue

        df = data.get(best_pair)
        if df is None or today not in df.index:
            continue

        base, quote = STR_TICKERS[best_pair]
        direction   = 'buy' if scores.get(base, 0.0) > scores.get(quote, 0.0) else 'sell'

        # ATR取得
        atr_s   = atrs.get(best_pair)
        if atr_s is None or today not in atr_s.index:
            continue
        atr_val = atr_s.loc[today]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        tp_dist = atr_val * TP_MULT
        sl_dist = atr_val * SL_MULT

        entry_price = df.loc[today, 'close']
        if direction == 'buy':
            tp_price = entry_price + tp_dist
            sl_price = entry_price - sl_dist
        else:
            tp_price = entry_price - tp_dist
            sl_price = entry_price + sl_dist

        # 強制クローズ日（from open date + hold_period営業日後）
        exit_max_idx = today_pos + hold_period
        exit_max_date = (all_dates[exit_max_idx]
                         if exit_max_idx < len(all_dates)
                         else all_dates[-1])

        open_positions.append({
            'entry_date':    today,
            'exit_date_max': exit_max_date,
            'symbol':        best_pair,
            'direction':     direction,
            'entry':         entry_price,
            'tp':            tp_price,
            'sl':            sl_price,
            'atr':           atr_val,
        })

    # 未決済を最終日終値でクローズ
    last_date = bt_dates[-1]
    for pos in open_positions:
        df  = data.get(pos['symbol'])
        if df is None:
            continue
        bar    = df.loc[last_date] if last_date in df.index else df.iloc[-1]
        mid    = (bar['high'] + bar['low']) / 2.0
        pnl_p  = (mid - pos['entry']) if pos['direction'] == 'buy' else (pos['entry'] - mid)
        trades.append({
            'entry_date': pos['entry_date'].strftime('%Y-%m-%d'),
            'exit_date':  last_date.strftime('%Y-%m-%d'),
            'symbol':     pos['symbol'],
            'direction':  pos['direction'],
            'exit_type':  'bt_end',
            'pnl_price':  round(pnl_p, 6),
        })

    if not trades:
        return None

    df_trades = pd.DataFrame(trades)

    wins   = (df_trades['pnl_price'] > 0).sum()
    losses = (df_trades['pnl_price'] <= 0).sum()
    n      = len(df_trades)

    gross_profit = df_trades.loc[df_trades['pnl_price'] > 0, 'pnl_price'].sum()
    gross_loss   = (-df_trades.loc[df_trades['pnl_price'] <= 0, 'pnl_price']).sum()

    pf       = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0
    win_rate = round(wins / n * 100, 1)

    # 平均保有期間（日数）
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_date'])
    df_trades['exit_dt']  = pd.to_datetime(df_trades['exit_date'])
    df_trades['hold_days'] = (df_trades['exit_dt'] - df_trades['entry_dt']).dt.days
    avg_hold = round(df_trades['hold_days'].mean(), 1)

    # 最大DD（累積pnlで計算）
    df_trades_sorted = df_trades.sort_values('exit_date')
    cumulative = df_trades_sorted['pnl_price'].cumsum().tolist()
    if cumulative:
        equity = [0.0] + [c for c in cumulative]
        max_dd = round(calc_max_dd(equity), 6)
    else:
        max_dd = 0.0

    return {
        'lookback':   lookback,
        'min_spread': min_spread,
        'n':          n,
        'wins':       int(wins),
        'losses':     int(losses),
        'win_rate':   win_rate,
        'pf':         pf,
        'avg_hold':   avg_hold,
        'max_dd':     max_dd,
        'gross_profit': round(gross_profit, 6),
        'gross_loss':   round(gross_loss, 6),
    }


# ===== メイン =====
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lookback',   type=int,   default=None,
                        help='単一実行用lookback（省略時はグリッドサーチ）')
    parser.add_argument('--min-spread', type=float, default=None,
                        help='単一実行用min_spread（省略時はグリッドサーチ）')
    parser.add_argument('--output',     default=OUTPUT_CSV)
    args = parser.parse_args()

    # データ取得（全グリッド共通）
    data = fetch_all(BT_START, BT_TO)
    if not data:
        print('[ERROR] データ取得失敗')
        return

    # グリッド構築
    if args.lookback is not None and args.min_spread is not None:
        grid = [(args.lookback, args.min_spread)]
    else:
        grid = list(itertools.product(LOOKBACK_CANDIDATES, MINSPREAD_CANDIDATES))

    print(f'\n=== STR バックテスト ({BT_FROM} - {BT_TO}) ===')
    print(f'グリッド数: {len(grid)}  hold_period=5日 TP_MULT={TP_MULT} SL_MULT={SL_MULT}')
    print(f'{"lookback":>9} | {"min_spread":>10} | {"N":>5} | '
          f'{"PF":>6} | {"勝率":>6} | {"avg_hold":>8} | {"max_DD":>10}')
    print('-' * 70)

    rows = []
    for lb, ms in grid:
        res = run_str_bt(data, lookback=lb, min_spread=ms)
        if res is None:
            print(f'  lb={lb:>2} ms={ms:.3f}: 取引なし')
            continue
        rows.append(res)
        print(f'  {lb:>9} | {ms:>10.3f} | {res["n"]:>5} | '
              f'{res["pf"]:>6.3f} | {res["win_rate"]:>5.1f}% | '
              f'{res["avg_hold"]:>7.1f}d | {res["max_dd"]:>+10.5f}')

    if not rows:
        print('[ERROR] 有効な結果なし')
        return

    df_out = pd.DataFrame(rows)
    df_out.to_csv(args.output, index=False, encoding='utf-8')
    print(f'\n出力: {args.output}')

    # サマリー
    print('\n=== グリッドサーチ結果サマリー（PF降順） ===')
    print(f'{"lookback":>9} | {"min_spread":>10} | {"N":>5} | '
          f'{"PF":>6} | {"勝率":>6} | {"avg_hold":>8} | {"max_DD":>10}')
    print('-' * 70)
    for r in sorted(rows, key=lambda x: x['pf'], reverse=True):
        print(f'  {r["lookback"]:>9} | {r["min_spread"]:>10.3f} | {r["n"]:>5} | '
              f'{r["pf"]:>6.3f} | {r["win_rate"]:>5.1f}% | '
              f'{r["avg_hold"]:>7.1f}d | {r["max_dd"]:>+10.5f}')

    best = max(rows, key=lambda x: x['pf'])
    print(f'\n最良パラメータ(PF基準): lookback={best["lookback"]} '
          f'min_spread={best["min_spread"]:.3f} '
          f'PF={best["pf"]} 勝率={best["win_rate"]}% N={best["n"]}')


if __name__ == '__main__':
    main()
