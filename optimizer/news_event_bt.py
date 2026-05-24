"""
news_event_bt.py - 経済指標戦略(B+C複合) バックテスト
===========================================================
戦略概要:
  B条件: サプライズZスコアが surprise_z_th 以上 (方向一致)
          surprise_raw = actual - forecast
          surprise_z  = surprise_raw / std(過去 surprise_window 回の surprise_raw)
          forecast が NaN の指標は B 条件をスキップ -> C 条件のみで発動
  C条件: 発表後 delay_min 分後の値動きが move_th pips 以上 (方向が B と一致)

エントリー: B AND C (forecast あり) / C のみ (forecast なし)
決済:
  - TP = move_th × rr pips
  - SL = sl_pips (固定)
  - 最大保有 = hold_max_min 分 (強制決済)

対象指標・ペア:
  NFP      -> USDJPY (USD サプライズ+  -> LONG)
  US_CPI   -> USDJPY (USD サプライズ+  -> LONG)
  US_CPI   -> EURUSD (USD サプライズ+  -> SHORT)
  GB_CPI   -> GBPUSD (GBP サプライズ+  -> LONG)
  GB_CPI   -> GBPJPY (GBP サプライズ+  -> LONG)

入力:
  data/news_events.csv   ... ForexFactory 形式 (date, time[ET], currency, event, actual, forecast, previous)
  data/{PAIR}_M1.csv     ... M1 足価格データ (存在しない場合は 5m 足で代替)

出力:
  optimizer/news_bt_result.csv

Usage:
  python news_event_bt.py [--events data/news_events.csv] [--verbose]

依存:
  pandas, numpy, pytz (or zoneinfo)
"""

import argparse
import itertools
import re
import warnings
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ===== パス設定 =====
ROOT_DIR   = Path(__file__).parent.parent
DATA_DIR   = ROOT_DIR / 'data'
OUTPUT_DIR = Path(__file__).parent
OUTPUT_CSV = str(OUTPUT_DIR / 'news_bt_result.csv')

# ===== 戦略設定: 指標→ペア・方向 =====
# event_key: ForexFactory event 列のキーワード (部分一致)
# sign: +1 = サプライズ+ -> LONG / -1 = サプライズ+ -> SHORT
EVENT_PAIR_MAP = [
    {'event_key': 'Non-Farm',  'currency': 'USD', 'pair': 'USDJPY', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'USD', 'pair': 'USDJPY', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'USD', 'pair': 'EURUSD', 'sign': -1},
    {'event_key': 'CPI',       'currency': 'GBP', 'pair': 'GBPUSD', 'sign': +1},
    {'event_key': 'CPI',       'currency': 'GBP', 'pair': 'GBPJPY', 'sign': +1},
]

# ===== pip 単位 =====
PIP_UNIT = {
    'USDJPY': 0.01,
    'EURUSD': 0.0001,
    'GBPUSD': 0.0001,
    'GBPJPY': 0.01,
}

# ===== スリッページ (pips) - 指標発表時想定スプレッド =====
SLIPPAGE_PIPS = {
    'USDJPY': 3.0,
    'EURUSD': 2.0,
    'GBPUSD': 2.5,
    'GBPJPY': 4.0,
}

# ===== パラメータグリッド =====
PARAM_GRID = {
    'delay_min':       [1, 2, 3, 5],
    'move_th_pips':    [3.0, 5.0, 8.0, 10.0],
    'surprise_z_th':   [0.5, 1.0, 1.5, 2.0],
    'sl_pips':         [5.0, 8.0, 12.0],
    'rr':              [1.5, 2.0, 3.0],
    'hold_max_min':    [30, 60, 120],
    'surprise_window': [12, 24, 36],
}

# ===== 採用基準 =====
MIN_PF   = 1.3
MIN_N    = 15
MAX_DD   = 20.0   # %

# ===== バックテスト期間 =====
# 1h データが 2024-04-24 から。5m は 2026-02 から。M1 は VPS のみ。
# 利用可能な最長期間に合わせて自動調整: 価格データの最古日を基準にする
BT_FROM = '2024-05-01'   # 1h データカバレッジに合わせる
BT_TO   = '2026-12-31'


# ---------------------------------------------------------------------------
# ユーティリティ関数
# ---------------------------------------------------------------------------

def parse_economic_value(s) -> float | None:
    """
    経済指標の文字列値を float に変換する。
    "280K" -> 280000 / "0.3%" -> 0.3 / "-0.1%" -> -0.1
    "1.25T" -> 1250000 / "2.1B" -> 2100000
    NaN / None / "" -> None
    """
    if s is None:
        return None
    if isinstance(s, float):
        return None if np.isnan(s) else s
    if isinstance(s, (int, np.integer)):
        return float(s)
    s = str(s).strip()
    if s in ('', 'nan', 'NaN', 'N/A', '-'):
        return None
    # 符号
    sign = -1.0 if s.startswith('-') else 1.0
    s_clean = s.lstrip('+-')
    # % を除去して後で戻す
    is_pct = s_clean.endswith('%')
    if is_pct:
        s_clean = s_clean[:-1]
    # K/M/B/T サフィックス
    multiplier = 1.0
    suffix_map = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    if s_clean and s_clean[-1].upper() in suffix_map:
        multiplier = suffix_map[s_clean[-1].upper()]
        s_clean = s_clean[:-1]
    try:
        val = float(s_clean) * multiplier * sign
        return val   # % の場合も数値そのまま返す (0.3% -> 0.3)
    except ValueError:
        return None


def et_to_utc(date_str: str, time_str: str) -> pd.Timestamp | None:
    """
    ForexFactory の日付・時刻 (ET) を UTC に変換する。
    夏時間 (EDT=UTC-4) / 冬時間 (EST=UTC-5) を自動判定。
    date_str: 'YYYY-MM-DD'
    time_str: 'HH:MM'
    """
    try:
        import pytz
        et = pytz.timezone('America/New_York')
        naive = datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
        localized = et.localize(naive, is_dst=None)
        utc_ts = localized.astimezone(pytz.utc)
        return pd.Timestamp(utc_ts).tz_localize(None)
    except ImportError:
        pass
    try:
        from zoneinfo import ZoneInfo
        import pytz
    except ImportError:
        pass
    # フォールバック: 4月〜10月第2日曜後=EDT(UTC-4)、それ以外=EST(UTC-5)
    naive = datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
    month = naive.month
    if 4 <= month <= 10:
        offset = 4
    else:
        offset = 5
    utc_naive = naive + timedelta(hours=offset)
    return pd.Timestamp(utc_naive)


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def fetch_m1_data(pair: str) -> pd.DataFrame | None:
    """
    M1 足データを取得。利用可能な全時間足をマージして最大カバレッジを確保する。
    優先度: M1 > 5m > 1h (同一時刻では細かい時間足を優先)
    Returns DataFrame with columns [time(UTC naive), open, high, low, close]
    time 列は index ではなく通常列。
    """
    frames = []

    # 1h (最長カバレッジ 2024-04〜)
    h1_path = DATA_DIR / f'{pair}_1h.csv'
    if h1_path.exists():
        df = _load_price_csv(h1_path, '1h')
        if df is not None and len(df) > 0:
            frames.append(df)

    # 5m (より細かい粒度 2026-02〜)
    m5_path = DATA_DIR / f'{pair}_5m.csv'
    if m5_path.exists():
        df = _load_price_csv(m5_path, '5m')
        if df is not None and len(df) > 0:
            frames.append(df)

    # M1 CSV (VPS 保存済みの場合)
    m1_path = DATA_DIR / f'{pair}_M1.csv'
    if m1_path.exists():
        df = _load_price_csv(m1_path, 'M1')
        if df is not None and len(df) > 0:
            frames.append(df)

    if frames:
        # マージ: 全時間足を結合し、同一時刻は後に追加した細かい足を優先
        merged = pd.concat(frames, ignore_index=True)
        # 時刻でソート後、重複時刻は最後の行(より細かい足)を残す
        merged = merged.sort_values('time').drop_duplicates(subset=['time'], keep='last')
        merged = merged.reset_index(drop=True)
        t_min = merged['time'].min().strftime('%Y-%m-%d')
        t_max = merged['time'].max().strftime('%Y-%m-%d')
        print(f'  [data] {pair} merged: {len(merged)} bars ({t_min}~{t_max})')
        return merged

    # MT5 API (VPS 環境)
    df = _fetch_m1_from_mt5(pair)
    if df is not None:
        out = DATA_DIR / f'{pair}_M1.csv'
        df.to_csv(out, index=False)
        print(f'  [data] {pair} M1 saved: {out}')
        return df

    print(f'  [WARN] {pair}: 価格データが見つかりません。スキップ。')
    return None


def _load_price_csv(path: Path, label: str) -> pd.DataFrame | None:
    """既存価格 CSV をロードして正規化する。"""
    try:
        df = pd.read_csv(path, index_col=0)
        # time 列を確保: index_col=0 で読み込むと index に timestamp が入る
        if 'time' not in df.columns:
            # index をリセットして time 列に変換
            df = df.reset_index()
            # index 列名が None / 空の場合でもリネーム
            first_col = df.columns[0]
            if first_col != 'time':
                df = df.rename(columns={first_col: 'time'})
        # timezone-aware -> naive UTC
        df['time'] = pd.to_datetime(df['time'], utc=True, errors='coerce').dt.tz_localize(None)
        # 小文字正規化 + 重複カラム排除 (Open/open 両方ある場合など)
        df.columns = [c.lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated(keep='first')]
        needed = ['time', 'open', 'high', 'low', 'close']
        for col in needed:
            if col not in df.columns:
                return None
        df = df[needed].dropna(subset=['open', 'high', 'low', 'close'])
        df = df.dropna(subset=['time'])
        return df.sort_values('time').reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] load {label} failed: {e}')
        return None


def _fetch_m1_from_mt5(pair: str) -> pd.DataFrame | None:
    """MT5 API 経由で M1 データを取得 (VPS 専用)。"""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        from datetime import timezone
        utc_now = datetime.now(timezone.utc)
        utc_from = utc_now - timedelta(days=365 * 2)
        rates = mt5.copy_rates_range(
            pair, mt5.TIMEFRAME_M1,
            utc_from, utc_now
        )
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
        return df[['time', 'open', 'high', 'low', 'close']].copy()
    except ImportError:
        return None
    except Exception as e:
        print(f'  [WARN] MT5 fetch {pair}: {e}')
        return None


# ---------------------------------------------------------------------------
# ニュースイベント読み込み
# ---------------------------------------------------------------------------

def load_news_events(csv_path: str) -> pd.DataFrame:
    """
    ForexFactory 形式 CSV を読み込み、UTC タイムスタンプと数値パースを追加。
    必須列: date, time, currency, event, actual, forecast, previous
    """
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]

    # 数値パース
    for col in ['actual', 'forecast', 'previous']:
        if col in df.columns:
            df[f'{col}_val'] = df[col].apply(parse_economic_value)
        else:
            df[f'{col}_val'] = np.nan

    # ET -> UTC 変換
    utc_times = []
    for _, row in df.iterrows():
        ts = et_to_utc(str(row['date']), str(row['time']))
        utc_times.append(ts)
    df['utc_time'] = utc_times

    df = df.dropna(subset=['utc_time']).sort_values('utc_time').reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# サプライズ Z スコア計算
# ---------------------------------------------------------------------------

def compute_surprise_z(events_df: pd.DataFrame, window: int) -> pd.Series:
    """
    surprise_raw = actual_val - forecast_val
    surprise_z   = surprise_raw / rolling std (同一指標の過去 window 回)

    【重要】Z スコアは同一指標種別 (event_key x currency) 内で計算する。
    異なる指標 (NFP: 単位=千件, CPI: 単位=%) を混ぜて計算しないこと。

    forecast が NaN の行は NaN を返す。
    """
    df = events_df.copy()
    df['surprise_raw'] = df['actual_val'] - df['forecast_val']
    df.loc[df['forecast_val'].isna(), 'surprise_raw'] = np.nan

    # 指標種別を event 列 + currency 列で識別
    # EVENT_PAIR_MAP の event_key でマッチさせる (簡易実装)
    EVENT_KEYS = ['Non-Farm', 'CPI', 'Unemployment', 'PPI', 'PCE', 'GDP', 'Retail']

    def get_event_key(event_str):
        for key in EVENT_KEYS:
            if key.lower() in str(event_str).lower():
                return key
        return str(event_str)[:20]   # 先頭20文字をキーに

    df['_event_key'] = df['event'].apply(get_event_key)
    df['_group'] = df['currency'].str.upper() + '_' + df['_event_key']

    z_out = pd.Series(np.nan, index=df.index)

    for group, grp_df in df.groupby('_group', sort=False):
        grp_idx = grp_df.index.tolist()
        for pos, idx in enumerate(grp_idx):
            sr = grp_df.loc[idx, 'surprise_raw']
            if pd.isna(sr):
                continue
            # 同一グループの過去 window 件 (自分を除く)
            past_idx = grp_idx[max(0, pos - window):pos]
            past = grp_df.loc[past_idx, 'surprise_raw'].dropna()
            if len(past) < 3:
                continue
            std_val = past.std(ddof=1)
            if std_val < 1e-9:
                continue
            z_out.loc[idx] = float(sr / std_val)

    return z_out


# ---------------------------------------------------------------------------
# 個別トレードシミュレーション
# ---------------------------------------------------------------------------

def simulate_trade(
    price_df: pd.DataFrame,
    entry_utc: pd.Timestamp,
    direction: int,       # +1=LONG / -1=SHORT
    pair: str,
    delay_min: int,
    move_th_pips: float,
    sl_pips: float,
    rr: float,
    hold_max_min: int,
) -> dict | None:
    """
    entry_utc から delay_min 後の値動きを確認し、条件を満たせばエントリー。
    Returns: dict with trade info / None if entry condition not met.
    """
    pip = PIP_UNIT[pair]
    slip = SLIPPAGE_PIPS[pair] * pip
    move_th = move_th_pips * pip
    sl_dist  = sl_pips * pip
    tp_dist  = move_th_pips * rr * pip

    # delay_min 後の参照バー
    ref_time = entry_utc + pd.Timedelta(minutes=delay_min)

    # price_df から該当バーを検索
    mask = price_df['time'] >= ref_time
    if not mask.any():
        return None
    ref_idx = price_df.index[mask][0]
    ref_bar = price_df.loc[ref_idx]

    # 発表前の終値 (delay_min 前の直近バー)
    mask_pre = price_df['time'] < entry_utc
    if not mask_pre.any():
        return None
    pre_idx = price_df.index[mask_pre][-1]
    pre_close = price_df.loc[pre_idx, 'close']
    ref_close = ref_bar['close']

    # C条件: 値動きが direction と一致し move_th 以上
    price_move = (ref_close - pre_close) * direction
    if price_move < move_th:
        return None   # C条件不成立

    # エントリー価格 (スリッページ考慮)
    if direction == +1:
        entry_px = ref_close + slip
        tp_px    = entry_px + tp_dist
        sl_px    = entry_px - sl_dist
    else:
        entry_px = ref_close - slip
        tp_px    = entry_px - tp_dist
        sl_px    = entry_px + sl_dist

    # 保有シミュレーション
    end_time = ref_bar['time'] + pd.Timedelta(minutes=hold_max_min)
    future = price_df[price_df['time'] > ref_bar['time']].copy()

    exit_px    = None
    exit_time  = None
    exit_reason = 'hold_max'

    for _, bar in future.iterrows():
        if bar['time'] > end_time:
            # 強制決済: バー終値で
            exit_px     = bar['close']
            exit_time   = bar['time']
            exit_reason = 'hold_max'
            break
        if direction == +1:
            if bar['low'] <= sl_px:
                exit_px     = sl_px
                exit_time   = bar['time']
                exit_reason = 'SL'
                break
            if bar['high'] >= tp_px:
                exit_px     = tp_px
                exit_time   = bar['time']
                exit_reason = 'TP'
                break
        else:
            if bar['high'] >= sl_px:
                exit_px     = sl_px
                exit_time   = bar['time']
                exit_reason = 'SL'
                break
            if bar['low'] <= tp_px:
                exit_px     = tp_px
                exit_time   = bar['time']
                exit_reason = 'TP'
                break

    if exit_px is None:
        # データ末尾で未決済 -> 最終バー終値
        if len(future) > 0:
            last = future.iloc[-1]
            exit_px    = last['close']
            exit_time  = last['time']
            exit_reason = 'eod'
        else:
            return None

    pnl_pips = (exit_px - entry_px) * direction / pip
    return {
        'entry_time':   ref_bar['time'],
        'exit_time':    exit_time,
        'direction':    'LONG' if direction == +1 else 'SHORT',
        'entry_px':     entry_px,
        'exit_px':      exit_px,
        'pnl_pips':     pnl_pips,
        'exit_reason':  exit_reason,
        'price_move':   price_move / pip,   # pips
    }


# ---------------------------------------------------------------------------
# 評価関数
# ---------------------------------------------------------------------------

def calc_metrics(trades: list[dict]) -> dict:
    """PF / WR / n / MaxDD / Sharpe を計算する。"""
    if not trades:
        return {'pf': 0.0, 'wr': 0.0, 'n': 0, 'max_dd': 0.0,
                'max_dd_pct': 0.0, 'sharpe': 0.0, 'total_pips': 0.0}

    pnls = [t['pnl_pips'] for t in trades]
    n    = len(pnls)
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]

    gross_win  = sum(wins) if wins else 0.0
    gross_loss = abs(sum(loss)) if loss else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    wr = len(wins) / n

    # MaxDD (pips ベース累積資産)
    equity = np.cumsum(pnls)
    peak   = np.maximum.accumulate(equity)
    dd_arr = (peak - equity)
    max_dd = float(dd_arr.max()) if len(dd_arr) > 0 else 0.0

    # MaxDD % (初期資産100として)
    initial = 100.0
    max_dd_pct = max_dd / initial * 100 if initial > 0 else 0.0

    # Sharpe (日次 pnl)
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252))
    else:
        sharpe = 0.0

    return {
        'pf':       round(pf, 3),
        'wr':       round(wr, 3),
        'n':        n,
        'max_dd':   round(max_dd, 2),
        'max_dd_pct': round(max_dd_pct, 2),
        'sharpe':   round(sharpe, 3),
        'total_pips': round(sum(pnls), 2),
    }


# ---------------------------------------------------------------------------
# メインバックテスト
# ---------------------------------------------------------------------------

def run_bt(
    events_df: pd.DataFrame,
    price_cache: dict,
    params: dict,
    verbose: bool = False,
) -> dict:
    """
    1パラメータセットでバックテストを実行する。
    events_df: load_news_events() の出力に surprise_z / event_id を付加済み
    price_cache: {pair: DataFrame}
    """
    delay_min      = params['delay_min']
    move_th_pips   = params['move_th_pips']
    surprise_z_th  = params['surprise_z_th']
    sl_pips        = params['sl_pips']
    rr             = params['rr']
    hold_max_min   = params['hold_max_min']

    all_trades   = []
    trades_b_on  = []   # B条件あり
    trades_b_off = []   # C条件のみ (forecast NaN)
    trades_by_event = {}

    for cfg in EVENT_PAIR_MAP:
        pair = cfg['pair']
        if pair not in price_cache:
            continue

        price_df = price_cache[pair]
        evt_currency = cfg['currency']
        evt_keyword  = cfg['event_key']
        sign         = cfg['sign']

        # 対象イベント行を絞り込む
        mask = (
            (events_df['currency'].str.upper() == evt_currency)
            & (events_df['event'].str.contains(evt_keyword, case=False, na=False))
        )
        sub = events_df[mask].copy()

        for _, row in sub.iterrows():
            utc_ts = row['utc_time']

            # BT 期間フィルター
            if str(utc_ts)[:10] < BT_FROM or str(utc_ts)[:10] > BT_TO:
                continue

            # B条件の判定
            surprise_z = row.get('surprise_z', np.nan)
            forecast_v = row.get('forecast_val', np.nan)
            surprise_r = row.get('surprise_raw', np.nan)
            b_active   = False
            b_skip_no_forecast = pd.isna(forecast_v)

            if not b_skip_no_forecast:
                if pd.isna(surprise_z):
                    continue   # z スコア計算不能 -> スキップ
                # B条件: abs(surprise_z) >= threshold
                # 正負どちらのサプライズでも abs(z) が閾値超えならエントリー
                if abs(surprise_z) >= surprise_z_th:
                    b_active = True
                else:
                    # B条件不成立かつ forecast あり -> エントリーしない
                    continue

            # direction: サプライズ方向 × sign で決定
            # forecast NaN の場合は C 条件の値動き方向で後から確認 (sign で仮置き)
            if b_skip_no_forecast:
                direction = sign   # C 条件で方向確認するので sign を仮置き
            else:
                # positive surprise (actual > forecast) -> sign 方向
                # negative surprise (actual < forecast) -> -sign 方向
                direction = sign if surprise_r >= 0 else -sign

            # シミュレーション
            result = simulate_trade(
                price_df  = price_df,
                entry_utc = utc_ts,
                direction = direction,
                pair      = pair,
                delay_min       = delay_min,
                move_th_pips    = move_th_pips,
                sl_pips         = sl_pips,
                rr              = rr,
                hold_max_min    = hold_max_min,
            )
            if result is None:
                continue

            # イベント種別タグ
            if 'Non-Farm' in str(row.get('event', '')):
                event_tag = 'NFP'
            elif 'CPI' in str(row.get('event', '')):
                event_tag = 'CPI_USD' if evt_currency == 'USD' else 'CPI_GBP'
            else:
                event_tag = evt_keyword

            result['pair']       = pair
            result['event']      = event_tag
            result['b_active']   = b_active
            result['surprise_z'] = surprise_z if not pd.isna(surprise_z) else None

            all_trades.append(result)
            if b_active:
                trades_b_on.append(result)
            else:
                trades_b_off.append(result)
            trades_by_event.setdefault(event_tag, []).append(result)

            if verbose:
                z_str = f'{surprise_z:.2f}' if (
                    isinstance(surprise_z, float) and not np.isnan(surprise_z)
                ) else 'NaN'
                print(
                    f"  {pair} {event_tag} {utc_ts.strftime('%Y-%m-%d %H:%M')} "
                    f"{'LONG' if direction==1 else 'SHORT'} "
                    f"z={z_str} "
                    f"-> {result['exit_reason']} {result['pnl_pips']:+.1f}pips"
                )

    return {
        'all':        all_trades,
        'b_on':       trades_b_on,
        'b_off':      trades_b_off,
        'by_event':   trades_by_event,
    }


# ---------------------------------------------------------------------------
# 分析・出力
# ---------------------------------------------------------------------------

def analyze_and_print(result: dict, params: dict):
    """結果サマリーを表示する。"""
    all_t = result['all']
    m     = calc_metrics(all_t)

    print(f"\n{'='*60}")
    print(f"  全体成績: n={m['n']} PF={m['pf']} WR={m['wr']:.1%} "
          f"DD={m['max_dd']:.1f}pips MaxDD%={m['max_dd_pct']:.1f}%")
    print(f"  Sharpe={m['sharpe']} TotalPips={m['total_pips']:.1f}")

    # 指標別
    print("\n--- 指標別 ---")
    for tag, trades in result['by_event'].items():
        tm = calc_metrics(trades)
        print(f"  {tag:10s}: n={tm['n']:3d} PF={tm['pf']:.3f} WR={tm['wr']:.1%} "
              f"DD={tm['max_dd']:.1f}")

    # B条件あり vs なし
    print("\n--- B条件あり vs C条件のみ ---")
    for label, trades in [('B+C', result['b_on']), ('C only', result['b_off'])]:
        tm = calc_metrics(trades)
        print(f"  {label:8s}: n={tm['n']:3d} PF={tm['pf']:.3f} WR={tm['wr']:.1%}")

    # サプライズ z 別 WR
    print("\n--- サプライズ Z スコア別 WR ---")
    bins = [
        ('z<1',    lambda t: t.get('surprise_z') is not None and abs(t['surprise_z']) < 1),
        ('1<=z<2', lambda t: t.get('surprise_z') is not None and 1 <= abs(t['surprise_z']) < 2),
        ('z>=2',   lambda t: t.get('surprise_z') is not None and abs(t['surprise_z']) >= 2),
    ]
    for label, fn in bins:
        grp = [t for t in all_t if fn(t)]
        if grp:
            gm = calc_metrics(grp)
            print(f"  {label:8s}: n={gm['n']:3d} WR={gm['wr']:.1%} PF={gm['pf']:.3f}")

    # エントリー遅延別 PF (params で固定値のため単一行)
    print(f"\n  delay_min={params['delay_min']} move_th={params['move_th_pips']}pips "
          f"sl={params['sl_pips']} rr={params['rr']} hold={params['hold_max_min']}min")


def grid_search(events_df: pd.DataFrame, price_cache: dict, verbose: bool) -> pd.DataFrame:
    """全パラメータ組み合わせを網羅的に探索する。"""
    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    total  = len(combos)
    print(f'\n[Grid] {total} 組み合わせを探索中...')

    rows = []
    best_pf = 0.0
    best_params = None

    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))

        # surprise_z 計算 (surprise_window ごとに再計算)
        sw = params['surprise_window']
        if 'surprise_z_cache' not in events_df.columns or \
                events_df.attrs.get('sw') != sw:
            events_df['surprise_raw'] = events_df['actual_val'] - events_df['forecast_val']
            events_df['surprise_z']   = compute_surprise_z(events_df, sw)
            events_df.attrs['sw']     = sw

        result  = run_bt(events_df, price_cache, params, verbose=False)
        metrics = calc_metrics(result['all'])

        row = {**params, **metrics,
               'n_b_on': len(result['b_on']),
               'n_b_off': len(result['b_off'])}
        rows.append(row)

        if metrics['pf'] > best_pf and metrics['n'] >= MIN_N:
            best_pf     = metrics['pf']
            best_params = params.copy()
            best_params.update(metrics)

        if i % 500 == 0 or i == total:
            print(f'  {i}/{total} done. best_pf={best_pf:.3f}')

    df_res = pd.DataFrame(rows)
    df_res = df_res.sort_values('pf', ascending=False).reset_index(drop=True)
    return df_res


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='news_event_bt.py - 経済指標 B+C 複合戦略 BT'
    )
    parser.add_argument('--events', default=str(DATA_DIR / 'news_events.csv'),
                        help='イベント CSV パス')
    parser.add_argument('--verbose', action='store_true',
                        help='トレード詳細を表示')
    parser.add_argument('--quick', action='store_true',
                        help='サンプルパラメータのみで高速テスト')
    args = parser.parse_args()

    print('=' * 60)
    print('  news_event_bt.py - 経済指標 B+C 複合戦略 BT')
    print('=' * 60)

    # イベント CSV ロード
    print(f'\n[1] イベントデータ読み込み: {args.events}')
    if not Path(args.events).exists():
        print(f'  ERROR: {args.events} not found.')
        return
    events_df = load_news_events(args.events)
    print(f'  {len(events_df)} 件読み込み')
    print(f'  期間: {events_df["utc_time"].min()} ~ {events_df["utc_time"].max()}')

    # 価格データロード
    print('\n[2] 価格データロード...')
    pairs_needed = list(set(c['pair'] for c in EVENT_PAIR_MAP))
    price_cache = {}
    for pair in pairs_needed:
        df = fetch_m1_data(pair)
        if df is not None:
            price_cache[pair] = df

    if not price_cache:
        print('  ERROR: 価格データが1つも取得できませんでした。')
        return
    print(f'  ロード完了: {list(price_cache.keys())}')

    # Quick テスト or グリッドサーチ
    if args.quick:
        print('\n[3] Quick テスト (固定パラメータ)')
        test_params = {
            'delay_min': 5,
            'move_th_pips': 3.0,
            'surprise_z_th': 0.5,
            'sl_pips': 8.0,
            'rr': 2.0,
            'hold_max_min': 60,
            'surprise_window': 3,   # サンプル小数でも z 計算可能
        }
        events_df['surprise_raw'] = events_df['actual_val'] - events_df['forecast_val']
        events_df['surprise_z']   = compute_surprise_z(events_df, test_params['surprise_window'])
        result = run_bt(events_df, price_cache, test_params, verbose=args.verbose)
        analyze_and_print(result, test_params)

        # トレード一覧を CSV 出力
        if result['all']:
            out_df = pd.DataFrame(result['all'])
            out_path = str(OUTPUT_DIR / 'news_bt_quick_trades.csv')
            out_df.to_csv(out_path, index=False)
            print(f'\n  トレード一覧: {out_path}')
        return

    # フルグリッドサーチ
    print('\n[3] グリッドサーチ開始...')
    df_res = grid_search(events_df, price_cache, verbose=args.verbose)

    # 結果保存
    df_res.to_csv(OUTPUT_CSV, index=False)
    print(f'\n[4] 結果保存: {OUTPUT_CSV}')

    # 採用候補表示
    adopted = df_res[
        (df_res['pf'] >= MIN_PF) &
        (df_res['n']  >= MIN_N)  &
        (df_res['max_dd_pct'] <= MAX_DD)
    ]
    print(f'\n--- 採用候補 (PF>={MIN_PF}, n>={MIN_N}, DD<={MAX_DD}%): {len(adopted)} 件 ---')
    if len(adopted) > 0:
        cols = ['delay_min', 'move_th_pips', 'surprise_z_th', 'sl_pips', 'rr',
                'hold_max_min', 'surprise_window', 'pf', 'wr', 'n', 'max_dd_pct', 'sharpe']
        print(adopted[cols].head(10).to_string(index=False))
    else:
        print('  採用候補なし。')

    # Top5 詳細表示
    print('\n--- Top 5 パラメータ詳細 ---')
    for _, row in df_res.head(5).iterrows():
        params = {k: row[k] for k in PARAM_GRID.keys()}
        events_df['surprise_raw'] = events_df['actual_val'] - events_df['forecast_val']
        events_df['surprise_z']   = compute_surprise_z(events_df, int(row['surprise_window']))
        result = run_bt(events_df, price_cache, params, verbose=False)
        analyze_and_print(result, params)


if __name__ == '__main__':
    main()
