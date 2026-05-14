"""
daily_review.py - history.csvから当日・直近パフォーマンスをターミナル表示

Usage:
    python optimizer/daily_review.py            # 当日(JST)
    python optimizer/daily_review.py --days 7   # 直近7日
    python optimizer/daily_review.py --date 2026-05-14
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

HISTORY_CSV = Path(__file__).parent / 'history.csv'
JST = timezone(timedelta(hours=9))

MAGIC_MAP = {
    20250001: 'BB',
    20250002: 'SMC_GBPAUD',
    20260001: 'stat_arb',
    20260010: 'SMA_SQ',
}


def load_history() -> pd.DataFrame:
    if not HISTORY_CSV.exists():
        print(f'[ERROR] {HISTORY_CSV} が見つかりません。VPSでsync_history.pyを実行してgit pullしてください。')
        sys.exit(1)
    df = pd.read_csv(HISTORY_CSV)
    df['close_time'] = pd.to_datetime(df['close_time'])
    df['open_time']  = pd.to_datetime(df['open_time'])
    # JSTに変換（naive → UTC → JST）
    df['close_jst'] = df['close_time'].dt.tz_localize('UTC').dt.tz_convert(JST)
    df['date_jst']  = df['close_jst'].dt.date
    df['strategy']  = df['magic'].map(MAGIC_MAP).fillna(df['magic'].astype(str))
    return df


def pf(wins, losses):
    return wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else float('inf')


def print_summary(df: pd.DataFrame, label: str):
    if df.empty:
        print(f'\n[{label}] データなし')
        return

    total = df['profit'].sum()
    wins  = df[df['profit'] > 0]
    losses= df[df['profit'] < 0]

    print(f'\n{"="*52}')
    print(f'  {label}  ({df["date_jst"].min()} 〜 {df["date_jst"].max()})')
    print(f'{"="*52}')
    print(f'  総損益: {total:+,.0f}円   n={len(df)}   '
          f'PF={pf(wins["profit"], losses["profit"]):.3f}   '
          f'WR={len(wins)/len(df)*100:.1f}%')
    print()

    # ペア別
    print('  ペア別:')
    print(f'  {"ペア":<10} {"n":>4} {"PF":>6} {"WR":>6} {"損益":>10}')
    print(f'  {"-"*42}')
    for sym, g in df.groupby('symbol'):
        w = g[g['profit'] > 0]
        l = g[g['profit'] < 0]
        print(f'  {sym:<10} {len(g):>4} {pf(w["profit"], l["profit"]):>6.3f} '
              f'{len(w)/len(g)*100:>5.1f}% {g["profit"].sum():>+10,.0f}円')

    # 戦略別（複数ある場合のみ）
    strategies = df['strategy'].unique()
    if len(strategies) > 1:
        print()
        print('  戦略別:')
        for strat, g in df.groupby('strategy'):
            w = g[g['profit'] > 0]
            l = g[g['profit'] < 0]
            print(f'  {strat:<12} n={len(g):>3}  PF={pf(w["profit"], l["profit"]):.3f}  '
                  f'損益={g["profit"].sum():+,.0f}円')

    # 日次損益（複数日の場合）
    dates = df['date_jst'].unique()
    if len(dates) > 1:
        print()
        print('  日次損益:')
        daily = df.groupby('date_jst')['profit'].sum()
        for date, profit in daily.items():
            sign = '+' if profit >= 0 else ''
            print(f'  {date}  {sign}{profit:,.0f}円')

    print()


def main():
    ap = argparse.ArgumentParser(description='当日・直近パフォーマンスを表示')
    ap.add_argument('--days',  type=int, help='直近N日（省略時は当日JST）')
    ap.add_argument('--date',  help='指定日 YYYY-MM-DD（省略時は当日JST）')
    ap.add_argument('--all',   action='store_true', help='全期間')
    args = ap.parse_args()

    df = load_history()
    now_jst = datetime.now(JST)

    if args.all:
        subset = df
        label  = '全期間'
    elif args.days:
        cutoff = (now_jst - timedelta(days=args.days)).date()
        subset = df[df['date_jst'] >= cutoff]
        label  = f'直近{args.days}日'
    elif args.date:
        target = datetime.strptime(args.date, '%Y-%m-%d').date()
        subset = df[df['date_jst'] == target]
        label  = str(target)
    else:
        today  = now_jst.date()
        subset = df[df['date_jst'] == today]
        label  = f'本日 {today}'

    print_summary(subset, label)

    # history.csv の最終更新を表示
    mtime = HISTORY_CSV.stat().st_mtime
    mtime_jst = datetime.fromtimestamp(mtime, tz=JST).strftime('%Y-%m-%d %H:%M JST')
    print(f'  history.csv 最終更新: {mtime_jst}')
    print(f'  全期間レコード数: {len(df)}件  ({df["date_jst"].min()} 〜 {df["date_jst"].max()})')
    print()


if __name__ == '__main__':
    main()
