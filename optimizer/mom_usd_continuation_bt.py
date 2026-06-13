"""
USD系ペア Momentum Continuation バックテスト
- 対象: EURUSD / GBPUSD / AUDUSD
- TF: H4エントリー + 日足トレンドフィルター
- WF: IS=60% / OOS=40%
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

PAIRS = {
    'EURUSD': {'ticker': 'EURUSD=X', 'cost_pips': 1.0,  'pip': 0.0001},
    'GBPUSD': {'ticker': 'GBPUSD=X', 'cost_pips': 1.5,  'pip': 0.0001},
    'AUDUSD': {'ticker': 'AUDUSD=X', 'cost_pips': 1.2,  'pip': 0.0001},
}

TP_ATR    = 3.0
SL_ATR    = 1.0
BE_RATIO  = 0.5   # TP×0.5到達でBE移動
MAX_HOLD_BARS = 5 * 6   # 5日 × 6bars/day (4h)

SMA_TOUCH_ATR = 0.3   # SMA20 ± ATR×0.3


# ── データ取得 ─────────────────────────────────────────────
def fetch_yf(ticker, interval, years=3):
    try:
        import yfinance as yf
    except ImportError:
        print('[ERROR] pip install yfinance')
        sys.exit(1)

    end  = datetime.now()
    # yfinance 1h/2h は直近729日まで
    if interval in ('1h', '2h', '60m', '90m'):
        days = min(365 * years, 729)
    else:
        days = 365 * years
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                     end=end.strftime('%Y-%m-%d'), interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        raise ValueError(f'No data: {ticker} {interval}')
    if hasattr(df.columns, 'levels'):
        df.columns = [c[0] for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close']].dropna()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    return df


# ── インジケーター ─────────────────────────────────────────
def calc_atr(df, n=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def calc_rsi(close, n=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(span=n, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=n, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def prepare_h4(df):
    d = df.copy()
    d['sma20'] = d['Close'].rolling(20).mean()
    d['atr']   = calc_atr(d, 14)
    d['rsi']   = calc_rsi(d['Close'], 14)
    return d


def prepare_daily(df):
    d = df.copy()
    d['sma20']  = d['Close'].rolling(20).mean()
    d['sma50']  = d['Close'].rolling(50).mean()
    d['slope3'] = d['sma20'] - d['sma20'].shift(3)   # 3日前比スロープ
    return d


# ── シグナル生成 ───────────────────────────────────────────
def generate_signals(h4, daily, pair_cfg):
    """H4バーごとにエントリーシグナルを生成。条件ヒット数も記録。"""

    cond_counts = {'c1_pass': 0, 'c2_pass': 0, 'c3_pass': 0, 'c4_pass': 0, 'total_entry': 0}
    signals = []

    for i in range(50, len(h4)):
        bar = h4.iloc[i]
        ts  = h4.index[i]

        # 日足データ: このH4バー以前の最新日足
        daily_sub = daily[daily.index.normalize() <= pd.Timestamp(ts.date())]
        if len(daily_sub) < 55:
            continue
        d = daily_sub.iloc[-1]

        # 条件1: 日足 SMA20/SMA50 トレンド方向
        if pd.isna(d['sma20']) or pd.isna(d['sma50']):
            continue
        if d['sma20'] > d['sma50']:
            direction = 1   # Long
        else:
            direction = -1  # Short
        cond_counts['c1_pass'] += 1

        # 条件2: H4終値がSMA20タッチ（直近2本以内）
        touch = False
        for j in [i, i-1]:
            if j < 0:
                continue
            bar_j = h4.iloc[j]
            band  = bar_j['atr'] * SMA_TOUCH_ATR
            if abs(bar_j['Close'] - bar_j['sma20']) <= band:
                touch = True
                break
        if not touch:
            continue
        cond_counts['c2_pass'] += 1

        # 条件3: RSI過熱排除
        rsi = bar['rsi']
        if pd.isna(rsi):
            continue
        if direction == 1 and not (40 <= rsi <= 55):
            continue
        if direction == -1 and not (45 <= rsi <= 60):
            continue
        cond_counts['c3_pass'] += 1

        # 条件4: 日足SMAスロープ同方向
        if pd.isna(d['slope3']):
            continue
        if direction == 1 and d['slope3'] <= 0:
            continue
        if direction == -1 and d['slope3'] >= 0:
            continue
        cond_counts['c4_pass'] += 1

        cond_counts['total_entry'] += 1
        signals.append({'ts': ts, 'bar_idx': i, 'direction': direction,
                         'entry': bar['Close'], 'atr': bar['atr']})

    return signals, cond_counts


# ── シミュレーション ───────────────────────────────────────
def simulate(signals, h4, pair_cfg):
    trades = []
    active = None

    sig_map = {s['bar_idx']: s for s in signals}

    for i in range(len(h4)):
        bar = h4.iloc[i]

        if active is not None:
            # TP/SL/BE/TimeExit判定
            s = active
            high, low = bar['High'], bar['Low']
            hit_tp = hit_sl = False
            be_triggered = s.get('be_triggered', False)

            sl_price = s['sl']
            tp_price = s['tp']

            if s['direction'] == 1:
                if high >= tp_price:
                    hit_tp = True
                if low <= sl_price:
                    hit_sl = True
                # BE check
                if not be_triggered and high >= s['entry'] + (tp_price - s['entry']) * BE_RATIO:
                    active['sl'] = s['entry']
                    active['be_triggered'] = True
            else:
                if low <= tp_price:
                    hit_tp = True
                if high >= sl_price:
                    hit_sl = True
                if not be_triggered and low <= s['entry'] - (s['entry'] - tp_price) * BE_RATIO:
                    active['sl'] = s['entry']
                    active['be_triggered'] = True

            bars_held = i - s['entry_bar']
            time_exit = bars_held >= MAX_HOLD_BARS

            if hit_tp or hit_sl or time_exit:
                if hit_tp:
                    exit_price = tp_price
                    exit_reason = 'TP'
                elif hit_sl:
                    exit_price = sl_price
                    exit_reason = 'SL'
                else:
                    exit_price = bar['Close']
                    exit_reason = 'TimeExit'

                pip     = pair_cfg['pip']
                cost    = pair_cfg['cost_pips'] * pip
                raw_pnl = (exit_price - s['entry']) * s['direction']
                pnl_pips = raw_pnl / pip - pair_cfg['cost_pips']
                pnl_r    = raw_pnl / (s['atr'] * SL_ATR)  # R倍数

                trades.append({
                    'entry_ts':   s['ts'],
                    'exit_ts':    h4.index[i],
                    'direction':  s['direction'],
                    'entry':      s['entry'],
                    'exit':       exit_price,
                    'pnl_pips':   pnl_pips,
                    'pnl_r':      pnl_r,
                    'exit_reason':exit_reason,
                    'bars_held':  bars_held,
                })
                active = None

        # 新エントリー（1ポジション上限）
        if active is None and i in sig_map:
            s = sig_map[i]
            atr = s['atr']
            direction = s['direction']
            entry = s['entry']
            tp = entry + direction * atr * TP_ATR
            sl = entry - direction * atr * SL_ATR
            active = {**s, 'tp': tp, 'sl': sl, 'entry_bar': i, 'be_triggered': False}

    return pd.DataFrame(trades)


# ── 指標計算 ───────────────────────────────────────────────
def calc_metrics(trades_df):
    if trades_df is None or len(trades_df) == 0:
        return {'n': 0, 'PF': 0, 'WR': 0, 'Sharpe': 0, 'MaxDD_r': 0, 'avg_r': 0}

    r = trades_df['pnl_r']
    wins  = r[r > 0]
    loses = r[r <= 0]

    pf  = wins.sum() / (-loses.sum()) if loses.sum() != 0 else np.inf
    wr  = len(wins) / len(r)
    avg = r.mean()

    # Sharpe (daily: グループ化)
    daily_r = trades_df.set_index('entry_ts')['pnl_r'].resample('D').sum()
    sharpe  = daily_r.mean() / daily_r.std() * np.sqrt(252) if daily_r.std() > 0 else 0

    # MaxDD (累積R)
    cum = r.cumsum()
    dd  = (cum - cum.cummax()).min()

    return {'n': len(r), 'PF': round(pf, 3), 'WR': round(wr * 100, 1),
            'Sharpe': round(sharpe, 2), 'MaxDD_r': round(dd, 2), 'avg_r': round(avg, 3)}


# ── WF分割 ────────────────────────────────────────────────
def walk_forward_split(trades_df, is_ratio=0.60):
    if trades_df is None or len(trades_df) == 0:
        return None, None
    trades_df = trades_df.sort_values('entry_ts')
    split_idx = int(len(trades_df) * is_ratio)
    return trades_df.iloc[:split_idx], trades_df.iloc[split_idx:]


# ── 月別損益集計 ───────────────────────────────────────────
def monthly_pnl(trades_df, label):
    if trades_df is None or len(trades_df) == 0:
        return pd.Series(dtype=float, name=label)
    s = trades_df.set_index('entry_ts')['pnl_r']
    s.index = pd.to_datetime(s.index)
    return s.resample('ME').sum().rename(label)


# ── メイン ────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('USD系ペア Momentum Continuation BT')
    print('=' * 65)

    all_trades   = {}
    all_conds    = {}
    monthly_data = []

    for pair, cfg in PAIRS.items():
        print(f'\n[{pair}] データ取得中...')
        try:
            h4_raw    = fetch_yf(cfg['ticker'], '1h')   # yfinance H4=4h intervalは不安定→1hで取得→resample
            daily_raw = fetch_yf(cfg['ticker'], '1d')
        except Exception as e:
            print(f'  [SKIP] {e}')
            continue

        # 1h→4hリサンプル
        h4_raw = h4_raw.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
        print(f'  H4 bars: {len(h4_raw)}, Daily bars: {len(daily_raw)}')

        h4    = prepare_h4(h4_raw)
        daily = prepare_daily(daily_raw)

        print(f'  シグナル生成中...')
        signals, cond_counts = generate_signals(h4, daily, cfg)
        print(f'  総バー: {len(h4)}, シグナル数: {len(signals)}')
        print(f'  条件通過: C1={cond_counts["c1_pass"]} C2={cond_counts["c2_pass"]} '
              f'C3={cond_counts["c3_pass"]} C4={cond_counts["c4_pass"]} '
              f'Entry={cond_counts["total_entry"]}')
        all_conds[pair] = cond_counts

        if len(signals) == 0:
            print('  [WARN] シグナルなし')
            continue

        trades = simulate(signals, h4, cfg)
        all_trades[pair] = trades
        print(f'  約定: {len(trades)}件')

        ms = monthly_pnl(trades, pair)
        monthly_data.append(ms)

    # ── サマリー出力 ─────────────────────────────────────
    print('\n' + '=' * 65)
    print('IS/OOS サマリー（Walk-Forward IS=60% / OOS=40%）')
    print('=' * 65)

    results = {}
    for pair, trades in all_trades.items():
        is_t, oos_t = walk_forward_split(trades)
        is_m  = calc_metrics(is_t)
        oos_m = calc_metrics(oos_t)
        results[pair] = {'IS': is_m, 'OOS': oos_m}

        print(f'\n  {pair}')
        print(f'    IS  (n={is_m["n"]:3d})  PF={is_m["PF"]:.3f}  WR={is_m["WR"]:.1f}%  '
              f'Sharpe={is_m["Sharpe"]:.2f}  MaxDD={is_m["MaxDD_r"]:.2f}R  AvgR={is_m["avg_r"]:.3f}')
        print(f'    OOS (n={oos_m["n"]:3d})  PF={oos_m["PF"]:.3f}  WR={oos_m["WR"]:.1f}%  '
              f'Sharpe={oos_m["Sharpe"]:.2f}  MaxDD={oos_m["MaxDD_r"]:.2f}R  AvgR={oos_m["avg_r"]:.3f}')

        # 合否判定
        o = oos_m
        ok_pf     = o['PF'] >= 1.4
        ok_sharpe = o['Sharpe'] >= 1.0
        ok_dd     = o['MaxDD_r'] > -15.0
        ok_n      = o['n'] >= 30
        passed    = all([ok_pf, ok_sharpe, ok_dd, ok_n])

        flags = f"PF{'✅' if ok_pf else '❌'}  Sharpe{'✅' if ok_sharpe else '❌'}  "
        flags += f"DD{'✅' if ok_dd else '❌'}  n{'✅' if ok_n else '❌'}"
        verdict = '✅ daily_trade.pyへの統合候補' if passed else '❌ 基準未達'
        print(f'    OOS合否: {flags}')
        print(f'    判定: {verdict}')

    # ── 条件別ヒット率 ────────────────────────────────────
    print('\n' + '=' * 65)
    print('エントリー条件別フィルタリング統計')
    print('=' * 65)
    print(f'  {"Pair":<10} {"Total_H4":>8} {"C1(trend)":>10} {"C2(touch)":>10} {"C3(rsi)":>10} {"C4(slope)":>10} {"Entry":>8}')
    for pair, cond in all_conds.items():
        h4_n = len(all_trades.get(pair, pd.DataFrame()))
        print(f'  {pair:<10} {"--":>8} {cond["c1_pass"]:>10} {cond["c2_pass"]:>10} '
              f'{cond["c3_pass"]:>10} {cond["c4_pass"]:>10} {cond["total_entry"]:>8}')

    # ── 月別損益グラフ ───────────────────────────────────
    if monthly_data:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker

            monthly_df = pd.concat(monthly_data, axis=1).fillna(0)
            monthly_df.index = monthly_df.index.strftime('%Y-%m')

            fig, axes = plt.subplots(len(PAIRS), 1, figsize=(14, 4 * len(PAIRS)), sharex=False)
            if len(PAIRS) == 1:
                axes = [axes]

            colors = {'EURUSD': '#2196F3', 'GBPUSD': '#4CAF50', 'AUDUSD': '#FF9800'}

            for ax, pair in zip(axes, PAIRS.keys()):
                if pair not in monthly_df.columns:
                    continue
                s = monthly_df[pair]
                bar_colors = [colors.get(pair, '#888') if v >= 0 else '#E53935' for v in s]
                ax.bar(range(len(s)), s.values, color=bar_colors, alpha=0.8)
                ax.set_title(f'{pair}  月別損益 (R単位)', fontsize=11)
                ax.set_xticks(range(len(s)))
                ax.set_xticklabels(s.index, rotation=45, ha='right', fontsize=8)
                ax.axhline(0, color='black', linewidth=0.8)
                ax.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.1f'))
                ax.set_ylabel('R')

            plt.tight_layout()
            out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'mom_usd_monthly_pnl.png')
            plt.savefig(out_path, dpi=120, bbox_inches='tight')
            plt.close()
            print(f'\nグラフ保存: {out_path}')
        except Exception as e:
            print(f'\n[WARN] グラフ生成スキップ: {e}')

    # ── 全取引CSV保存 ────────────────────────────────────
    if all_trades:
        dfs = []
        for pair, t in all_trades.items():
            t2 = t.copy()
            t2.insert(0, 'pair', pair)
            dfs.append(t2)
        combined = pd.concat(dfs).sort_values('entry_ts')
        out_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'mom_usd_continuation_bt_result.csv')
        combined.to_csv(out_csv, index=False)
        print(f'全取引CSV保存: {out_csv}')

    print('\n完了')


if __name__ == '__main__':
    main()
