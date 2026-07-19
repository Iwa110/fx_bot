"""data_loader.py - loads pair OHLC with a real-data-first, honest-fallback policy.

Prefers data/<PAIR>_1h_dukas.csv (Dukascopy 11yr, the project's real-data
source per grid_loop_engineering_design.md sec "long-term data"). Falls back
to the legacy data/<PAIR>_1h.csv (2yr, yfinance) ONLY for mechanism/E2E
testing, and always reports which source was used and whether it spans
enough history for a real IS(2015-2021)/OOS(2022-2026) split, so callers can
flag insufficient_data instead of silently running a partial gate.

Does not import or touch grid_floatstop_bt.py's load_data (frozen file) -
this is an independent loader that produces an equivalent DataFrame shape
(datetime index, open/high/low/close, UTC, sorted, no NaN).
"""

from pathlib import Path

import pandas as pd

IS_START = '2015-01-01'
IS_END = '2021-12-31'
OOS_START = '2022-01-01'

# fetch_dukascopy_ohlc.py pulls N years back from "today", so the earliest
# bar drifts a bit past 2015-01-01 depending on fetch date (e.g. fetching on
# 2026-07-19 with --years 11 yields a 2015-07-22 start). This matches every
# existing project BT (grid_atr_optimize.py etc. all just mask df >=
# IS_START, which is a no-op when df already starts after IS_START) - it is
# not a real data gap, so sufficiency is judged against this tolerance
# rather than requiring an exact 2015-01-01 first bar.
IS_START_TOLERANCE = '2016-01-01'


def _default_data_dir():
    return Path(__file__).resolve().parent.parent.parent / 'data'


def load_pair(pair, data_dir=None):
    """Returns (df, meta) where meta = {'source': 'dukas_11yr'|'legacy_2yr',
    'start': ts, 'end': ts, 'sufficient_for_is_oos': bool}."""
    data_dir = Path(data_dir) if data_dir else _default_data_dir()
    dukas_path = data_dir / f'{pair}_1h_dukas.csv'
    legacy_path = data_dir / f'{pair}_1h.csv'

    if dukas_path.exists():
        df = pd.read_csv(dukas_path)
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
        df = df.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()
        source = 'dukas_11yr'
    elif legacy_path.exists():
        df = pd.read_csv(legacy_path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[['open', 'high', 'low', 'close']].sort_index().dropna()
        source = 'legacy_2yr'
    else:
        raise FileNotFoundError(
            f'No data found for {pair}: tried {dukas_path.name} and {legacy_path.name} in {data_dir}'
        )

    start, end = df.index[0], df.index[-1]
    sufficient = (start <= pd.Timestamp(IS_START_TOLERANCE, tz='UTC')) and (end >= pd.Timestamp(OOS_START, tz='UTC'))

    meta = {
        'source': source,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'n_bars': len(df),
        'sufficient_for_is_oos': bool(sufficient),
    }
    return df, meta


def split_is_oos(df):
    """Splits a full-history df into (df_is, df_oos) per the frozen windows."""
    is_mask = (df.index >= pd.Timestamp(IS_START, tz='UTC')) & (df.index <= pd.Timestamp(IS_END, tz='UTC'))
    oos_mask = df.index >= pd.Timestamp(OOS_START, tz='UTC')
    return df[is_mask], df[oos_mask]
