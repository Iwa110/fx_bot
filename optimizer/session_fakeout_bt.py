"""
session_fakeout_bt.py
東京→ロンドン セッション移行フェイクアウト逆張り バックテスト v2

戦略:
  - 東京セッション(00:00-09:00 UTC)の1h足で高安を確定
  - 13:00-16:00 UTC の5m足で東京高安をフェイクアウトしたらエントリー
  - TP: 東京レンジ幅 × TP_RATIO（レンジ中央値方向）
  - SL: ブレイク幅 + FAKE_PIPS 外側
  - タイムアウト: 当日 20:00 UTC
  - 1日1エントリー上限（先着）

データ優先順:
  1. ローカルMT5 CSV (MT5_DATA_DIR に GBPJPY_M5.csv 等を置く)
  2. yfinance フォールバック (5m は直近60日のみ)

使い方:
  python session_fakeout_bt.py                  # デフォルト実行
  python session_fakeout_bt.py --sweep          # パラメータ感度分析
  python session_fakeout_bt.py --mt5 /path/to/data  # MT5 CSVディレクトリ指定
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

# ─────────────────────────────────────────────────────────────────
# パラメータ (感度分析でスイープする変数)
# ─────────────────────────────────────────────────────────────────
FAKE_PIPS     = 5       # フェイクアウト判定の最小ブレイク幅 (pips)
TP_RATIO      = 0.5     # 東京レンジに対するTP比率
SESSION_START = "13:00" # フェイクアウト検出ウィンドウ開始 UTC
SESSION_END   = "16:00" # フェイクアウト検出ウィンドウ終了 UTC
TIMEOUT_TIME  = "20:00" # タイムアウト強制クローズ UTC

TOKYO_START   = 0       # 東京セッション開始 UTC hour
TOKYO_END     = 9       # 東京セッション終了 UTC hour (exclusive)

# 感度分析スイープ範囲
SWEEP_FAKE_PIPS = [3, 5, 7, 10]
SWEEP_TP_RATIO  = [0.3, 0.5, 0.7, 1.0]

# ─────────────────────────────────────────────────────────────────
# ペア設定
# ─────────────────────────────────────────────────────────────────
PAIRS = {
    "GBPJPY": {"ticker": "GBPJPY=X", "spread_pips": 3.0, "pip": 0.01},
    "EURJPY": {"ticker": "EURJPY=X", "spread_pips": 2.0, "pip": 0.01},
    "GBPUSD": {"ticker": "GBPUSD=X", "spread_pips": 1.5, "pip": 0.0001},
}

# 除外日設定
EXCLUDE_MONTH_START   = True   # 毎月1日除外
EXCLUDE_FIRST_FRIDAY  = True   # 第1金曜除外（NFP代用）

# yfinance取得期間 (1h足は730日上限)
HIST_DAYS_1H = 700
HIST_DAYS_5M = 58    # API上限60日から安全マージン

# MT5 CSVディレクトリ (コマンドライン引数で上書き可)
MT5_DATA_DIR = None

# ─────────────────────────────────────────────────────────────────
# 出力パス
# ─────────────────────────────────────────────────────────────────
OUT_DIR   = os.path.dirname(os.path.abspath(__file__))
TRADE_CSV = os.path.join(OUT_DIR, "session_fakeout_bt_trades.csv")
STATS_CSV = os.path.join(OUT_DIR, "session_fakeout_bt_stats.csv")
SWEEP_CSV = os.path.join(OUT_DIR, "session_fakeout_bt_sweep.csv")
CHART_PNG = os.path.join(OUT_DIR, "session_fakeout_bt_chart.png")


# ─────────────────────────────────────────────────────────────────
# データ取得: MT5 CSV ローダー
# ─────────────────────────────────────────────────────────────────
def load_mt5_csv(pair: str, timeframe: str, data_dir: str):
    """
    MT5エクスポートCSVを読み込む。
    想定ファイル名パターン:
      GBPJPY_H1.csv / GBPJPY_M5.csv  (MT5標準エクスポート形式)
      or GBPJPY_1h.csv / GBPJPY_5m.csv
    MT5 CSVフォーマット: <DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t...
    または pandas-friendly: Date,Open,High,Low,Close,...
    """
    tf_map = {
        "1h": ["H1", "1h", "60"],
        "5m": ["M5", "5m", "5"],
    }
    candidates = []
    for suffix in tf_map.get(timeframe, []):
        candidates += [
            os.path.join(data_dir, f"{pair}_{suffix}.csv"),
            os.path.join(data_dir, f"{pair.lower()}_{suffix.lower()}.csv"),
        ]

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            # MT5タブ区切り形式を試みる
            df = pd.read_csv(path, sep="\t", header=0)
            cols = [c.strip("<>").upper() for c in df.columns]
            df.columns = cols
            if "DATE" in cols and "TIME" in cols:
                df["datetime"] = pd.to_datetime(
                    df["DATE"].astype(str) + " " + df["TIME"].astype(str),
                    utc=True
                )
                df = df.rename(columns={
                    "OPEN": "Open", "HIGH": "High", "LOW": "Low", "CLOSE": "Close"
                })
                df = df.set_index("datetime").sort_index()
                print(f"  [MT5] Loaded {path} ({len(df)} bars)")
                return df[["Open", "High", "Low", "Close"]]
        except Exception:
            pass
        try:
            # pandas形式 (Date列がdatetime)
            df = pd.read_csv(path, parse_dates=["Date"])
            df.index = pd.to_datetime(df["Date"], utc=True)
            df = df.sort_index()
            print(f"  [MT5] Loaded {path} ({len(df)} bars)")
            return df[["Open", "High", "Low", "Close"]]
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────────────────────────
# データ取得: yfinance
# ─────────────────────────────────────────────────────────────────
def fetch_yfinance(ticker: str, interval: str, days: int):
    import yfinance as yf
    import datetime as dt

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


# ─────────────────────────────────────────────────────────────────
# データ取得: メインルーター
# ─────────────────────────────────────────────────────────────────
def fetch_data(pair: str, ticker: str, mt5_dir: str = None):
    """
    MT5 CSV → yfinance の優先順でデータ取得。
    Returns: (df_1h, df_5m) — どちらも None の場合はスキップ
    """
    df_1h = df_5m = None

    # ── MT5 CSV 優先 ──────────────────────────────────────────
    if mt5_dir and os.path.isdir(mt5_dir):
        df_1h = load_mt5_csv(pair, "1h", mt5_dir)
        df_5m = load_mt5_csv(pair, "5m", mt5_dir)

    # ── yfinance フォールバック ────────────────────────────────
    if df_1h is None:
        print(f"  [yfinance 1h] {ticker}  ({HIST_DAYS_1H}d)")
        df_1h = fetch_yfinance(ticker, "1h", HIST_DAYS_1H)
        if df_1h is None:
            print(f"  WARNING: 1h data unavailable for {ticker}")
            return None, None

    if df_5m is None:
        print(f"  [yfinance 5m] {ticker}  (last {HIST_DAYS_5M}d only!)")
        df_5m = fetch_yfinance(ticker, "5m", HIST_DAYS_5M)
        if df_5m is None:
            print(f"  WARNING: 5m data unavailable for {ticker}")
            return df_1h, None

    print(f"  → 1h: {len(df_1h)} bars / 5m: {len(df_5m)} bars "
          f"({df_5m.index[0].date()} ~ {df_5m.index[-1].date()})")
    return df_1h, df_5m


# ─────────────────────────────────────────────────────────────────
# 東京レンジ計算
# ─────────────────────────────────────────────────────────────────
def get_tokyo_range(df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    日次 東京セッション(00:00-09:00 UTC)の高安・中値・レンジ幅を返す。
    """
    df = df_1h.copy()
    tokyo = df[(df.index.hour >= TOKYO_START) & (df.index.hour < TOKYO_END)]
    grp = tokyo.groupby(tokyo.index.normalize()).agg(
        tok_high=("High", "max"),
        tok_low=("Low",  "min")
    )
    grp["tok_mid"]   = (grp["tok_high"] + grp["tok_low"]) / 2
    grp["tok_range"] = grp["tok_high"] - grp["tok_low"]
    return grp


# ─────────────────────────────────────────────────────────────────
# 除外日
# ─────────────────────────────────────────────────────────────────
def build_exclude_dates(all_dates) -> set:
    exclude = set()
    for d in all_dates:
        if EXCLUDE_MONTH_START and d.day == 1:
            exclude.add(d)
        if EXCLUDE_FIRST_FRIDAY and d.weekday() == 4 and d.day <= 7:
            exclude.add(d)
    return exclude


# ─────────────────────────────────────────────────────────────────
# フェイクアウト検出
# ─────────────────────────────────────────────────────────────────
def detect_fakeout(df_5m_day: pd.DataFrame,
                   tok_high: float, tok_low: float,
                   fake_pips: float, pip: float) -> dict | None:
    """
    セッションウィンドウ内の5m足を走査しフェイクアウトを先着検出。
    Returns: dict or None
    """
    thresh = fake_pips * pip

    def _td(t_str):
        h, m = map(int, t_str.split(":"))
        return pd.Timedelta(hours=h, minutes=m)

    base     = df_5m_day.index[0].normalize()
    sess_s   = base + _td(SESSION_START)
    sess_e   = base + _td(SESSION_END)

    window = df_5m_day[(df_5m_day.index >= sess_s) & (df_5m_day.index < sess_e)]
    if window.empty:
        return None

    for ts, row in window.iterrows():
        # ショート: ヒゲが tok_high + FAKE_PIPS 以上 かつ 終値 ≤ tok_high
        if (row["High"] >= tok_high + thresh) and (row["Close"] <= tok_high):
            sl = row["High"] + thresh   # ヒゲ先端 + FAKE_PIPS
            return {"direction": "SHORT", "entry_ts": ts,
                    "entry": row["Close"], "sl": sl}

        # ロング: ヒゲが tok_low - FAKE_PIPS 以下 かつ 終値 ≥ tok_low
        if (row["Low"] <= tok_low - thresh) and (row["Close"] >= tok_low):
            sl = row["Low"] - thresh    # ヒゲ先端 - FAKE_PIPS
            return {"direction": "LONG", "entry_ts": ts,
                    "entry": row["Close"], "sl": sl}

    return None


# ─────────────────────────────────────────────────────────────────
# バックテスト本体
# ─────────────────────────────────────────────────────────────────
def run_backtest(pair: str, df_1h: pd.DataFrame, df_5m: pd.DataFrame,
                 config: dict,
                 fake_pips: float = FAKE_PIPS,
                 tp_ratio: float  = TP_RATIO) -> pd.DataFrame:
    pip    = config["pip"]
    spread = config["spread_pips"] * pip

    tok_df = get_tokyo_range(df_1h)

    # 5m を日付でインデックス化
    df_5m_copy = df_5m.copy()
    df_5m_copy["_date"] = df_5m_copy.index.normalize()

    all_dates = [ts.normalize() for ts in tok_df.index]
    exclude   = build_exclude_dates(all_dates)

    trades = []

    for date_ts in tok_df.index:
        date_norm = date_ts.normalize()
        if date_norm in exclude:
            continue

        row_tok   = tok_df.loc[date_ts]
        tok_high  = row_tok["tok_high"]
        tok_low   = row_tok["tok_low"]
        tok_range = row_tok["tok_range"]

        # レンジが極端に狭い日 (10pips未満) は除外
        if tok_range < pip * 10:
            continue

        df_day = df_5m_copy[df_5m_copy["_date"] == date_norm]
        if df_day.empty:
            continue

        sig = detect_fakeout(df_day, tok_high, tok_low, fake_pips, pip)
        if sig is None:
            continue

        direction = sig["direction"]
        entry_ts  = sig["entry_ts"]
        entry_raw = sig["entry"]
        sl_raw    = sig["sl"]

        # スプレッドをエントリーに反映
        if direction == "SHORT":
            entry = entry_raw - spread          # Sell at Bid
            sl    = sl_raw                      # SL stays at wick
            tp    = entry - tok_range * tp_ratio
        else:
            entry = entry_raw + spread          # Buy at Ask
            sl    = sl_raw
            tp    = entry + tok_range * tp_ratio

        # タイムアウト時刻
        _h, _m = map(int, TIMEOUT_TIME.split(":"))
        timeout_ts = date_norm + pd.Timedelta(hours=_h, minutes=_m)

        # エントリー後の足で決済判定
        after = df_day[(df_day.index > entry_ts) & (df_day.index <= timeout_ts)]

        result_pips = None
        exit_ts     = None
        exit_price  = None
        exit_reason = None

        for ts2, row2 in after.iterrows():
            if direction == "SHORT":
                if row2["High"] >= sl:
                    exit_price, exit_reason, exit_ts = sl, "SL", ts2
                    result_pips = (entry - sl) / pip
                    break
                if row2["Low"] <= tp:
                    exit_price, exit_reason, exit_ts = tp, "TP", ts2
                    result_pips = (entry - tp) / pip
                    break
            else:
                if row2["Low"] <= sl:
                    exit_price, exit_reason, exit_ts = sl, "SL", ts2
                    result_pips = (sl - entry) / pip
                    break
                if row2["High"] >= tp:
                    exit_price, exit_reason, exit_ts = tp, "TP", ts2
                    result_pips = (tp - entry) / pip
                    break

        # タイムアウト
        if result_pips is None:
            bar = df_day[df_day.index <= timeout_ts]
            if bar.empty:
                continue
            exit_price = bar.iloc[-1]["Close"]
            exit_ts    = bar.index[-1]
            exit_reason = "TIMEOUT"
            if direction == "SHORT":
                result_pips = (entry - exit_price) / pip
            else:
                result_pips = (exit_price - entry) / pip

        sl_pips = abs(entry - sl_raw) / pip
        rr      = result_pips / sl_pips if sl_pips > 0 else 0

        trades.append({
            "pair":        pair,
            "date":        date_norm.date(),
            "direction":   direction,
            "entry_ts":    entry_ts,
            "exit_ts":     exit_ts,
            "entry":       round(entry, 5),
            "tp":          round(tp, 5),
            "sl":          round(sl, 5),
            "exit_price":  round(exit_price, 5),
            "tok_high":    round(tok_high, 5),
            "tok_low":     round(tok_low, 5),
            "tok_range_p": round(tok_range / pip, 1),
            "result_pips": round(result_pips, 1),
            "sl_pips":     round(sl_pips, 1),
            "rr":          round(rr, 2),
            "exit_reason": exit_reason,
            "win":         result_pips > 0,
            "fake_pips":   fake_pips,
            "tp_ratio":    tp_ratio,
        })

    return pd.DataFrame(trades)


# ─────────────────────────────────────────────────────────────────
# 統計計算
# ─────────────────────────────────────────────────────────────────
def calc_stats(df: pd.DataFrame, pair: str = "ALL",
               fake_pips: float = None, tp_ratio: float = None) -> dict:
    if df.empty:
        return {"pair": pair, "n": 0,
                "fake_pips": fake_pips, "tp_ratio": tp_ratio}

    n    = len(df)
    wins = int(df["win"].sum())
    wr   = wins / n * 100

    pos_p = df[df["result_pips"] > 0]["result_pips"].sum()
    neg_p = abs(df[df["result_pips"] < 0]["result_pips"].sum())
    pf    = pos_p / neg_p if neg_p > 0 else np.inf

    avg_win  = df[df["result_pips"] > 0]["result_pips"].mean() if wins      > 0 else 0
    avg_loss = df[df["result_pips"] < 0]["result_pips"].mean() if (n-wins) > 0 else 0
    avg_rr   = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    equity   = df["result_pips"].cumsum().values
    peak     = np.maximum.accumulate(equity)
    drawdown = equity - peak
    max_dd   = drawdown.min()           # pips (負値)
    max_dd_abs = abs(max_dd)

    # DD% : peak基準 (peak>0 のときのみ有意)
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

    adopt = (sharpe >= 1.0) and (dd_pct < 20.0) and (n >= 200)

    return {
        "pair":       pair,
        "fake_pips":  fake_pips,
        "tp_ratio":   tp_ratio,
        "n":          n,
        "wins":       wins,
        "wr_%":       round(wr, 1),
        "pf":         round(pf, 3),
        "avg_rr":     round(avg_rr, 2),
        "avg_win_p":  round(avg_win, 1),
        "avg_loss_p": round(avg_loss, 1),
        "total_p":    round(total_pips, 1),
        "max_dd_p":   round(max_dd, 1),
        "dd_%":       round(dd_pct, 1),
        "sharpe":     round(sharpe, 2),
        "adopt":      adopt,
        "n_tp":       int((df["exit_reason"] == "TP").sum()),
        "n_sl":       int((df["exit_reason"] == "SL").sum()),
        "n_to":       int((df["exit_reason"] == "TIMEOUT").sum()),
    }


# ─────────────────────────────────────────────────────────────────
# 月次損益テーブル
# ─────────────────────────────────────────────────────────────────
def monthly_table(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    df2["date"] = pd.to_datetime(df2["date"])
    df2["YM"]   = df2["date"].dt.to_period("M")
    tbl = df2.groupby(["pair", "YM"])["result_pips"].sum().unstack("pair")
    tbl["TOTAL"] = tbl.sum(axis=1)
    return tbl


# ─────────────────────────────────────────────────────────────────
# パラメータ感度分析
# ─────────────────────────────────────────────────────────────────
def run_sweep(pair_data: dict, configs: dict) -> pd.DataFrame:
    """
    FAKE_PIPS × TP_RATIO の全組み合わせを総当たり。
    pair_data: {pair: (df_1h, df_5m)}
    """
    results = []
    total = len(SWEEP_FAKE_PIPS) * len(SWEEP_TP_RATIO)
    done  = 0

    for fp in SWEEP_FAKE_PIPS:
        for tr in SWEEP_TP_RATIO:
            done += 1
            print(f"  sweep [{done}/{total}] fake_pips={fp}, tp_ratio={tr}")
            all_trades = []
            for pair, (df_1h, df_5m) in pair_data.items():
                if df_1h is None or df_5m is None:
                    continue
                t = run_backtest(pair, df_1h, df_5m, configs[pair],
                                 fake_pips=fp, tp_ratio=tr)
                if not t.empty:
                    all_trades.append(t)
            if not all_trades:
                continue
            combined = pd.concat(all_trades, ignore_index=True)
            s = calc_stats(combined, "ALL", fake_pips=fp, tp_ratio=tr)
            results.append(s)

    return pd.DataFrame(results)


# ─────────────────────────────────────────────────────────────────
# チャート描画
# ─────────────────────────────────────────────────────────────────
def plot_results(all_trades: pd.DataFrame, stats_list: list):
    pairs  = list(PAIRS.keys())
    colors = {"GBPJPY": "#2196F3", "EURJPY": "#4CAF50", "GBPUSD": "#FF5722"}

    n_pairs = len(pairs)
    fig = plt.figure(figsize=(16, 13))
    gs  = gridspec.GridSpec(3, max(n_pairs, 1), figure=fig, hspace=0.50, wspace=0.35)

    # ── 上段: Equity Curve (全ペア合算) ─────────────────────────
    ax_eq = fig.add_subplot(gs[0, :])
    for pair in pairs:
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
        # DD塗り
        peak_all = np.maximum.accumulate(eq_all.values)
        ax_eq.fill_between(combined_sorted["entry_ts"].values,
                           eq_all.values, peak_all,
                           alpha=0.12, color="red", label="_nolegend_")

    ax_eq.axhline(0, color="gray", linestyle=":", linewidth=0.8)
    ax_eq.set_title("Equity Curve (pips) — Tokyo→London Fakeout Reversal",
                    fontsize=11, fontweight="bold")
    ax_eq.set_ylabel("Cumulative pips")
    ax_eq.legend(loc="upper left", fontsize=9)
    ax_eq.grid(True, alpha=0.3)

    # ── 中段: 月次棒グラフ ─────────────────────────────────────
    for i, pair in enumerate(pairs):
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

    # ── 下段: 統計サマリーテーブル ──────────────────────────────
    ax_tbl = fig.add_subplot(gs[2, :])
    ax_tbl.axis("off")

    col_keys  = ["pair", "n", "wr_%", "pf", "avg_rr", "total_p",
                 "max_dd_p", "dd_%", "sharpe", "n_tp", "n_sl", "n_to", "adopt"]
    col_names = ["Pair", "N", "WR%", "PF", "AvgRR", "Total(p)",
                 "MaxDD(p)", "DD%", "Sharpe", "TP", "SL", "TO", "Adopt?"]

    disp_stats = [s for s in stats_list if s.get("n", 0) > 0]
    table_data = [[str(s.get(k, "")) for k in col_keys] for s in disp_stats]

    tbl = ax_tbl.table(cellText=table_data, colLabels=col_names,
                       loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5)
    tbl.scale(1, 1.6)

    adopt_idx = col_keys.index("adopt")
    for i, s in enumerate(disp_stats):
        cell = tbl[i + 1, adopt_idx]
        cell.set_facecolor("#C8E6C9" if s.get("adopt") else "#FFCDD2")

    ax_tbl.set_title("Strategy Statistics", fontsize=10, fontweight="bold", pad=6)

    # データ期間注記
    if not all_trades.empty:
        d0 = pd.to_datetime(all_trades["date"].min())
        d1 = pd.to_datetime(all_trades["date"].max())
        fig.text(0.01, 0.01,
                 f"Data: {d0.date()} ~ {d1.date()} | "
                 f"FAKE_PIPS={FAKE_PIPS}, TP_RATIO={TP_RATIO}, "
                 f"Spread: GBPJPY=3p/EURJPY=2p/GBPUSD=1.5p",
                 fontsize=7, color="gray")

    plt.savefig(CHART_PNG, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Chart saved: {CHART_PNG}")


# ─────────────────────────────────────────────────────────────────
# 感度分析ヒートマップ
# ─────────────────────────────────────────────────────────────────
def plot_sweep(sweep_df: pd.DataFrame):
    if sweep_df.empty:
        return
    sweep_png = CHART_PNG.replace(".png", "_sweep.png")

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    metrics = [("pf", "Profit Factor"), ("wr_%", "Win Rate %"), ("sharpe", "Sharpe")]

    for ax, (metric, title) in zip(axes, metrics):
        pivot = sweep_df.pivot(index="fake_pips", columns="tp_ratio",
                               values=metric).astype(float)
        im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn",
                       vmin=pivot.values.min(), vmax=pivot.values.max())
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(c) for c in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([str(r) for r in pivot.index])
        ax.set_xlabel("TP_RATIO")
        ax.set_ylabel("FAKE_PIPS")
        ax.set_title(title, fontsize=11, fontweight="bold")
        plt.colorbar(im, ax=ax)
        # セル内に数値表示
        for r in range(pivot.shape[0]):
            for c in range(pivot.shape[1]):
                ax.text(c, r, f"{pivot.values[r, c]:.2f}",
                        ha="center", va="center", fontsize=8)

    plt.suptitle("Parameter Sensitivity Analysis (ALL pairs)",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(sweep_png, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Sweep chart: {sweep_png}")


# ─────────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true",
                        help="パラメータ感度分析を実行")
    parser.add_argument("--mt5", type=str, default=None,
                        help="MT5 CSVディレクトリパス")
    args = parser.parse_args()

    mt5_dir = args.mt5 or MT5_DATA_DIR

    print("=" * 62)
    print("Tokyo→London Fakeout Reversal Backtest v2")
    print(f"  FAKE_PIPS={FAKE_PIPS}, TP_RATIO={TP_RATIO}")
    print(f"  Window={SESSION_START}-{SESSION_END} UTC, Timeout={TIMEOUT_TIME}")
    if mt5_dir:
        print(f"  MT5 data dir: {mt5_dir}")
    else:
        print(f"  Data: yfinance (5m = last {HIST_DAYS_5M}d only)")
    print("=" * 62)

    # ── データ取得 ────────────────────────────────────────────
    pair_data = {}
    for pair, cfg in PAIRS.items():
        print(f"\n[{pair}]")
        df_1h, df_5m = fetch_data(pair, cfg["ticker"], mt5_dir)
        pair_data[pair] = (df_1h, df_5m)

    # ── メインバックテスト ────────────────────────────────────
    all_trades  = []
    stats_list  = []

    for pair, (df_1h, df_5m) in pair_data.items():
        if df_1h is None or df_5m is None:
            print(f"  [{pair}] SKIP")
            continue
        print(f"\n[{pair}] Running BT...")
        trades_df = run_backtest(pair, df_1h, df_5m, PAIRS[pair])
        print(f"  Trades: {len(trades_df)}")
        if not trades_df.empty:
            all_trades.append(trades_df)
            s = calc_stats(trades_df, pair, FAKE_PIPS, TP_RATIO)
            stats_list.append(s)
            print(f"  WR={s['wr_%']}%  PF={s['pf']}  Sharpe={s['sharpe']}  "
                  f"DD%={s['dd_%']}%  n={s['n']}  Adopt={s['adopt']}")
        else:
            stats_list.append({"pair": pair, "n": 0})

    if not all_trades:
        print("\nERROR: No trades generated.")
        print("Tip: VPS の MT5 5m CSVデータを --mt5 で指定すると長期BT可能")
        return

    combined = pd.concat(all_trades, ignore_index=True)
    s_all    = calc_stats(combined, "ALL", FAKE_PIPS, TP_RATIO)
    stats_list.append(s_all)
    print(f"\n[ALL] WR={s_all['wr_%']}%  PF={s_all['pf']}  "
          f"Sharpe={s_all['sharpe']}  DD%={s_all['dd_%']}%  "
          f"n={s_all['n']}  Adopt={s_all['adopt']}")

    # ── CSV出力 ───────────────────────────────────────────────
    combined.to_csv(TRADE_CSV, index=False)
    pd.DataFrame(stats_list).to_csv(STATS_CSV, index=False)
    print(f"\n  Trades: {TRADE_CSV}")
    print(f"  Stats : {STATS_CSV}")

    # ── 月次テーブル ──────────────────────────────────────────
    print("\n── Monthly PnL (pips) ─────────────────────────────────────")
    mtbl = monthly_table(combined)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 120)
    print(mtbl.to_string())

    # ── チャート ──────────────────────────────────────────────
    plot_results(combined, stats_list)

    # ── 感度分析 ──────────────────────────────────────────────
    if args.sweep:
        print("\n── Parameter Sweep ─────────────────────────────────────────")
        sweep_df = run_sweep(pair_data, PAIRS)
        if not sweep_df.empty:
            sweep_df.to_csv(SWEEP_CSV, index=False)
            print(sweep_df[["fake_pips", "tp_ratio", "n", "wr_%",
                            "pf", "sharpe"]].to_string(index=False))
            print(f"\n  Sweep CSV: {SWEEP_CSV}")
            try:
                plot_sweep(sweep_df)
            except Exception as e:
                print(f"  Sweep chart error: {e}")

    # ── 採用判定サマリー ──────────────────────────────────────
    print("\n── Adoption (Sharpe≥1.0, DD<20%, n≥200) ───────────────────")
    for s in stats_list:
        if s.get("n", 0) == 0:
            continue
        mark = "✅ ADOPT" if s.get("adopt") else "❌ REJECT"
        print(f"  {s['pair']:8s}: {mark}  "
              f"(Sharpe={s.get('sharpe','N/A')}, "
              f"DD={s.get('dd_%','N/A')}%, n={s.get('n','N/A')})")

    # ── データ不足の場合の指示 ────────────────────────────────
    if s_all["n"] < 200:
        print(f"""
⚠️  サンプル数不足 (n={s_all['n']} < 200) — 統計的信頼性が低い
   ロング期間BTには VPS の MT5 5m CSVデータが必要:

   VPS → ローカルへ転送例 (scp):
     scp Administrator@<VPS_IP>:C:/Users/Administrator/fx_bot/data/GBPJPY_M5.csv ./
     scp Administrator@<VPS_IP>:C:/Users/Administrator/fx_bot/data/EURJPY_M5.csv ./
     scp Administrator@<VPS_IP>:C:/Users/Administrator/fx_bot/data/GBPUSD_M5.csv ./
     scp Administrator@<VPS_IP>:C:/Users/Administrator/fx_bot/data/GBPJPY_H1.csv ./
     ...

   実行例:
     python session_fakeout_bt.py --mt5 /path/to/mt5_data/
""")


if __name__ == "__main__":
    main()
