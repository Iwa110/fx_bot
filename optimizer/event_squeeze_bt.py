"""
Strategy 1: Pre-Event Squeeze Breakout BT (Simplified, 1h data)
Events: NFP (first Friday of month, 13:30 UTC) + US CPI (~12th of month, 12:30 UTC)
Squeeze: ATR compression < 50th pct + BB Width < 20th pct in 2h before event
Entry: next H1 bar open (breakout direction = HTF EMA direction)
SL = 1.5xATR, TP = 2.5xSL, max hold = 4h (4 x H1 bars)
"""

import warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent.parent / "data"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]
SL_ATR_MULT = 1.5
TP_RR = 2.5
MAX_HOLD_BARS = 4  # H1 bars
ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
ATR_COMPRESS_PCT = 50   # ATR below this percentile of 20-bar rolling
BBW_COMPRESS_PCT = 20   # BB Width below this percentile of 100-bar rolling
SPREAD_PIPS = {"EURUSD": 0.5, "GBPUSD": 0.7, "USDJPY": 0.5, "GBPJPY": 1.2}
SPREAD_UNITS = {"EURUSD": 0.00005, "GBPUSD": 0.00007, "USDJPY": 0.05, "GBPJPY": 0.12}


# --- Event calendar ---
def generate_nfp_dates(start_year=2024, end_year=2026):
    """NFP = first Friday of each month at 13:30 UTC"""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            for day in range(1, 8):
                try:
                    d = pd.Timestamp(year, month, day, 13, 30)
                    if d.weekday() == 4:  # Friday
                        dates.append(d)
                        break
                except Exception:
                    pass
    return dates


def generate_cpi_dates(start_year=2024, end_year=2026):
    """US CPI roughly around 10th-15th of month at 12:30 UTC
    Approximated as 2nd Tuesday of each month (historical pattern)"""
    # Actual CPI release dates for BT accuracy
    known_cpi = [
        "2024-01-11 12:30", "2024-02-13 12:30", "2024-03-12 12:30",
        "2024-04-10 12:30", "2024-05-15 12:30", "2024-06-12 12:30",
        "2024-07-11 12:30", "2024-08-14 12:30", "2024-09-11 12:30",
        "2024-10-10 12:30", "2024-11-13 12:30", "2024-12-11 12:30",
        "2025-01-15 12:30", "2025-02-12 12:30", "2025-03-12 12:30",
        "2025-04-10 12:30", "2025-05-13 12:30", "2025-06-11 12:30",
        "2025-07-11 12:30", "2025-08-12 12:30", "2025-09-10 12:30",
        "2025-10-15 12:30", "2025-11-13 12:30", "2025-12-10 12:30",
        "2026-01-14 12:30", "2026-02-11 12:30", "2026-03-11 12:30",
        "2026-04-10 12:30",
    ]
    return [pd.Timestamp(d) for d in known_cpi]


# --- Price helpers ---
def load_price_1h(pair):
    path = DATA_DIR / f"{pair}_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    # Handle dual-column format: ,open,high,low,close,volume,Open,High,Low,Close,Volume
    if df.columns[0] in ("", "Unnamed: 0"):
        df = df.rename(columns={df.columns[0]: "date"})
    else:
        date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
        df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    # Use uppercase OHLC if lowercase missing
    for col, ucol in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")]:
        if col not in df.columns and ucol in df.columns:
            df[col] = df[ucol]
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    df["atr"] = compute_atr(df)
    # BB Width
    bb_mid = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_width"] = (bb_std * BB_STD * 2) / bb_mid
    # HTF EMA (50 bars = ~50h as trend filter)
    df["ema50"] = df["close"].ewm(span=50).mean()
    return df


def compute_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period).mean()


# --- Backtest ---
def run_bt(pair, price_df, event_times):
    trades = []
    price_df = price_df.copy()
    spread = SPREAD_UNITS.get(pair, 0.0001)

    for ev_time in event_times:
        # Pre-event bar: H1 bar 1h before event (bar index at ev_time - 2h to ev_time - 1h)
        pre_start = ev_time - pd.Timedelta(hours=2)
        pre_end = ev_time - pd.Timedelta(minutes=1)
        pre_bars = price_df[(price_df["date"] >= pre_start) & (price_df["date"] <= pre_end)]
        if len(pre_bars) < 1:
            continue
        pre_bar = pre_bars.iloc[-1]

        # Rolling percentile thresholds from past 100 bars before event
        lookback_end = pre_bar.name
        lookback_start = max(0, lookback_end - 100)
        lookback = price_df.iloc[lookback_start:lookback_end]
        if len(lookback) < 20:
            continue

        atr_now = pre_bar["atr"]
        bbw_now = pre_bar["bb_width"]
        if pd.isna(atr_now) or pd.isna(bbw_now):
            continue

        atr_pct = (lookback["atr"].dropna() <= atr_now).sum() / len(lookback["atr"].dropna()) * 100
        bbw_pct = (lookback["bb_width"].dropna() <= bbw_now).sum() / len(lookback["bb_width"].dropna()) * 100

        # Squeeze conditions
        atr_squeeze = atr_pct <= ATR_COMPRESS_PCT
        bbw_squeeze = bbw_pct <= BBW_COMPRESS_PCT

        if not (atr_squeeze and bbw_squeeze):
            continue

        # HTF direction from EMA50
        ema50 = pre_bar["ema50"]
        close = pre_bar["close"]
        if close > ema50:
            direction = 1  # BUY
        elif close < ema50:
            direction = -1  # SELL
        else:
            continue

        # Entry: bar at ev_time (event bar open)
        event_bars = price_df[(price_df["date"] >= ev_time) & (price_df["date"] < ev_time + pd.Timedelta(hours=2))]
        if len(event_bars) < 1:
            continue
        entry_bar = event_bars.iloc[0]
        entry_px = entry_bar["open"] + spread * direction
        atr = atr_now
        sl = atr * SL_ATR_MULT
        tp = sl * TP_RR

        sl_price = entry_px - sl * direction
        tp_price = entry_px + tp * direction

        result = "TIMEOUT"
        exit_px = None
        hold_bars = 0
        entry_idx = entry_bar.name
        for j in range(entry_idx + 1, min(entry_idx + 1 + MAX_HOLD_BARS, len(price_df))):
            bar = price_df.iloc[j]
            hold_bars += 1
            if direction > 0:
                if bar["low"] <= sl_price:
                    result = "SL"; exit_px = sl_price; break
                if bar["high"] >= tp_price:
                    result = "TP"; exit_px = tp_price; break
            else:
                if bar["high"] >= sl_price:
                    result = "SL"; exit_px = sl_price; break
                if bar["low"] <= tp_price:
                    result = "TP"; exit_px = tp_price; break

        if result == "TIMEOUT":
            timeout_idx = min(entry_idx + MAX_HOLD_BARS, len(price_df) - 1)
            exit_px = price_df.iloc[timeout_idx]["close"]

        if exit_px is None:
            continue

        pnl_raw = (exit_px - entry_px) * direction
        trades.append({
            "event_time": ev_time, "pair": pair,
            "direction": "BUY" if direction > 0 else "SELL",
            "atr_pct": round(atr_pct, 1), "bbw_pct": round(bbw_pct, 1),
            "entry": entry_px, "exit": exit_px,
            "result": result, "hold_bars": hold_bars,
            "pnl_raw": pnl_raw, "atr": atr,
        })

    return pd.DataFrame(trades)


def print_results(df_all, event_type="ALL"):
    if df_all.empty:
        print("No trades generated.")
        return
    wins = df_all[df_all["pnl_raw"] > 0]
    losses = df_all[df_all["pnl_raw"] <= 0]
    gross_win = wins["pnl_raw"].sum()
    gross_loss = abs(losses["pnl_raw"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(df_all) * 100 if len(df_all) > 0 else 0
    cum = df_all["pnl_raw"].cumsum()
    dd = (cum - cum.cummax()).min()

    print(f"\n{'='*60}")
    print(f"Strategy 1: Pre-Event Squeeze Breakout BT ({event_type})")
    print(f"{'='*60}")
    start = df_all["event_time"].min()
    end = df_all["event_time"].max()
    print(f"Period: {start.date()} - {end.date()}")
    print(f"Total trades: {len(df_all)}")
    print(f"Win rate:     {wr:.1f}%")
    print(f"PF:           {pf:.3f}")
    print(f"Max DD:       {dd:.6f} price units")
    print(f"Avg ATR pct on entry: {df_all['atr_pct'].mean():.1f}%")
    print(f"Avg BBW pct on entry: {df_all['bbw_pct'].mean():.1f}%")

    print(f"\n--- By Pair ---")
    for pair in df_all["pair"].unique():
        sub = df_all[df_all["pair"] == pair]
        sw = sub[sub["pnl_raw"] > 0]
        gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
        sub_wr = len(sw) / len(sub) * 100 if len(sub) > 0 else 0
        print(f"  {pair}: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}")

    print(f"\n--- By Result ---")
    for rt in df_all["result"].unique():
        sub = df_all[df_all["result"] == rt]
        print(f"  {rt}: n={len(sub)} ({len(sub)/len(df_all)*100:.0f}%)")

    print(f"\n--- By Direction ---")
    for d in df_all["direction"].unique():
        sub = df_all[df_all["direction"] == d]
        sw = sub[sub["pnl_raw"] > 0]
        gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
        print(f"  {d}: n={len(sub)}, PF={sub_pf:.3f}")


def main():
    print("=== Strategy 1: Pre-Event Squeeze Breakout BT ===")

    nfp_dates = generate_nfp_dates(2024, 2026)
    cpi_dates = generate_cpi_dates()
    all_events = sorted(nfp_dates + cpi_dates)
    print(f"Events in period: NFP={len(nfp_dates)}, CPI={len(cpi_dates)}, Total={len(all_events)}")

    all_trades = []
    nfp_trades = []
    cpi_trades = []

    for pair in PAIRS:
        price_df = load_price_1h(pair)
        if price_df is None:
            print(f"  No 1h data for {pair}, skip.")
            continue
        print(f"\n[BT] {pair} (1h bars: {len(price_df)}, {price_df['date'].iloc[0].date()} - {price_df['date'].iloc[-1].date()})")

        t_nfp = run_bt(pair, price_df, nfp_dates)
        t_cpi = run_bt(pair, price_df, cpi_dates)
        print(f"  NFP trades: {len(t_nfp)}, CPI trades: {len(t_cpi)}")
        all_trades.extend([t_nfp, t_cpi])
        nfp_trades.append(t_nfp)
        cpi_trades.append(t_cpi)

    df_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    df_nfp = pd.concat(nfp_trades, ignore_index=True) if nfp_trades else pd.DataFrame()
    df_cpi = pd.concat(cpi_trades, ignore_index=True) if cpi_trades else pd.DataFrame()

    print_results(df_all, "NFP+CPI")
    if not df_nfp.empty:
        print_results(df_nfp, "NFP only")
    if not df_cpi.empty:
        print_results(df_cpi, "CPI only")

    if not df_all.empty:
        out = Path(__file__).parent / "event_squeeze_bt_results.csv"
        df_all.to_csv(out, index=False)
        print(f"\nSaved: {out}")

        # Sensitivity: tighter squeeze filter
        print(f"\n--- Sensitivity: squeeze threshold ---")
        for atr_th, bbw_th in [(50, 20), (40, 15), (30, 10)]:
            sub = df_all[(df_all["atr_pct"] <= atr_th) & (df_all["bbw_pct"] <= bbw_th)]
            if len(sub) == 0:
                print(f"  ATR<{atr_th}% + BBW<{bbw_th}%: no trades")
                continue
            sw = sub[sub["pnl_raw"] > 0]
            gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
            sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
            sub_wr = len(sw) / len(sub) * 100
            print(f"  ATR<{atr_th}% + BBW<{bbw_th}%: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}")


if __name__ == "__main__":
    main()
