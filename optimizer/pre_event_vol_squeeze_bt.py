"""
pre_event_vol_squeeze_bt.py
指標発表前ボラ縮小 x BB逆張り バックテスト v1

戦略:
  - ATR(14,1h) / ATR_20日SMA < VOL_RATIO_THRESH でボラ縮小を検知
  - 指標発表72時間前以内 かつ BB(20, 1.5)上限/下限タッチ でエントリー
  - TP: ATR x TP_ATR_MULT / SL: ATR x SL_ATR_MULT
  - 強制クローズ: 指標発表2時間前（最優先）
  - 1ポジション限定

カレンダー:
  - 固定日近似を使用（2024-2026年）
    NFP: 毎月第1金曜
    CPI: 毎月12日（10〜15日の中央値）
    FOMC: 近似スケジュール（2024年8回 x 2025年8回 x 2026年4回暫定）

使い方:
  python pre_event_vol_squeeze_bt.py              # デフォルト実行
  python pre_event_vol_squeeze_bt.py --sweep      # VOL_RATIO_THRESH x BB_STD 感度分析
"""

import warnings
warnings.filterwarnings("ignore")

import sys
import os
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timezone
import datetime as dt

# =============================================================================
# パラメータ（冒頭定数化）
# =============================================================================
PAIRS = ["EURUSD", "USDJPY"]
PAIR_CONFIG = {
    "EURUSD": {"ticker": "EURUSD=X", "spread_pips": 1.0, "pip": 0.0001},
    "USDJPY": {"ticker": "JPY=X",    "spread_pips": 1.5, "pip": 0.01},
}

VOL_RATIO_THRESH    = 0.7    # ATR縮小判定: ATR_current / ATR_20dSMA < thresh
ATR_PERIOD          = 14     # ATR算出期間（バー数）
ATR_MA_PERIOD       = 20 * 24   # 20日 x 24バー(1h) = 480バー でSMA
BB_PERIOD           = 20    # BB算出期間
BB_STD              = 1.5   # BB標準偏差倍率

TP_ATR_MULT         = 1.0   # TP = ATR x この値
SL_ATR_MULT         = 1.5   # SL = ATR x この値

PRE_EVENT_HOURS     = 72    # エントリー有効期間（指標発表前X時間以内）
CLOSE_BEFORE_HOURS  = 2     # 強制クローズ（指標発表X時間前）

SPREAD_PIPS = {"EURUSD": 1.0, "USDJPY": 1.5}

# yfinance取得期間
HIST_DAYS_1H = 700  # 1h足は730日上限

# 感度分析スイープ範囲
SWEEP_VOL_THRESH = [0.5, 0.6, 0.7, 0.8]
SWEEP_BB_STD     = [1.5, 2.0]

# 出力パス
OUT_DIR       = os.path.dirname(os.path.abspath(__file__))
TRADE_CSV     = os.path.join(OUT_DIR, "pre_event_vol_squeeze_trades.csv")
STATS_CSV     = os.path.join(OUT_DIR, "pre_event_vol_squeeze_stats.csv")
SWEEP_CSV     = os.path.join(OUT_DIR, "pre_event_vol_squeeze_sweep.csv")
CHART_PNG     = os.path.join(OUT_DIR, "pre_event_vol_squeeze_chart.png")

# カレンダー方式（固定日使用の場合は明記）
CALENDAR_METHOD = "FIXED_DATES"  # 固定日近似（investpy取得不可のため）


# =============================================================================
# カレンダー生成
# =============================================================================
def _first_friday(year: int, month: int) -> dt.date:
    """その月の第1金曜日を返す"""
    d = dt.date(year, month, 1)
    # weekday: 0=Mon ... 4=Fri
    offset = (4 - d.weekday()) % 7
    return d + dt.timedelta(days=offset)


def _fomc_dates_approx() -> list:
    """
    FOMCスケジュール近似（UTC 18:00 発表想定）
    2024: 8会合
    2025: 8会合
    2026: 4会合（暫定）
    """
    return [
        # 2024
        dt.date(2024, 1, 31),
        dt.date(2024, 3, 20),
        dt.date(2024, 5, 1),
        dt.date(2024, 6, 12),
        dt.date(2024, 7, 31),
        dt.date(2024, 9, 18),
        dt.date(2024, 11, 7),
        dt.date(2024, 12, 18),
        # 2025
        dt.date(2025, 1, 29),
        dt.date(2025, 3, 19),
        dt.date(2025, 5, 7),
        dt.date(2025, 6, 18),
        dt.date(2025, 7, 30),
        dt.date(2025, 9, 17),
        dt.date(2025, 11, 5),
        dt.date(2025, 12, 17),
        # 2026 (暫定)
        dt.date(2026, 1, 28),
        dt.date(2026, 3, 18),
        dt.date(2026, 5, 6),
        dt.date(2026, 6, 17),
    ]


def load_calendar(start: str = "2024-01-01", end: str = "2026-05-31") -> pd.DataFrame:
    """
    経済指標カレンダーを生成する。
    ★ 固定日近似を使用（カレンダーAPIが利用不可のため）

    Returns:
        DataFrame with columns: [event_dt (UTC), event, pair_relevant]
        event_dt は datetime (UTC, tz-aware)
    """
    start_d = dt.date.fromisoformat(start)
    end_d   = dt.date.fromisoformat(end)

    records = []

    # 月ループ
    y, m = start_d.year, start_d.month
    while dt.date(y, m, 1) <= end_d:
        # ── NFP: 第1金曜 21:30 UTC ──────────────────────────────
        nfp_d = _first_friday(y, m)
        if start_d <= nfp_d <= end_d:
            records.append({
                "event_dt": pd.Timestamp(nfp_d, tz="UTC").replace(hour=21, minute=30),
                "event": "NFP",
                "pair_relevant": ["EURUSD", "USDJPY"],
            })

        # ── CPI: 毎月12日 12:30 UTC ─────────────────────────────
        cpi_d = dt.date(y, m, 12)
        if start_d <= cpi_d <= end_d:
            records.append({
                "event_dt": pd.Timestamp(cpi_d, tz="UTC").replace(hour=12, minute=30),
                "event": "CPI",
                "pair_relevant": ["EURUSD", "USDJPY"],
            })

        # 月を進める
        m += 1
        if m > 12:
            m = 1
            y += 1

    # ── FOMC: 近似スケジュール 18:00 UTC ─────────────────────────
    for d_fomc in _fomc_dates_approx():
        if start_d <= d_fomc <= end_d:
            records.append({
                "event_dt": pd.Timestamp(d_fomc, tz="UTC").replace(hour=18, minute=0),
                "event": "FOMC",
                "pair_relevant": ["EURUSD", "USDJPY"],
            })

    df = pd.DataFrame(records).sort_values("event_dt").reset_index(drop=True)
    print(f"  [Calendar] FIXED_DATES method: {len(df)} events "
          f"({df['event'].value_counts().to_dict()})")
    print("  ★ 注意: investpy取得不可のため固定日近似を使用")
    return df


# =============================================================================
# データ取得（yfinance）
# =============================================================================
def fetch_yfinance(ticker: str, interval: str, days: int) -> pd.DataFrame | None:
    import yfinance as yf

    end_dt   = dt.datetime.now(tz=timezone.utc)
    start_dt = end_dt - dt.timedelta(days=days)

    df = yf.download(ticker, start=start_dt, end=end_dt,
                     interval=interval, auto_adjust=True, progress=False)
    if df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


# =============================================================================
# ボラ縮小計算
# =============================================================================
def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """True Range を使ったATR計算"""
    high = df["High"]
    low  = df["Low"]
    prev_close = df["Close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=period, adjust=False).mean()


def calc_vol_ratio(df: pd.DataFrame,
                   atr_period: int = ATR_PERIOD,
                   atr_ma_period: int = ATR_MA_PERIOD) -> pd.Series:
    """
    VOL_RATIO = ATR(atr_period) / SMA(ATR(atr_period), atr_ma_period)
    値が低いほど直近のボラが低い（縮小状態）
    """
    atr    = calc_atr(df, atr_period)
    atr_ma = atr.rolling(atr_ma_period, min_periods=atr_period).mean()
    return atr / atr_ma


def calc_bb(df: pd.DataFrame,
            period: int = BB_PERIOD,
            std_mult: float = BB_STD) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    BB計算
    Returns: (upper, mid, lower)
    """
    mid   = df["Close"].rolling(period, min_periods=period).mean()
    sigma = df["Close"].rolling(period, min_periods=period).std()
    upper = mid + std_mult * sigma
    lower = mid - std_mult * sigma
    return upper, mid, lower


# =============================================================================
# エントリー検出
# =============================================================================
def detect_entry(row: pd.Series,
                 pair: str,
                 vol_ratio: float,
                 bb_upper: float,
                 bb_lower: float,
                 atr_val: float,
                 vol_ratio_thresh: float,
                 calendar_df: pd.DataFrame,
                 pre_event_hours: int = PRE_EVENT_HOURS,
                 close_before_hours: int = CLOSE_BEFORE_HOURS) -> dict | None:
    """
    1本の1hバーでエントリー条件を検出。

    Returns:
        dict: {direction, entry, tp, sl, force_close_ts, event_dt, event}
        None: エントリーなし
    """
    ts = row.name  # pd.Timestamp (UTC)

    # ── ボラ縮小条件 ─────────────────────────────────────────────
    if np.isnan(vol_ratio) or vol_ratio >= vol_ratio_thresh:
        return None
    if np.isnan(atr_val) or atr_val <= 0:
        return None

    # ── 直近イベント検索 ─────────────────────────────────────────
    # ts から pre_event_hours 時間後以内にイベントがある
    ts_end   = ts + pd.Timedelta(hours=pre_event_hours)
    relevant = calendar_df[
        (calendar_df["event_dt"] > ts) &
        (calendar_df["event_dt"] <= ts_end) &
        (calendar_df["pair_relevant"].apply(lambda lst: pair in lst))
    ]

    if relevant.empty:
        return None

    # 最近のイベントを使う
    event_row = relevant.iloc[0]
    event_dt  = event_row["event_dt"]
    event_name = event_row["event"]

    # 強制クローズ時刻（発表 close_before_hours 前、最小1バー分後）
    force_close_ts = event_dt - pd.Timedelta(hours=close_before_hours)
    if force_close_ts <= ts:
        return None  # すでに強制クローズ窓を過ぎている

    close_val = row["Close"]
    high_val  = row["High"]
    low_val   = row["Low"]

    pip = PAIR_CONFIG[pair]["pip"]
    spread = SPREAD_PIPS[pair] * pip

    direction = None
    entry = tp = sl = None

    # ── BB上限タッチ → ショート ───────────────────────────────
    if high_val >= bb_upper and close_val < bb_upper:
        direction = "SHORT"
        entry     = close_val - spread         # Sell at Bid
        tp        = entry - atr_val * TP_ATR_MULT
        sl        = entry + atr_val * SL_ATR_MULT

    # ── BB下限タッチ → ロング ────────────────────────────────
    elif low_val <= bb_lower and close_val > bb_lower:
        direction = "LONG"
        entry     = close_val + spread         # Buy at Ask
        tp        = entry + atr_val * TP_ATR_MULT
        sl        = entry - atr_val * SL_ATR_MULT

    if direction is None:
        return None

    return {
        "direction":       direction,
        "entry":           entry,
        "tp":              tp,
        "sl":              sl,
        "atr":             atr_val,
        "vol_ratio":       vol_ratio,
        "force_close_ts":  force_close_ts,
        "event_dt":        event_dt,
        "event":           event_name,
    }


# =============================================================================
# バックテスト本体
# =============================================================================
def run_backtest(pair: str,
                 df_1h: pd.DataFrame,
                 calendar_df: pd.DataFrame,
                 vol_ratio_thresh: float = VOL_RATIO_THRESH,
                 bb_std: float = BB_STD) -> pd.DataFrame:
    """
    1ペアのバックテストを実行。
    1ポジション限定ルール適用。
    """
    cfg    = PAIR_CONFIG[pair]
    pip    = cfg["pip"]

    # インジケータ計算
    df = df_1h.copy()
    vol_ratio_s          = calc_vol_ratio(df)
    atr_s                = calc_atr(df)
    bb_upper_s, bb_mid_s, bb_lower_s = calc_bb(df, BB_PERIOD, bb_std)

    trades    = []
    in_pos    = False   # 1ポジション限定
    pos       = {}

    for i, (ts, row) in enumerate(df.iterrows()):
        # ── 既存ポジションの決済判定 ─────────────────────────
        if in_pos:
            direction      = pos["direction"]
            entry          = pos["entry"]
            tp             = pos["tp"]
            sl             = pos["sl"]
            force_close_ts = pos["force_close_ts"]

            high_now = row["High"]
            low_now  = row["Low"]
            close_now = row["Close"]

            result_pips = None
            exit_price  = None
            exit_reason = None

            if direction == "SHORT":
                # SLヒット
                if high_now >= sl:
                    exit_price  = sl
                    exit_reason = "SL"
                    result_pips = (entry - sl) / pip
                # TPヒット
                elif low_now <= tp:
                    exit_price  = tp
                    exit_reason = "TP"
                    result_pips = (entry - tp) / pip
                # 強制クローズ（イベント2h前）
                elif ts >= force_close_ts:
                    exit_price  = close_now
                    exit_reason = "FORCE_CLOSE"
                    result_pips = (entry - close_now) / pip

            else:  # LONG
                if low_now <= sl:
                    exit_price  = sl
                    exit_reason = "SL"
                    result_pips = (sl - entry) / pip
                elif high_now >= tp:
                    exit_price  = tp
                    exit_reason = "TP"
                    result_pips = (tp - entry) / pip
                elif ts >= force_close_ts:
                    exit_price  = close_now
                    exit_reason = "FORCE_CLOSE"
                    result_pips = (close_now - entry) / pip

            if result_pips is not None:
                sl_pips = abs(entry - sl) / pip
                rr      = result_pips / sl_pips if sl_pips > 0 else 0
                trades.append({
                    "pair":        pair,
                    "date":        pos["entry_ts"].date(),
                    "direction":   direction,
                    "entry_ts":    pos["entry_ts"],
                    "exit_ts":     ts,
                    "entry":       round(entry, 5),
                    "tp":          round(tp, 5),
                    "sl":          round(sl, 5),
                    "exit_price":  round(exit_price, 5),
                    "atr":         round(pos["atr"], 6),
                    "vol_ratio":   round(pos["vol_ratio"], 3),
                    "bb_std":      bb_std,
                    "vol_thresh":  vol_ratio_thresh,
                    "result_pips": round(result_pips, 1),
                    "sl_pips":     round(sl_pips, 1),
                    "rr":          round(rr, 2),
                    "exit_reason": exit_reason,
                    "win":         result_pips > 0,
                    "event":       pos["event"],
                    "event_dt":    pos["event_dt"],
                })
                in_pos = False
                pos    = {}

        # ── 新規エントリー判定（ポジションなし時のみ） ─────────
        if not in_pos:
            vol_ratio_v = vol_ratio_s.iloc[i]
            atr_v       = atr_s.iloc[i]
            bb_upper_v  = bb_upper_s.iloc[i]
            bb_lower_v  = bb_lower_s.iloc[i]

            sig = detect_entry(
                row, pair,
                vol_ratio_v, bb_upper_v, bb_lower_v, atr_v,
                vol_ratio_thresh, calendar_df,
            )
            if sig:
                in_pos = True
                pos = {
                    "entry_ts":      ts,
                    "direction":     sig["direction"],
                    "entry":         sig["entry"],
                    "tp":            sig["tp"],
                    "sl":            sig["sl"],
                    "atr":           sig["atr"],
                    "vol_ratio":     sig["vol_ratio"],
                    "force_close_ts": sig["force_close_ts"],
                    "event":         sig["event"],
                    "event_dt":      sig["event_dt"],
                }

    # ── データ終端でオープン中のポジションを時価決済 ─────────────
    if in_pos and len(df) > 0:
        last_row  = df.iloc[-1]
        last_ts   = df.index[-1]
        direction = pos["direction"]
        entry     = pos["entry"]
        sl        = pos["sl"]
        close_now = last_row["Close"]
        if direction == "SHORT":
            result_pips = (entry - close_now) / pip
        else:
            result_pips = (close_now - entry) / pip
        sl_pips = abs(entry - sl) / pip
        rr      = result_pips / sl_pips if sl_pips > 0 else 0
        trades.append({
            "pair":        pair,
            "date":        pos["entry_ts"].date(),
            "direction":   direction,
            "entry_ts":    pos["entry_ts"],
            "exit_ts":     last_ts,
            "entry":       round(entry, 5),
            "tp":          round(pos["tp"], 5),
            "sl":          round(sl, 5),
            "exit_price":  round(close_now, 5),
            "atr":         round(pos["atr"], 6),
            "vol_ratio":   round(pos["vol_ratio"], 3),
            "bb_std":      bb_std,
            "vol_thresh":  vol_ratio_thresh,
            "result_pips": round(result_pips, 1),
            "sl_pips":     round(sl_pips, 1),
            "rr":          round(rr, 2),
            "exit_reason": "EOD",
            "win":         result_pips > 0,
            "event":       pos["event"],
            "event_dt":    pos["event_dt"],
        })

    return pd.DataFrame(trades)


# =============================================================================
# 統計計算（session_fakeout_bt.py から流用・採用基準変更）
# =============================================================================
def calc_stats(df: pd.DataFrame, pair: str = "ALL",
               vol_thresh: float = None, bb_std_val: float = None) -> dict:
    if df.empty:
        return {"pair": pair, "n": 0,
                "vol_thresh": vol_thresh, "bb_std": bb_std_val}

    n    = len(df)
    wins = int(df["win"].sum())
    wr   = wins / n * 100

    pos_p = df[df["result_pips"] > 0]["result_pips"].sum()
    neg_p = abs(df[df["result_pips"] < 0]["result_pips"].sum())
    pf    = pos_p / neg_p if neg_p > 0 else np.inf

    avg_win  = df[df["result_pips"] > 0]["result_pips"].mean() if wins      > 0 else 0
    avg_loss = df[df["result_pips"] < 0]["result_pips"].mean() if (n-wins)  > 0 else 0
    avg_rr   = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    equity   = df["result_pips"].cumsum().values
    peak     = np.maximum.accumulate(equity)
    drawdown = equity - peak
    max_dd   = drawdown.min()
    max_dd_abs = abs(max_dd)

    max_peak = peak.max()
    dd_pct   = (max_dd_abs / max_peak * 100) if max_peak > 0 else 100.0

    total_pips = equity[-1] if len(equity) > 0 else 0

    # 月次Sharpe
    df2 = df.copy()
    df2["date"] = pd.to_datetime(df2["date"])
    monthly = df2.groupby(df2["date"].dt.to_period("M"))["result_pips"].sum()
    if len(monthly) >= 2 and monthly.std() > 0:
        sharpe = monthly.mean() / monthly.std() * np.sqrt(12)
    else:
        sharpe = 0.0

    # 採用基準: Sharpe>1.0 かつ DD<20% かつ n>=200
    adopt = (sharpe >= 1.0) and (dd_pct < 20.0) and (n >= 200)

    return {
        "pair":        pair,
        "vol_thresh":  vol_thresh,
        "bb_std":      bb_std_val,
        "n":           n,
        "wins":        wins,
        "wr_%":        round(wr, 1),
        "pf":          round(pf, 3),
        "avg_rr":      round(avg_rr, 2),
        "avg_win_p":   round(avg_win, 1),
        "avg_loss_p":  round(avg_loss, 1),
        "total_p":     round(total_pips, 1),
        "max_dd_p":    round(max_dd, 1),
        "dd_%":        round(dd_pct, 1),
        "sharpe":      round(sharpe, 2),
        "adopt":       adopt,
        "n_tp":        int((df["exit_reason"] == "TP").sum()),
        "n_sl":        int((df["exit_reason"] == "SL").sum()),
        "n_fc":        int((df["exit_reason"] == "FORCE_CLOSE").sum()),
    }


# =============================================================================
# 月次損益テーブル
# =============================================================================
def monthly_table(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    df2["date"] = pd.to_datetime(df2["date"])
    df2["YM"]   = df2["date"].dt.to_period("M")
    tbl = df2.groupby(["pair", "YM"])["result_pips"].sum().unstack("pair")
    tbl["TOTAL"] = tbl.sum(axis=1)
    return tbl


# =============================================================================
# 感度分析（スイープ）
# =============================================================================
def run_sweep(pair_data: dict, calendar_df: pd.DataFrame) -> pd.DataFrame:
    results = []
    total = len(SWEEP_VOL_THRESH) * len(SWEEP_BB_STD)
    done  = 0

    for vt in SWEEP_VOL_THRESH:
        for bs in SWEEP_BB_STD:
            done += 1
            print(f"  sweep [{done}/{total}] vol_thresh={vt}, bb_std={bs}")
            all_trades = []
            for pair, df_1h in pair_data.items():
                if df_1h is None:
                    continue
                t = run_backtest(pair, df_1h, calendar_df,
                                 vol_ratio_thresh=vt, bb_std=bs)
                if not t.empty:
                    all_trades.append(t)
            if not all_trades:
                continue
            combined = pd.concat(all_trades, ignore_index=True)
            s = calc_stats(combined, "ALL", vol_thresh=vt, bb_std_val=bs)
            results.append(s)

    return pd.DataFrame(results)


# =============================================================================
# チャート描画（session_fakeout_bt.py の可視化から流用・拡張）
# =============================================================================
def plot_results(all_trades: pd.DataFrame, stats_list: list):
    pairs  = list(set(all_trades["pair"].tolist())) if not all_trades.empty else PAIRS
    colors = {"EURUSD": "#2196F3", "USDJPY": "#FF5722"}

    fig = plt.figure(figsize=(16, 16))
    gs  = gridspec.GridSpec(4, 2, figure=fig, hspace=0.55, wspace=0.35)

    # ── 上段左: Equity Curve ──────────────────────────────────────
    ax_eq = fig.add_subplot(gs[0, :])
    for pair in PAIRS:
        sub = all_trades[all_trades["pair"] == pair].sort_values("entry_ts")
        if sub.empty:
            continue
        eq = sub["result_pips"].cumsum()
        ax_eq.plot(sub["entry_ts"].values, eq.values,
                   label=pair, color=colors.get(pair, "gray"), linewidth=1.5)

    combined_sorted = all_trades.sort_values("entry_ts")
    if not combined_sorted.empty:
        eq_all = combined_sorted["result_pips"].cumsum()
        ax_eq.plot(combined_sorted["entry_ts"].values, eq_all.values,
                   color="black", linewidth=2.2, linestyle="--", label="ALL")
        peak_all = np.maximum.accumulate(eq_all.values)
        ax_eq.fill_between(combined_sorted["entry_ts"].values,
                           eq_all.values, peak_all,
                           alpha=0.12, color="red")

    ax_eq.axhline(0, color="gray", linestyle=":", linewidth=0.8)
    ax_eq.set_title(
        f"Equity Curve — Pre-Event Vol Squeeze BB Reversal\n"
        f"VOL_RATIO_THRESH={VOL_RATIO_THRESH}, BB_STD={BB_STD}, "
        f"TP×{TP_ATR_MULT} / SL×{SL_ATR_MULT} ATR",
        fontsize=10, fontweight="bold")
    ax_eq.set_ylabel("Cumulative pips")
    ax_eq.legend(loc="upper left", fontsize=9)
    ax_eq.grid(True, alpha=0.3)

    # ── 2段目: 月次棒グラフ ───────────────────────────────────────
    for i, pair in enumerate(PAIRS):
        ax = fig.add_subplot(gs[1, i])
        sub = all_trades[all_trades["pair"] == pair].copy()
        if sub.empty:
            ax.set_title(f"{pair}\nNo data", fontsize=9)
            continue
        sub["date"] = pd.to_datetime(sub["date"])
        monthly = sub.groupby(sub["date"].dt.to_period("M"))["result_pips"].sum()
        x_labels = [str(p) for p in monthly.index]
        bar_colors = ["#1976D2" if v >= 0 else "#E53935" for v in monthly.values]
        ax.bar(range(len(x_labels)), monthly.values, color=bar_colors, width=0.7)
        ax.set_xticks(range(len(x_labels)))
        ax.set_xticklabels(x_labels, rotation=45, fontsize=6)
        ax.axhline(0, color="gray", linestyle=":", linewidth=0.8)
        ax.set_title(f"{pair} Monthly PnL (pips)", fontsize=9)
        ax.grid(True, alpha=0.3, axis="y")

    # ── 3段目左: VOL_RATIO ヒストグラム ──────────────────────────
    ax_hist = fig.add_subplot(gs[2, 0])
    if not all_trades.empty:
        vr = all_trades["vol_ratio"].dropna()
        ax_hist.hist(vr, bins=25, color="#4CAF50", edgecolor="white", alpha=0.8)
        ax_hist.axvline(VOL_RATIO_THRESH, color="red", linestyle="--",
                        linewidth=1.5, label=f"Thresh={VOL_RATIO_THRESH}")
        ax_hist.set_title("VOL_RATIO Distribution (at entry)", fontsize=9)
        ax_hist.set_xlabel("VOL_RATIO")
        ax_hist.set_ylabel("Count")
        ax_hist.legend(fontsize=8)
        ax_hist.grid(True, alpha=0.3)

    # ── 3段目右: イベント別トレード数・WR ────────────────────────
    ax_ev = fig.add_subplot(gs[2, 1])
    if not all_trades.empty:
        ev_stats = all_trades.groupby("event").apply(
            lambda x: pd.Series({
                "n": len(x),
                "wr": x["win"].mean() * 100,
                "pf_approx": (x[x["result_pips"] > 0]["result_pips"].sum() /
                               abs(x[x["result_pips"] < 0]["result_pips"].sum())
                               if x["result_pips"].lt(0).any() else np.inf)
            })
        ).reset_index()
        x_pos = range(len(ev_stats))
        bars = ax_ev.bar(x_pos, ev_stats["n"], color="#9C27B0", alpha=0.7)
        for xi, (_, ev_row) in zip(x_pos, ev_stats.iterrows()):
            ax_ev.text(xi, ev_row["n"] + 0.3,
                       f"WR={ev_row['wr']:.0f}%\nPF={ev_row['pf_approx']:.2f}",
                       ha="center", fontsize=7.5)
        ax_ev.set_xticks(x_pos)
        ax_ev.set_xticklabels(ev_stats["event"].tolist(), fontsize=9)
        ax_ev.set_title("Trades by Event Type", fontsize=9)
        ax_ev.set_ylabel("# Trades")
        ax_ev.grid(True, alpha=0.3, axis="y")

    # ── 4段目: 統計サマリーテーブル ──────────────────────────────
    ax_tbl = fig.add_subplot(gs[3, :])
    ax_tbl.axis("off")

    col_keys  = ["pair", "n", "wr_%", "pf", "avg_rr", "total_p",
                 "max_dd_p", "dd_%", "sharpe", "n_tp", "n_sl", "n_fc", "adopt"]
    col_names = ["Pair", "N", "WR%", "PF", "AvgRR", "Total(p)",
                 "MaxDD(p)", "DD%", "Sharpe", "TP", "SL", "FC", "Adopt?"]

    disp_stats = [s for s in stats_list if s.get("n", 0) > 0]
    table_data = [[str(s.get(k, "")) for k in col_keys] for s in disp_stats]

    if table_data:
        tbl = ax_tbl.table(cellText=table_data, colLabels=col_names,
                           loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8.5)
        tbl.scale(1, 1.6)

        adopt_idx = col_keys.index("adopt")
        for i, s in enumerate(disp_stats):
            cell = tbl[i + 1, adopt_idx]
            cell.set_facecolor("#C8E6C9" if s.get("adopt") else "#FFCDD2")

    ax_tbl.set_title("Strategy Statistics (Adopt: Sharpe>=1.0, DD<20%, n>=200)",
                     fontsize=10, fontweight="bold", pad=6)

    if not all_trades.empty:
        d0 = pd.to_datetime(all_trades["date"].min())
        d1 = pd.to_datetime(all_trades["date"].max())
        fig.text(0.01, 0.003,
                 f"Data: {d0.date()} ~ {d1.date()} | "
                 f"Calendar: {CALENDAR_METHOD} (NFP/CPI/FOMC近似) | "
                 f"Spread: EURUSD={SPREAD_PIPS['EURUSD']}p / USDJPY={SPREAD_PIPS['USDJPY']}p",
                 fontsize=7, color="gray")

    plt.savefig(CHART_PNG, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Chart saved: {CHART_PNG}")


# =============================================================================
# 感度分析ヒートマップ
# =============================================================================
def plot_sweep(sweep_df: pd.DataFrame):
    if sweep_df.empty:
        return
    sweep_png = CHART_PNG.replace(".png", "_sweep.png")

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    metrics = [("pf", "Profit Factor"), ("wr_%", "Win Rate %"), ("sharpe", "Sharpe")]

    for ax, (metric, title) in zip(axes, metrics):
        pivot = sweep_df.pivot(index="vol_thresh", columns="bb_std",
                               values=metric).astype(float)
        im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn",
                       vmin=pivot.values.min(), vmax=pivot.values.max())
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(c) for c in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([str(r) for r in pivot.index])
        ax.set_xlabel("BB_STD")
        ax.set_ylabel("VOL_RATIO_THRESH")
        ax.set_title(title, fontsize=11, fontweight="bold")
        plt.colorbar(im, ax=ax)
        for r in range(pivot.shape[0]):
            for c in range(pivot.shape[1]):
                ax.text(c, r, f"{pivot.values[r, c]:.2f}",
                        ha="center", va="center", fontsize=9)

    plt.suptitle("Parameter Sensitivity Analysis (ALL pairs)\n"
                 "VOL_RATIO_THRESH x BB_STD",
                 fontsize=12, fontweight="bold")
    plt.tight_layout()
    plt.savefig(sweep_png, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Sweep chart: {sweep_png}")


# =============================================================================
# メイン
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Pre-Event Volatility Squeeze BB Reversal Backtest"
    )
    parser.add_argument("--sweep", action="store_true",
                        help="VOL_RATIO_THRESH x BB_STD 感度分析を実行")
    args = parser.parse_args()

    print("=" * 66)
    print("Pre-Event Vol Squeeze x BB Reversal Backtest v1")
    print(f"  VOL_RATIO_THRESH={VOL_RATIO_THRESH}, BB({BB_PERIOD},{BB_STD})")
    print(f"  TP=ATRx{TP_ATR_MULT}, SL=ATRx{SL_ATR_MULT}")
    print(f"  PRE_EVENT={PRE_EVENT_HOURS}h, FORCE_CLOSE={CLOSE_BEFORE_HOURS}h before")
    print(f"  PAIRS: {PAIRS}")
    print(f"  Calendar: {CALENDAR_METHOD} (NFP/CPI/FOMC 固定日近似)")
    print("=" * 66)

    # ── カレンダー生成 ──────────────────────────────────────────
    print("\n[Calendar]")
    calendar_df = load_calendar()
    print(f"  Events: {len(calendar_df)}")
    print(calendar_df[["event_dt", "event"]].head(8).to_string(index=False))

    # ── データ取得 ──────────────────────────────────────────────
    pair_data = {}
    for pair in PAIRS:
        cfg = PAIR_CONFIG[pair]
        print(f"\n[{pair}] fetching 1h data ({HIST_DAYS_1H}d)...")
        df_1h = fetch_yfinance(cfg["ticker"], "1h", HIST_DAYS_1H)
        if df_1h is None:
            print(f"  WARNING: no data for {pair}")
            pair_data[pair] = None
        else:
            print(f"  → {len(df_1h)} bars  "
                  f"({df_1h.index[0].date()} ~ {df_1h.index[-1].date()})")
            pair_data[pair] = df_1h

    # ── メインバックテスト ──────────────────────────────────────
    all_trades = []
    stats_list = []

    for pair, df_1h in pair_data.items():
        if df_1h is None:
            print(f"\n[{pair}] SKIP (no data)")
            continue
        print(f"\n[{pair}] Running BT ...")
        trades_df = run_backtest(pair, df_1h, calendar_df)
        print(f"  Trades: {len(trades_df)}")
        if not trades_df.empty:
            all_trades.append(trades_df)
            s = calc_stats(trades_df, pair,
                           vol_thresh=VOL_RATIO_THRESH, bb_std_val=BB_STD)
            stats_list.append(s)
            print(f"  WR={s['wr_%']}%  PF={s['pf']}  Sharpe={s['sharpe']}  "
                  f"DD%={s['dd_%']}%  n={s['n']}  Adopt={s['adopt']}")
        else:
            stats_list.append({"pair": pair, "n": 0})

    if not all_trades:
        print("\nERROR: No trades generated.")
        return

    combined = pd.concat(all_trades, ignore_index=True)
    s_all    = calc_stats(combined, "ALL",
                          vol_thresh=VOL_RATIO_THRESH, bb_std_val=BB_STD)
    stats_list.append(s_all)
    print(f"\n[ALL] WR={s_all['wr_%']}%  PF={s_all['pf']}  "
          f"Sharpe={s_all['sharpe']}  DD%={s_all['dd_%']}%  "
          f"n={s_all['n']}  Adopt={s_all['adopt']}")

    # ── CSV出力 ─────────────────────────────────────────────────
    combined.to_csv(TRADE_CSV, index=False)
    pd.DataFrame(stats_list).to_csv(STATS_CSV, index=False)
    print(f"\n  Trades CSV: {TRADE_CSV}")
    print(f"  Stats CSV : {STATS_CSV}")

    # ── 月次テーブル ──────────────────────────────────────────
    print("\n── Monthly PnL (pips) ─────────────────────────────────────────")
    mtbl = monthly_table(combined)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 120)
    print(mtbl.to_string())

    # ── イベント別統計 ─────────────────────────────────────────
    print("\n── By Event ──────────────────────────────────────────────────")
    ev_grp = combined.groupby("event").apply(
        lambda x: pd.Series({
            "n":  len(x),
            "wr": round(x["win"].mean() * 100, 1),
            "pf": round(
                x[x["result_pips"] > 0]["result_pips"].sum() /
                abs(x[x["result_pips"] < 0]["result_pips"].sum())
                if x["result_pips"].lt(0).any() else np.inf, 3),
            "total_pips": round(x["result_pips"].sum(), 1),
        })
    ).reset_index()
    print(ev_grp.to_string(index=False))

    # ── チャート ──────────────────────────────────────────────
    print("\n[Chart] Plotting...")
    plot_results(combined, stats_list)

    # ── 感度分析 ──────────────────────────────────────────────
    if args.sweep:
        print("\n── Parameter Sweep ─────────────────────────────────────────")
        sweep_df = run_sweep(pair_data, calendar_df)
        if not sweep_df.empty:
            sweep_df.to_csv(SWEEP_CSV, index=False)
            cols = ["vol_thresh", "bb_std", "n", "wr_%", "pf", "sharpe", "dd_%", "adopt"]
            print(sweep_df[cols].to_string(index=False))
            print(f"\n  Sweep CSV: {SWEEP_CSV}")
            try:
                plot_sweep(sweep_df)
            except Exception as e:
                print(f"  Sweep chart error: {e}")

    # ── 採用判定サマリー ──────────────────────────────────────
    print("\n── Adoption (Sharpe>=1.0, DD<20%, n>=200) ────────────────────")
    for s in stats_list:
        if s.get("n", 0) == 0:
            continue
        mark = "ADOPT" if s.get("adopt") else "REJECT"
        print(f"  {s['pair']:8s}: [{mark}]  "
              f"Sharpe={s.get('sharpe','?')}, "
              f"DD={s.get('dd_%','?')}%, n={s.get('n','?')}")

    print(f"\n  ★ カレンダー方式: {CALENDAR_METHOD}")
    print("    NFP=第1金曜 21:30 UTC / CPI=毎月12日 12:30 UTC / FOMC=近似スケジュール 18:00 UTC")
    print("    investpy / pandas_datareaderは取得不可のため固定日近似を採用")

    if s_all["n"] < 200:
        print(f"\n  ⚠ サンプル数不足 n={s_all['n']} < 200")
        print("    1h足は yfinance で ~700日取得済み。より長期データが必要な場合は")
        print("    VPS MT5から手動エクスポートして利用してください。")


if __name__ == "__main__":
    main()
