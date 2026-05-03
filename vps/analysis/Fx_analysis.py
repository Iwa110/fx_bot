
##FX Trading Performance Analyzer
##VPS (Windows Server 2022) + MT5 Python API
##Usage: python fx_analysis.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
##Output: C:\Users\Administrator\fx_bot\analysis\


import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone, timedelta
import argparse
import os
import sys

# ============================================================
# CONFIG
# ============================================================
OUTPUT_DIR = 'C:\\Users\\Administrator\\fx_bot\\analysis'

MAGIC_MAP = {
    20250001: 'BB',
    20260001: 'stat_arb',
    20260002: 'SMC_GBPAUD',
    # STR/MOM系はmagicが不明な場合はcommentで判定
}

# commentベースのフォールバック（magic未登録の場合）
COMMENT_MAP = {
    'STR':      'STR',
    'MOM_JPY':  'MOM_JPY',
    'MOM_GBJ':  'MOM_GBJ',
    'MOM_ENZ':  'MOM_ENZ',
    'MOM_ECA':  'MOM_ECA',
    'MOM_GBU':  'MOM_GBU',
    'CORR':     'CORR',
    'stat_arb': 'stat_arb',
    'triangle': 'TRIANGLE',
}

JPY_PAIRS = ['USDJPY', 'GBPJPY', 'EURJPY', 'AUDJPY', 'CADJPY', 'CHFJPY']
JST = timezone(timedelta(hours=9))

# ============================================================
# MT5 DATA FETCH
# ============================================================
def fetch_deals(date_from, date_to):
    if not mt5.initialize():
        print(f'MT5 initialize failed: {mt5.last_error()}')
        sys.exit(1)

    all_deals = mt5.history_deals_get(date_from, date_to)
    mt5.shutdown()

    if all_deals is None or len(all_deals) == 0:
        print('No deals found.')
        sys.exit(0)

    df_all = pd.DataFrame(list(all_deals), columns=all_deals[0]._asdict().keys())

    # entry=0のdealsからorder->strategy名のマップを作成
    entry_df = df_all[df_all['entry'] == 0].copy()
    order_to_strategy = {}
    for _, row in entry_df.iterrows():
        comment = str(row.get('comment', ''))
        for key in ['MOM_JPY','MOM_GBJ','MOM_ENZ','MOM_ECA','MOM_GBU','STR','CORR','TRI']:
            if key in comment:
                order_to_strategy[int(row['ticket'])] = key
                break

    # 決済のみ抽出
    df = df_all[df_all['entry'] == mt5.DEAL_ENTRY_OUT].copy()

    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df['time_jst'] = df['time'].dt.tz_convert(JST)
    df['hour_utc'] = df['time'].dt.hour
    df['weekday'] = df['time'].dt.day_name()
    df['month'] = df['time'].dt.to_period('M').astype(str)

    # order列でエントリーのticketを引いて戦略名を付与
    def assign(row):
        magic = int(row['magic'])
        if magic == 20250001:
            return 'BB'
        if magic == 20260001:
            return 'stat_arb'
        if magic == 20260002:
            return 'SMC_GBPAUD'
        if magic == 20240101:
            return order_to_strategy.get(int(row['order']), 'DAILY_OTHER')
        return f'magic_{magic}'

    df['strategy'] = df.apply(assign, axis=1)
    df['pip_profit'] = df.apply(_to_pips, axis=1)
    return df

def _assign_strategy(row) -> str:
    magic = int(row['magic'])
    comment = str(row.get('comment', ''))
    
    # BB/stat_arb/SMCはmagicで一意識別
    if magic == 20250001:
        return 'BB'
    if magic == 20260001:
        return 'stat_arb'
    if magic == 20260002:
        return 'SMC_GBPAUD'
    
    # daily_trade.pyはcommentで戦略識別
    if magic == 20240101:
        for key in ['MOM_JPY','MOM_GBJ','MOM_ENZ','MOM_ECA','MOM_GBU',
                    'STR','CORR','TRI']:
            if key in comment:
                return key
        return 'DAILY_OTHER'
    
    return f'magic_{magic}'


def _to_pips(row) -> float:
    symbol = str(row['symbol'])
    profit = float(row['profit'])
    volume = float(row['volume'])
    price_open = float(row.get('price', 0))
    # price差はAPIから直接取れないためprofitベースのpip概算
    # JPYペア: 1pip=0.01, その他: 1pip=0.0001
    if volume == 0:
        return 0.0
    pip_val = 0.01 if symbol in JPY_PAIRS else 0.0001
    # profit = pips * pip_value_per_lot * volume (概算)
    pip_value_per_lot = 1000 if symbol in JPY_PAIRS else 10
    pips = profit / (pip_value_per_lot * volume) / pip_val * pip_val * 10000
    return round(profit / volume, 4)  # lot正規化profit


# ============================================================
# METRICS
# ============================================================
def calc_metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    profits = df['profit'].values
    wins = profits[profits > 0]
    losses = profits[profits < 0]
    n = len(profits)
    win_rate = len(wins) / n if n > 0 else 0
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 1e-9
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss if gross_loss > 0 else 0
    cumulative = np.cumsum(profits)
    peak = np.maximum.accumulate(cumulative)
    dd = (peak - cumulative)
    max_dd = dd.max() if len(dd) > 0 else 0
    expected = win_rate * avg_win - (1 - win_rate) * avg_loss
    return {
        'trades': n,
        'win_rate': round(win_rate * 100, 1),
        'profit_factor': round(pf, 3),
        'rr': round(rr, 3),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expected_value': round(expected, 2),
        'net_profit': round(profits.sum(), 2),
        'max_dd': round(max_dd, 2),
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
    }


# ============================================================
# ANALYSIS FUNCTIONS
# ============================================================
def analyze_by_strategy(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strat, g in df.groupby('strategy'):
        m = calc_metrics(g)
        m['strategy'] = strat
        rows.append(m)
    result = pd.DataFrame(rows).set_index('strategy')
    cols = ['trades', 'win_rate', 'profit_factor', 'rr', 'expected_value',
            'net_profit', 'max_dd', 'avg_win', 'avg_loss']
    return result[[c for c in cols if c in result.columns]].sort_values('profit_factor', ascending=False)


def analyze_by_hour(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h in range(24):
        g = df[df['hour_utc'] == h]
        if g.empty:
            continue
        m = calc_metrics(g)
        m['hour_utc'] = h
        rows.append(m)
    return pd.DataFrame(rows).set_index('hour_utc')[
        ['trades', 'win_rate', 'profit_factor', 'net_profit']
    ] if rows else pd.DataFrame()


def analyze_by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sym, g in df.groupby('symbol'):
        m = calc_metrics(g)
        m['symbol'] = sym
        rows.append(m)
    result = pd.DataFrame(rows).set_index('symbol')
    cols = ['trades', 'win_rate', 'profit_factor', 'rr', 'net_profit', 'max_dd']
    return result[[c for c in cols if c in result.columns]].sort_values('net_profit', ascending=False)


def analyze_by_weekday(df: pd.DataFrame) -> pd.DataFrame:
    order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    rows = []
    for day in order:
        g = df[df['weekday'] == day]
        if g.empty:
            continue
        m = calc_metrics(g)
        m['weekday'] = day
        rows.append(m)
    return pd.DataFrame(rows).set_index('weekday')[
        ['trades', 'win_rate', 'profit_factor', 'net_profit']
    ] if rows else pd.DataFrame()


def analyze_monthly(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for month, g in df.groupby('month'):
        m = calc_metrics(g)
        m['month'] = month
        rows.append(m)
    return pd.DataFrame(rows).set_index('month')[
        ['trades', 'win_rate', 'profit_factor', 'net_profit']
    ] if rows else pd.DataFrame()


def analyze_strategy_symbol(df: pd.DataFrame) -> pd.DataFrame:
    """戦略×通貨ペアのクロス集計"""
    rows = []
    for (strat, sym), g in df.groupby(['strategy', 'symbol']):
        m = calc_metrics(g)
        m['strategy'] = strat
        m['symbol'] = sym
        rows.append(m)
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).set_index(['strategy', 'symbol'])
    cols = ['trades', 'win_rate', 'profit_factor', 'net_profit']
    return result[[c for c in cols if c in result.columns]].sort_values('net_profit', ascending=False)


def analyze_strategy_hour(df: pd.DataFrame) -> pd.DataFrame:
    """戦略×時間帯のpivot"""
    rows = []
    for (strat, h), g in df.groupby(['strategy', 'hour_utc']):
        rows.append({'strategy': strat, 'hour_utc': h, 'net_profit': g['profit'].sum(), 'trades': len(g)})
    if not rows:
        return pd.DataFrame()
    pivot = pd.DataFrame(rows).pivot(index='strategy', columns='hour_utc', values='net_profit').fillna(0)
    return pivot


# ============================================================
# PLOTTING
# ============================================================
def _save(fig, name: str):
    path = os.path.join(OUTPUT_DIR, name)
    fig.savefig(path, dpi=120, bbox_inches='tight')
    plt.close(fig)
    print(f'  Saved: {path}')


def plot_equity_curve(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(12, 5))
    strategies = df['strategy'].unique()
    colors = plt.cm.tab10.colors
    for i, strat in enumerate(sorted(strategies)):
        g = df[df['strategy'] == strat].sort_values('time')
        cumsum = g['profit'].cumsum()
        ax.plot(g['time'].dt.tz_localize(None), cumsum, label=strat, color=colors[i % 10], linewidth=1.5)
    # 全体
    total = df.sort_values('time')
    ax.plot(total['time'].dt.tz_localize(None), total['profit'].cumsum(),
            label='TOTAL', color='black', linewidth=2.5, linestyle='--')
    ax.set_title('Equity Curve by Strategy (UTC)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Cumulative Profit (account currency)')
    ax.legend(loc='upper left', fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.grid(alpha=0.3)
    _save(fig, 'equity_curve.png')


def plot_strategy_summary(summary: pd.DataFrame):
    if summary.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, col, title, thresh in zip(
        axes,
        ['profit_factor', 'win_rate', 'net_profit'],
        ['Profit Factor', 'Win Rate (%)', 'Net Profit'],
        [1.0, 50, 0]
    ):
        values = summary[col] if col in summary.columns else pd.Series()
        colors = ['steelblue' if v >= thresh else 'salmon' for v in values]
        bars = ax.bar(summary.index, values, color=colors)
        ax.axhline(thresh, color='gray', linestyle='--', linewidth=1)
        ax.set_title(title)
        ax.set_xticklabels(summary.index, rotation=30, ha='right', fontsize=8)
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f'{val:.2f}', ha='center', va='bottom', fontsize=7)
    fig.suptitle('Strategy Summary', fontsize=13)
    plt.tight_layout()
    _save(fig, 'strategy_summary.png')


def plot_hourly_heatmap(strategy_hour: pd.DataFrame):
    if strategy_hour.empty:
        return
    fig, ax = plt.subplots(figsize=(16, max(4, len(strategy_hour) * 0.5 + 1)))
    data = strategy_hour.values
    vmax = max(abs(data.min()), abs(data.max())) or 1
    im = ax.imshow(data, cmap='RdYlGn', aspect='auto', vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(strategy_hour.columns)))
    ax.set_xticklabels([f'{h:02d}' for h in strategy_hour.columns])
    ax.set_yticks(range(len(strategy_hour.index)))
    ax.set_yticklabels(strategy_hour.index, fontsize=9)
    ax.set_xlabel('Hour (UTC)')
    ax.set_title('Net Profit Heatmap: Strategy x Hour (UTC)')
    plt.colorbar(im, ax=ax, label='Net Profit')
    _save(fig, 'strategy_hour_heatmap.png')


def plot_weekday_bar(weekday: pd.DataFrame):
    if weekday.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = ['steelblue' if v >= 0 else 'salmon' for v in weekday['net_profit']]
    ax.bar(weekday.index, weekday['net_profit'], color=colors)
    ax.axhline(0, color='black', linewidth=0.8)
    ax.set_title('Net Profit by Weekday')
    ax.set_ylabel('Net Profit')
    for i, (idx, row) in enumerate(weekday.iterrows()):
        ax.text(i, row['net_profit'], f"{row['trades']}T", ha='center',
                va='bottom' if row['net_profit'] >= 0 else 'top', fontsize=8)
    _save(fig, 'weekday_profit.png')


def plot_monthly_bar(monthly: pd.DataFrame):
    if monthly.empty:
        return
    fig, ax = plt.subplots(figsize=(max(8, len(monthly) * 0.8), 4))
    colors = ['steelblue' if v >= 0 else 'salmon' for v in monthly['net_profit']]
    ax.bar(monthly.index, monthly['net_profit'], color=colors)
    ax.axhline(0, color='black', linewidth=0.8)
    ax.set_title('Monthly Net Profit')
    ax.set_ylabel('Net Profit')
    plt.xticks(rotation=30, ha='right', fontsize=8)
    _save(fig, 'monthly_profit.png')


def plot_symbol_summary(symbol_df: pd.DataFrame):
    if symbol_df.empty:
        return
    fig, ax = plt.subplots(figsize=(max(8, len(symbol_df) * 0.8), 4))
    colors = ['steelblue' if v >= 0 else 'salmon' for v in symbol_df['net_profit']]
    ax.bar(symbol_df.index, symbol_df['net_profit'], color=colors)
    ax.axhline(0, color='black', linewidth=0.8)
    ax.set_title('Net Profit by Symbol')
    ax.set_ylabel('Net Profit')
    plt.xticks(rotation=30, ha='right', fontsize=8)
    for i, (idx, row) in enumerate(symbol_df.iterrows()):
        ax.text(i, row['net_profit'], f"PF:{row.get('profit_factor', 0):.2f}",
                ha='center', va='bottom' if row['net_profit'] >= 0 else 'top', fontsize=7)
    _save(fig, 'symbol_profit.png')


def plot_rr_distribution(df: pd.DataFrame):
    """戦略別 勝ちトレード/負けトレードの利益分布"""
    strategies = sorted(df['strategy'].unique())
    n = len(strategies)
    if n == 0:
        return
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows), squeeze=False)
    for i, strat in enumerate(strategies):
        ax = axes[i // cols][i % cols]
        g = df[df['strategy'] == strat]['profit']
        wins = g[g > 0]
        losses = g[g < 0]
        if not wins.empty:
            ax.hist(wins, bins=20, color='steelblue', alpha=0.7, label=f'Win(n={len(wins)})')
        if not losses.empty:
            ax.hist(losses, bins=20, color='salmon', alpha=0.7, label=f'Loss(n={len(losses)})')
        ax.axvline(0, color='black', linewidth=0.8)
        ax.set_title(strat, fontsize=9)
        ax.legend(fontsize=7)
    # 余白のaxを非表示
    for j in range(i + 1, rows * cols):
        axes[j // cols][j % cols].set_visible(False)
    fig.suptitle('Profit Distribution by Strategy', fontsize=12)
    plt.tight_layout()
    _save(fig, 'rr_distribution.png')


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='FX Trade Analyzer')
    parser.add_argument('--from', dest='date_from', default=None, help='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', default=None, help='YYYY-MM-DD')
    args = parser.parse_args()

    now = datetime.now(tz=timezone.utc)
    date_from = (
        datetime.strptime(args.date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        if args.date_from else now - timedelta(days=180)
    )
    date_to = (
        datetime.strptime(args.date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        if args.date_to else now
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f'=== FX Analysis: {date_from.date()} ~ {date_to.date()} ===')
    print('Fetching deals from MT5...')
    df = fetch_deals(date_from, date_to)
    print(f'Total closed trades: {len(df)}')

    # ---- Analysis ----
    print('\n[1] Strategy Summary')
    strat_df = analyze_by_strategy(df)
    print(strat_df.to_string())

    print('\n[2] Symbol Summary')
    sym_df = analyze_by_symbol(df)
    print(sym_df.to_string())

    print('\n[3] Hourly (UTC)')
    hour_df = analyze_by_hour(df)
    print(hour_df.to_string())

    print('\n[4] Weekday')
    wd_df = analyze_by_weekday(df)
    print(wd_df.to_string())

    print('\n[5] Monthly')
    mo_df = analyze_monthly(df)
    print(mo_df.to_string())

    print('\n[6] Strategy x Symbol')
    ss_df = analyze_strategy_symbol(df)
    print(ss_df.to_string())

    # ---- CSV export ----
    csv_map = {
        'summary_strategy.csv': strat_df,
        'summary_symbol.csv': sym_df,
        'summary_hourly.csv': hour_df,
        'summary_weekday.csv': wd_df,
        'summary_monthly.csv': mo_df,
        'summary_strategy_symbol.csv': ss_df,
    }
    for fname, data in csv_map.items():
        if not data.empty:
            data.to_csv(os.path.join(OUTPUT_DIR, fname), encoding='utf-8-sig')

    # ---- Plots ----
    print('\nGenerating charts...')
    strat_hour_df = analyze_strategy_hour(df)
    plot_equity_curve(df)
    plot_strategy_summary(strat_df)
    plot_hourly_heatmap(strat_hour_df)
    plot_weekday_bar(wd_df)
    plot_monthly_bar(mo_df)
    plot_symbol_summary(sym_df)
    plot_rr_distribution(df)

    print(f'\nDone. Output: {OUTPUT_DIR}')


if __name__ == '__main__':
    main()