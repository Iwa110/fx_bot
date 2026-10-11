"""check_dukas_fresh.py - exit 0 if the last bar of every data/<PAIR>_1h_dukas.csv is within N days.

Used by vps/update_dukas_weekly.bat before pushing refreshed Dukascopy data
(loop/run_weekly.py SKIPs when any search-space pair is older than max_data_age_days).

Usage: python vps/check_dukas_fresh.py [--days 7] [--pairs AUDCAD CADCHF AUDNZD EURGBP]
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def last_bar(path):
    with open(path, 'rb') as f:
        f.seek(max(path.stat().st_size - 4096, 0))
        last = f.read().decode(errors='ignore').strip().splitlines()[-1]
    return datetime.strptime(last.split(',')[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=7.0)
    ap.add_argument('--pairs', nargs='+', default=['AUDCAD', 'CADCHF', 'AUDNZD', 'EURGBP'])
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    ok = True
    for pair in args.pairs:
        path = DATA_DIR / f'{pair}_1h_dukas.csv'
        if not path.exists():
            print(f'[NG] {pair}: {path.name} missing')
            ok = False
            continue
        ts = last_bar(path)
        age = (now - ts).total_seconds() / 86400
        flag = 'OK' if age <= args.days else 'NG'
        ok = ok and flag == 'OK'
        print(f'[{flag}] {pair}: last={ts:%Y-%m-%d %H:%M} age={age:.1f}d (limit {args.days}d)')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
