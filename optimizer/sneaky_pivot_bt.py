"""
sneaky_pivot_bt.py - Sneaky Pivot Strategy バックテスト (M15足)

戦略概要:
    前日 OHLC から 4本の水平線(Rumers Magic Lines)を計算し、
    ロンドン/NY セッション開始直後の 3本の15分足(3-Candlestick Framework)で
    スニーキーキャンドル + ブレイクアウトエントリーを行う平均回帰戦略。

水平線:
    RH (Range High)  : 前日高値
    RL (Range Low)   : 前日安値
    SH (Swing High)  : RHより過去のフラクタル高値でRHを上回る最直近値
    SL (Swing Low)   : RLより過去のフラクタル安値でRLを下回る最直近値

    Sell Zone = [RH, SH]  /  Buy Zone = [SL, RL]
    中間エリア(RL~RH 間)はノートレード

エントリー (3-Candlestick Framework):
    Long : セッション開始後 3本以内に Buy Zone 到達 + 陽線(Sneaky Candle)
           → 次足以降で Sneaky Candle 高値ブレイクで成行 Long
    Short: セッション開始後 3本以内に Sell Zone 到達 + 陰線(Sneaky Candle)
           → 次足以降で Sneaky Candle 安値ブレイクで成行 Short

SL/TP:
    Long  SL = Sneaky Candle 安値 - sl_buf
    Long  TP = RH (一次ターゲット) / SH (拡張ターゲット, flag で切替)
    Short SL = Sneaky Candle 高値 + sl_buf
    Short TP = RL (一次ターゲット) / SL (拡張ターゲット)

データ:
    data/{SYM}_5m_10y.csv.gz  を 15m にリサンプルして使用

評価:
    IS = 2016-2021  /  OOS = 2022-2026
    指標: n / WR / PF / net_pips / maxDD_pips / avg_rr / Sharpe

★ 検証結果まとめ (2026-06-27):
    改善ループを通じて確定したベスト構成:

    GBPJPY / NYセッション(13:00 UTC) / approach_verify=True / body_filter=True / tp_mode=mid
        IS  PF=1.72  n=57   WR=59.6%  Sharpe=3.11  (2016-2021)
        OOS PF=1.97  n=38   WR=63.2%  Sharpe=3.82  (2022-2026)
        パラメータ頑健性(IS&OOS両方>1.0のプラトー):
          zone_tol=0.05-0.10 x sl_buf=0.05-0.15 全組合せで通過

    approach_verify の効果:
        「NYセッション開始前の4本(=1h)にゾーンタッチが無い場合のみエントリー」
        = 機関投資家のオーダーが"まだ消化されていない"フレッシュな水準に
          NYオープンが到達した時だけ入る。ゾーンが既にロンドンで反応した日は除外。
        フィルタ前後: n=600→95, WR 40%→61%, IS PF 0.77→1.72

    他ペア・他セッションの結果:
        GBPJPY London(8h) + approach_verify: IS PF=1.04, OOS PF=1.15 (n=106, 薄め)
        USDJPY London + approach_verify: IS PF=0.91, OOS PF=0.79 (採用なし)
        EURJPY London + approach_verify: IS PF=1.42, OOS PF=0.95 (IS↔OOSスプリット)
        EURJPY NY + approach_verify: IS PF=1.01, OOS PF=0.60 (採用なし)

    留保事項:
        - n≈95(10.5年) = ~9件/年 と薄い。IS/OOS スプリットで n=57/38。
        - tp_mode=mid にのみ依存(rh_rlおよびhalf はIS PF<1.0)。
        - yk% 変動大(2022=-66pip, 2024=-14pip 等 OOSで負け年あり)。
        - forward-test 最低50件到達まで実投入禁止。

実行例:
    # ベスト構成
    python optimizer/sneaky_pivot_bt.py --pairs GBPJPY --sessions 13 \
        --zone-tol 0.05 --sl-buf 0.15 --tp-mode mid \
        --body-filter --approach-verify --yearly
    # スイープ
    python optimizer/sneaky_pivot_bt.py --pairs GBPJPY --sweep
"""

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

# ── パス設定 ─────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
OUT_DIR  = Path(__file__).resolve().parent

# ── デフォルトパラメータ ─────────────────────────────────────────────────────
SESSION_HOURS         = {'london': 8, 'ny': 13}   # UTC
SESSION_WINDOW_BARS   = 3     # Sneaky Candle 探索ウィンドウ(15m本数)
ENTRY_WINDOW_BARS     = 6     # ブレイクエントリー待機最大本数(=1.5h)
MAX_HOLD_BARS         = 32    # 最大保有本数(=8h)
SWING_LOOKBACK_DAYS   = 20    # スイング高値/安値のフラクタル探索日数
ZONE_TOL_ATR_MULT     = 0.05  # ゾーン到達判定: 前日ATR × この値 をトレランスに
SL_BUF_ATR_MULT       = 0.10  # SLバッファ: 前日ATR × この値
MIN_RR                = 0.8   # 最低リスクリワード比
TP_MODE               = 'mid' # 'rh_rl'=前日逆端ゾーン / 'mid'=レンジ中間点 / 'half'=SL距離×2
MAX_TRADES_PER_DAY    = 1     # 1日最大トレード数

IS_START = pd.Timestamp('2016-01-01')
IS_END   = pd.Timestamp('2021-12-31 23:59:59')
OOS_END  = pd.Timestamp('2026-12-31')

PAIRS_5M = ['USDJPY', 'GBPJPY', 'EURJPY']   # 5m 10y データが存在するペア


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def pip_size(pair: str) -> float:
    return 0.01 if 'JPY' in pair else 0.0001


def spread_cost(pair: str) -> float:
    """往復スプレッドコスト (価格ベース)"""
    pips = 2.0 if 'JPY' in pair else 1.0
    return pips * pip_size(pair)


def load_and_resample(sym: str):
    """5m データを読み込み 15m にリサンプル。日足 OHLC も返す。"""
    path_gz  = DATA_DIR / f'{sym}_5m_10y.csv.gz'
    path_csv = DATA_DIR / f'{sym}_5m.csv'

    if path_gz.exists():
        df5 = pd.read_csv(path_gz, parse_dates=['datetime'])
    elif path_csv.exists():
        df5 = pd.read_csv(path_csv, parse_dates=['datetime'])
    else:
        return None, None

    df5 = (df5.dropna(subset=['open', 'high', 'low', 'close'])
               .sort_values('datetime')
               .drop_duplicates('datetime')
               .reset_index(drop=True))

    # 15m リサンプル (左端ラベル)
    df15 = (df5.set_index('datetime')
               .resample('15min', closed='left', label='left')
               .agg({'open': 'first', 'high': 'max', 'low': 'min',
                     'close': 'last', 'volume': 'sum'})
               .dropna(subset=['open', 'high', 'low', 'close'])
               .reset_index())

    # 日足 (UTC 0:00 区切り)
    dfD = (df5.set_index('datetime')
              .resample('D', closed='left', label='left')
              .agg({'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'})
              .dropna(subset=['open', 'high', 'low', 'close'])
              .reset_index())
    dfD['date'] = dfD['datetime'].dt.normalize()

    return df15, dfD


def calc_daily_atr(dfD: pd.DataFrame, period: int = 14) -> pd.Series:
    """日足 ATR (EWM)"""
    h, l, c = dfD['high'], dfD['low'], dfD['close']
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def fractal_swing_high(highs: pd.Series, rh: float) -> float:
    """highs の中から rh を上回る最直近フラクタル高値を探す。"""
    arr = highs.values
    for i in range(len(arr) - 2, 0, -1):
        h = arr[i]
        if h > rh and h > arr[i - 1] and h > arr[i + 1]:
            return float(h)
    above = highs[highs > rh]
    return float(above.max()) if len(above) > 0 else rh * 1.005


def fractal_swing_low(lows: pd.Series, rl: float) -> float:
    """lows の中から rl を下回る最直近フラクタル安値を探す。"""
    arr = lows.values
    for i in range(len(arr) - 2, 0, -1):
        l = arr[i]
        if l < rl and l < arr[i - 1] and l < arr[i + 1]:
            return float(l)
    below = lows[lows < rl]
    return float(below.min()) if len(below) > 0 else rl * 0.995


def simulate_exit(day15: pd.DataFrame, entry_idx: int,
                  entry_price: float, sl: float, tp: float,
                  direction: str, max_bars: int = MAX_HOLD_BARS):
    """エントリーバー以降を走査して SL / TP / タイムアウトを判定。"""
    n = len(day15)
    for j in range(entry_idx + 1, min(entry_idx + max_bars + 1, n)):
        bar = day15.iloc[j]
        lo, hi = bar['low'], bar['high']

        if direction == 'long':
            if lo <= sl and hi >= tp:
                return sl, bar['datetime'], 'sl'
            if lo <= sl:
                return sl, bar['datetime'], 'sl'
            if hi >= tp:
                return tp, bar['datetime'], 'tp'
        else:
            if hi >= sl and lo <= tp:
                return sl, bar['datetime'], 'sl'
            if hi >= sl:
                return sl, bar['datetime'], 'sl'
            if lo <= tp:
                return tp, bar['datetime'], 'tp'

    last_i = min(entry_idx + max_bars, n - 1)
    last   = day15.iloc[last_i]
    return last['close'], last['datetime'], 'timeout'


# ── メインバックテスト ────────────────────────────────────────────────────────

def run_backtest(sym: str,
                 session_hours=None,
                 zone_tol_atr: float = ZONE_TOL_ATR_MULT,
                 sl_buf_atr: float   = SL_BUF_ATR_MULT,
                 swing_lb: int       = SWING_LOOKBACK_DAYS,
                 min_rr: float       = MIN_RR,
                 tp_mode: str        = TP_MODE,
                 body_filter: bool   = True,
                 wick_filter: bool   = False,
                 approach_verify: bool = False,
                 range_filter: bool  = False,
                 max_tpd: int        = MAX_TRADES_PER_DAY) -> pd.DataFrame:
    """
    1ペア分のバックテストを実行してトレードリストを返す。

    Parameters
    ----------
    body_filter     : Sneaky Candle の終値がバー全体の上60%(long) / 下60%(short)を要求
    wick_filter     : Sneaky Candle の否定ウィック > ボディを要求(pin bar確認)
    approach_verify : セッション開始バーがゾーン外→セッション中にゾーン接触の順序を確認
    range_filter    : 前日レンジが ATR の 0.5~2.5 倍の場合のみ処理
    """
    if session_hours is None:
        session_hours = list(SESSION_HOURS.values())

    pip  = pip_size(sym)
    cost = spread_cost(sym)

    df15, dfD = load_and_resample(sym)
    if df15 is None:
        print(f'  [{sym}] データなし')
        return pd.DataFrame()

    dfD['atr'] = calc_daily_atr(dfD)
    dfD = dfD.reset_index(drop=True)
    date_map = {row['date']: idx for idx, row in dfD.iterrows()}

    trades = []

    for day_i in range(swing_lb + 2, len(dfD)):
        row_today = dfD.iloc[day_i]
        today     = row_today['date']

        if today < IS_START or today > OOS_END:
            continue

        prev = dfD.iloc[day_i - 1]
        atr  = row_today['atr']
        if pd.isna(atr) or atr <= 0:
            continue

        rh = prev['high']
        rl = prev['low']
        dr = rh - rl  # 前日レンジ幅
        if dr <= 0:
            continue

        # 前日レンジ品質フィルター
        if range_filter and (dr < 0.5 * atr or dr > 2.5 * atr):
            continue

        # Swing High / Low
        look = dfD.iloc[max(0, day_i - swing_lb - 1): day_i - 1]
        if len(look) < 3:
            continue
        sh    = fractal_swing_high(look['high'], rh)
        sl_lv = fractal_swing_low(look['low'], rl)

        tol    = atr * zone_tol_atr
        sl_buf = atr * sl_buf_atr

        # 当日の 15m 足
        day15 = df15[
            (df15['datetime'] >= today) &
            (df15['datetime'] <  today + pd.Timedelta(days=1))
        ].reset_index(drop=True)

        if len(day15) < SESSION_WINDOW_BARS + 2:
            continue

        trades_today = 0

        for sess_h in session_hours:
            if trades_today >= max_tpd:
                break

            sess_start = today + pd.Timedelta(hours=sess_h)
            sess_mask  = day15['datetime'] >= sess_start
            if not sess_mask.any():
                continue
            si0 = int(sess_mask.idxmax())

            # ── アプローチ検証 ──
            # セッション開始「前」の 4本が既にゾーン内なら「新鮮なテスト」でない。
            # セッション開始バー自体はゾーン到達可能にする(第1バーがSneaky Candleの場合を許容)。
            if approach_verify and si0 >= 4:
                pre_bars = day15.iloc[si0 - 4: si0]   # セッション直前4本
                pre_in_buy  = (pre_bars['low']  <= rl + tol).any()
                pre_in_sell = (pre_bars['high'] >= rh - tol).any()
                if pre_in_buy or pre_in_sell:
                    continue

            # ── Sneaky Candle 探索 ──
            long_sneaky  = None
            short_sneaky = None

            for ci in range(si0, min(si0 + SESSION_WINDOW_BARS, len(day15))):
                bar = day15.iloc[ci]
                h, l, o, c = bar['high'], bar['low'], bar['open'], bar['close']
                bar_range = h - l
                if bar_range <= 0:
                    continue

                # Long setup (Buy Zone テスト + 陽線反転):
                if long_sneaky is None:
                    in_buy  = (l <= rl + tol)   # low がゾーンに接触
                    bullish = (c > o)            # 陽線
                    rejects = (c >= rl - tol)   # 終値はゾーン上方で引け
                    not_mid = (h <= rh)          # 高値が中間エリア上限を越えない(厳格)

                    # ボディが上60%に位置(強い反転)
                    body_ok = (not body_filter) or (c >= l + 0.6 * bar_range)

                    # 下ウィック(open-low) >= ボディ(close-open): pin bar 判定
                    lower_wick = o - l   # 陽線: lower wick = open - low
                    body_size  = c - o   # 陽線: body = close - open
                    wick_ok    = (not wick_filter) or (lower_wick >= body_size and lower_wick > 0)

                    if in_buy and bullish and rejects and not_mid and body_ok and wick_ok:
                        long_sneaky = (ci, h, l)

                # Short setup (Sell Zone テスト + 陰線反転):
                if short_sneaky is None:
                    in_sell  = (h >= rh - tol)  # high がゾーンに接触
                    bearish  = (c < o)           # 陰線
                    rejects  = (c <= rh + tol)  # 終値はゾーン下方で引け
                    not_mid  = (l >= rl)         # 安値が中間エリア下限を越えない(厳格)

                    body_ok  = (not body_filter) or (c <= h - 0.6 * bar_range)

                    # 上ウィック(high-open) >= ボディ(open-close): shooting star 判定
                    upper_wick = h - o   # 陰線: upper wick = high - open
                    body_size  = o - c   # 陰線: body = open - close
                    wick_ok    = (not wick_filter) or (upper_wick >= body_size and upper_wick > 0)

                    if in_sell and bearish and rejects and not_mid and body_ok and wick_ok:
                        short_sneaky = (ci, h, l)

            # ── エントリー試行 ──
            for direction, sneaky in [('long', long_sneaky), ('short', short_sneaky)]:
                if sneaky is None or trades_today >= max_tpd:
                    continue

                sneaky_ci, s_high, s_low = sneaky

                for bi in range(sneaky_ci + 1,
                                min(sneaky_ci + 1 + ENTRY_WINDOW_BARS, len(day15))):
                    entry_bar = day15.iloc[bi]

                    if direction == 'long':
                        if entry_bar['high'] <= s_high:
                            continue

                        entry_price = s_high + pip
                        sl_price    = s_low - sl_buf
                        sl_dist     = entry_price - sl_price
                        if sl_dist <= 0:
                            break

                        if tp_mode == 'mid':
                            tp_price = rl + 0.5 * dr   # レンジ中間点
                        elif tp_mode == 'half':
                            tp_price = entry_price + 2.0 * sl_dist
                        else:  # 'rh_rl': 対向ゾーン境界
                            tp_price = rh

                        if tp_price <= entry_price or sl_price >= entry_price:
                            break

                        rr = (tp_price - entry_price) / sl_dist
                        if rr < min_rr:
                            break

                    else:   # short
                        if entry_bar['low'] >= s_low:
                            continue

                        entry_price = s_low - pip
                        sl_price    = s_high + sl_buf
                        sl_dist     = sl_price - entry_price
                        if sl_dist <= 0:
                            break

                        if tp_mode == 'mid':
                            tp_price = rh - 0.5 * dr   # レンジ中間点
                        elif tp_mode == 'half':
                            tp_price = entry_price - 2.0 * sl_dist
                        else:  # 'rh_rl'
                            tp_price = rl

                        if tp_price >= entry_price or sl_price <= entry_price:
                            break

                        rr = (entry_price - tp_price) / sl_dist
                        if rr < min_rr:
                            break

                    exit_price, exit_time, exit_type = simulate_exit(
                        day15, bi, entry_price, sl_price, tp_price, direction
                    )

                    pnl = ((exit_price - entry_price) if direction == 'long'
                           else (entry_price - exit_price)) - cost

                    trades.append({
                        'sym':        sym,
                        'entry_time': entry_bar['datetime'],
                        'exit_time':  exit_time,
                        'session':    sess_h,
                        'direction':  direction,
                        'entry':      entry_price,
                        'exit':       exit_price,
                        'sl':         sl_price,
                        'tp':         tp_price,
                        'rh':         rh,
                        'rl':         rl,
                        'sh':         sh,
                        'sl_level':   sl_lv,
                        'result':     exit_type,
                        'pnl':        pnl,
                        'pnl_pips':   pnl / pip,
                        'rr':         rr,
                        'atr':        atr,
                    })
                    trades_today += 1
                    break

    return pd.DataFrame(trades) if trades else pd.DataFrame()


# ── 統計分析 ──────────────────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, sym: str, is_end=IS_END) -> dict:
    """IS / OOS / full の統計を返す"""
    if df.empty:
        return {}

    pip = pip_size(sym)
    df  = df.copy()
    df['period'] = df['entry_time'].apply(
        lambda t: 'IS' if t <= is_end else 'OOS')

    result = {}
    for period in ('full', 'IS', 'OOS'):
        sub = df if period == 'full' else df[df['period'] == period]
        if sub.empty:
            continue

        n   = len(sub)
        win = (sub['pnl'] > 0).sum()
        wr  = win / n

        gp  = sub.loc[sub['pnl'] > 0, 'pnl'].sum()
        gl  = sub.loc[sub['pnl'] < 0, 'pnl'].abs().sum()
        pf  = gp / gl if gl > 0 else np.inf

        net_pips = sub['pnl_pips'].sum()

        cum      = sub['pnl_pips'].cumsum()
        roll_max = cum.cummax()
        max_dd   = (roll_max - cum).max()

        avg_rr   = sub['rr'].mean()
        n_tp     = (sub['result'] == 'tp').sum()
        n_sl     = (sub['result'] == 'sl').sum()
        n_to     = (sub['result'] == 'timeout').sum()
        avg_pnl  = sub['pnl_pips'].mean()
        std_pnl  = sub['pnl_pips'].std()
        sharpe   = avg_pnl / std_pnl * np.sqrt(250) if std_pnl > 0 else 0

        result[period] = dict(
            n=n, wr=wr, pf=pf, net_pips=net_pips,
            max_dd_pips=max_dd, avg_rr=avg_rr, sharpe=sharpe,
            n_tp=n_tp, n_sl=n_sl, n_to=n_to,
        )
    return result


def print_stats(stats: dict, sym: str):
    print(f'\n=== {sym} ===')
    for period, s in stats.items():
        print(f'  {period:4s}: n={s["n"]:4d}  WR={s["wr"]:.1%}  PF={s["pf"]:.2f}  '
              f'net={s["net_pips"]:+.0f}pip  maxDD={s["max_dd_pips"]:.0f}pip  '
              f'RR={s["avg_rr"]:.2f}  Sh={s["sharpe"]:.2f}  '
              f'[TP:{s["n_tp"]} SL:{s["n_sl"]} TO:{s["n_to"]}]')


def yearly_summary(df: pd.DataFrame, sym: str):
    if df.empty:
        return
    df = df.copy()
    df['year'] = df['entry_time'].dt.year
    yg = df.groupby('year')['pnl_pips'].agg(['sum', 'count', lambda x: (x > 0).mean()])
    yg.columns = ['net_pips', 'n', 'wr']
    print(f'  年次 ({sym}):')
    for yr, row in yg.iterrows():
        tag = 'IS ' if yr <= IS_END.year else 'OOS'
        print(f'    {yr} [{tag}]: n={int(row["n"]):3d}  WR={row["wr"]:.0%}  net={row["net_pips"]:+.0f}pip')


# ── パラメータスイープ ────────────────────────────────────────────────────────

def run_sweep(sym: str, verbose: bool = False) -> pd.DataFrame:
    """
    主要パラメータの総当たりスイープ。
    実行時間を抑えるためLondonのみに絞ったコンパクト版。
    """
    # セッションは London / NY / 両方
    sess_options = [[8], [13], [8, 13]]

    grid_keys = ['zone_tol_atr', 'sl_buf_atr', 'tp_mode', 'min_rr',
                 'body_filter', 'wick_filter', 'approach_verify', 'range_filter']
    grid_vals = [
        [0.0, 0.05, 0.10],        # zone_tol_atr
        [0.05, 0.10, 0.15],        # sl_buf_atr
        ['rh_rl', 'mid', 'half'],  # tp_mode
        [0.8, 1.2],                # min_rr
        [True, False],             # body_filter
        [True, False],             # wick_filter
        [True, False],             # approach_verify
        [False],                   # range_filter (固定)
    ]

    rows = []
    total = len(sess_options)
    for v in grid_vals:
        total *= len(v)

    cnt = 0
    for sessions in sess_options:
        for combo in itertools.product(*grid_vals):
            params = dict(zip(grid_keys, combo))
            cnt += 1

            df_tr = run_backtest(
                sym,
                session_hours   = sessions,
                zone_tol_atr    = params['zone_tol_atr'],
                sl_buf_atr      = params['sl_buf_atr'],
                tp_mode         = params['tp_mode'],
                min_rr          = params['min_rr'],
                body_filter     = params['body_filter'],
                wick_filter     = params['wick_filter'],
                approach_verify = params['approach_verify'],
                range_filter    = params['range_filter'],
            )

            if df_tr.empty or len(df_tr) < 20:
                continue

            st = analyze(df_tr, sym)
            if 'IS' not in st or 'OOS' not in st:
                continue

            row = {'sym': sym, 'sessions': str(sessions), **params}
            for p in ('full', 'IS', 'OOS'):
                if p not in st:
                    continue
                for k, v in st[p].items():
                    row[f'{p}_{k}'] = round(v, 4) if isinstance(v, float) else v

            rows.append(row)

            if verbose and cnt % 50 == 0:
                print(f'  [{sym}] sweep {cnt}/{total}...', flush=True)

    return pd.DataFrame(rows)


# ── メイン ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Sneaky Pivot Strategy BT')
    parser.add_argument('--pairs',     nargs='+', default=PAIRS_5M)
    parser.add_argument('--sessions',  nargs='+', type=int,
                        default=list(SESSION_HOURS.values()))
    parser.add_argument('--zone-tol',  type=float, default=ZONE_TOL_ATR_MULT)
    parser.add_argument('--sl-buf',    type=float, default=SL_BUF_ATR_MULT)
    parser.add_argument('--swing-lb',  type=int,   default=SWING_LOOKBACK_DAYS)
    parser.add_argument('--min-rr',    type=float, default=MIN_RR)
    parser.add_argument('--tp-mode',   type=str,   default=TP_MODE,
                        choices=['rh_rl', 'mid', 'half'])
    parser.add_argument('--body-filter',     action='store_true',
                        help='終値がバー上/下60%を要求')
    parser.add_argument('--wick-filter',     action='store_true',
                        help='否定ウィックがボディより長い(pin bar)を要求')
    parser.add_argument('--approach-verify', action='store_true',
                        help='セッション開始時点がゾーン外であることを要求')
    parser.add_argument('--range-filter',    action='store_true',
                        help='前日レンジが ATR の 0.5~2.5 倍のみ処理')
    parser.add_argument('--yearly',    action='store_true')
    parser.add_argument('--sweep',     action='store_true')
    parser.add_argument('--out',       type=str,
                        default=str(OUT_DIR / 'sneaky_pivot_bt_result.csv'))
    args = parser.parse_args()

    if args.sweep:
        print('パラメータスイープ開始...')
        all_sweep = []
        for sym in args.pairs:
            print(f'  [{sym}] スイープ中...')
            df_sw = run_sweep(sym, verbose=True)
            if not df_sw.empty:
                all_sweep.append(df_sw)
                cands = df_sw[
                    (df_sw.get('IS_pf',  pd.Series(0, index=df_sw.index)) > 1.0) &
                    (df_sw.get('OOS_pf', pd.Series(0, index=df_sw.index)) > 1.0)
                ]
                print(f'  [{sym}] {len(df_sw)}通り / 候補(IS&OOS>1.0): {len(cands)}件')
                if len(cands) > 0:
                    top  = cands.sort_values('OOS_pf', ascending=False).head(10)
                    cols = ['sessions', 'zone_tol_atr', 'sl_buf_atr', 'tp_mode',
                            'min_rr', 'body_filter', 'wick_filter', 'approach_verify',
                            'IS_n', 'IS_pf', 'OOS_n', 'OOS_pf', 'full_pf']
                    print(top[[c for c in cols if c in top.columns]].to_string())
                else:
                    # OOS PF 上位10を表示
                    top = df_sw.sort_values('OOS_pf', ascending=False).head(10)
                    cols = ['sessions', 'zone_tol_atr', 'sl_buf_atr', 'tp_mode',
                            'min_rr', 'body_filter', 'wick_filter', 'approach_verify',
                            'IS_n', 'IS_pf', 'OOS_n', 'OOS_pf']
                    print('  OOS PF 上位10:')
                    print(top[[c for c in cols if c in top.columns]].to_string())

        if all_sweep:
            df_all = pd.concat(all_sweep, ignore_index=True)
            sw_path = str(OUT_DIR / 'sneaky_pivot_sweep_result.csv')
            df_all.to_csv(sw_path, index=False)
            print(f'\nスイープ保存: {sw_path}  ({len(df_all)} rows)')

            # 候補まとめ
            if 'IS_pf' in df_all.columns and 'OOS_pf' in df_all.columns:
                cands_all = df_all[(df_all['IS_pf'] > 1.0) & (df_all['OOS_pf'] > 1.0)]
                print(f'\n全ペア IS&OOS PF>1.0 候補: {len(cands_all)}件')
                if len(cands_all) > 0:
                    print(cands_all.sort_values('OOS_pf', ascending=False).head(20).to_string())
        return

    # 通常単発実行
    print(f'Sneaky Pivot BT  zone_tol={args.zone_tol}xATR  sl_buf={args.sl_buf}xATR  '
          f'swing_lb={args.swing_lb}d  min_rr={args.min_rr}  sessions={args.sessions}  '
          f'tp_mode={args.tp_mode}  body_filter={args.body_filter}  '
          f'wick_filter={args.wick_filter}  approach_verify={args.approach_verify}')

    all_trades = []

    for sym in args.pairs:
        df_tr = run_backtest(
            sym,
            session_hours   = args.sessions,
            zone_tol_atr    = args.zone_tol,
            sl_buf_atr      = args.sl_buf,
            swing_lb        = args.swing_lb,
            min_rr          = args.min_rr,
            tp_mode         = args.tp_mode,
            body_filter     = args.body_filter,
            wick_filter     = args.wick_filter,
            approach_verify = args.approach_verify,
            range_filter    = args.range_filter,
        )

        if df_tr.empty:
            print(f'[{sym}] トレードなし')
            continue

        stats = analyze(df_tr, sym)
        print_stats(stats, sym)
        if args.yearly:
            yearly_summary(df_tr, sym)

        all_trades.append(df_tr)

    if not all_trades:
        print('\nトレードがありませんでした。')
        return

    df_all = pd.concat(all_trades, ignore_index=True)
    df_all.to_csv(args.out, index=False)
    print(f'\n結果保存: {args.out}  ({len(df_all)} trades)')


if __name__ == '__main__':
    main()
