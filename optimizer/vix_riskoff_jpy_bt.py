"""
Strategy 5: VIX Spike x Multi-Sentiment JPY Momentum BT
Trigger: VIX daily change > +15% -> SHORT JPY crosses
D1 price data, 2023-2026
yfinance for VIX, Crypto Fear&Greed for sentiment supplement
"""

import warnings
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from pathlib import Path

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent.parent / "data"

PAIRS = ["GBPJPY", "USDJPY"]  # AUDJPY not in D1 data
VIX_SPIKE_THRESHOLD = 0.15   # +15% daily change
EMA200_PERIOD = 200
ATR_PERIOD = 14
SL_ATR_MULT = 2.0
TP_ATR_MULT = 3.0
MAX_HOLD_DAYS = 5
CRYPTO_FG_DROP = 10

SPREAD_PIPS = {"GBPJPY": 1.2, "USDJPY": 0.5, "AUDJPY": 1.5}
PIP_VALUE = {"GBPJPY": 0.006, "USDJPY": 0.007, "AUDJPY": 0.006}


def load_vix():
    print("  Downloading VIX data via yfinance...")
    try:
        raw = yf.download("^VIX", start="2021-01-01", end="2026-06-01", progress=False)
        # Handle multi-level columns from newer yfinance
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.droplevel(1)
        close = raw["Close"].squeeze()
        vix = pd.DataFrame({"vix_close": close})
        vix.index = pd.to_datetime(vix.index).tz_localize(None)
        vix["vix_pct"] = vix["vix_close"].pct_change()
        vix["vix_spike"] = vix["vix_pct"] > VIX_SPIKE_THRESHOLD
        print(f"    VIX loaded: {vix.index[0].date()} - {vix.index[-1].date()}, spikes: {int(vix['vix_spike'].sum())}")
        return vix
    except Exception as e:
        print(f"    VIX download failed: {e}")
        return None


def load_crypto_fg():
    """Crypto Fear & Greed daily (official free API)"""
    print("  Downloading Crypto F&G data...")
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=2000", timeout=15)
        data = resp.json()["data"]
        df = pd.DataFrame(data)[["timestamp", "value"]]
        df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.normalize()
        df["cfg"] = df["value"].astype(float)
        df["cfg_change"] = df["cfg"].diff(-1)  # positive = dropped today vs yesterday
        df = df.set_index("date")[["cfg", "cfg_change"]].sort_index()
        print(f"    Crypto F&G loaded: {len(df)} records")
        return df
    except Exception as e:
        print(f"    Crypto F&G failed: {e}")
        return None


def load_price_d1(pair):
    path = DATA_DIR / f"{pair}_D1.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    date_col = next((c for c in df.columns if "date" in c), df.columns[0])
    df["date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None).dt.normalize()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    df["atr"] = compute_atr(df, ATR_PERIOD)
    df["ema200"] = df["close"].ewm(span=EMA200_PERIOD, min_periods=50).mean()
    # 5-day consecutive candle direction
    df["dn5"] = (df["close"] < df["close"].shift(1)).rolling(5).sum() >= 4
    return df


def compute_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period).mean()


def run_bt(pair, price_df, vix_df, cfg_df=None):
    trades = []
    price_indexed = price_df.set_index("date")

    for idx in range(len(price_df) - MAX_HOLD_DAYS - 1):
        row = price_df.iloc[idx]
        date = row["date"]

        # Get VIX data for this date
        vix_match = vix_df[vix_df.index <= date]
        if vix_match.empty:
            continue
        vix_row = vix_match.iloc[-1]

        vix_spike = bool(vix_row["vix_spike"])
        if not vix_spike:
            continue

        # Crypto F&G condition (supplementary)
        cfg_drop = False
        if cfg_df is not None and date in cfg_df.index:
            cfg_drop = cfg_df.loc[date, "cfg_change"] >= CRYPTO_FG_DROP

        # At least 1 of 2 conditions (VIX always + optional crypto)
        # For BT, VIX spike alone qualifies (main condition)

        # HTF filter: price below EMA200 (risk-off = JPY strength = cross down)
        close = row["close"]
        ema200 = row["ema200"]
        atr = row["atr"]
        if pd.isna(atr) or pd.isna(ema200) or atr == 0:
            continue

        # JPY cross SHORT: price should be in downtrend (or just above EMA200 on spike day)
        # Relaxed: enter short on any VIX spike day when price is above EMA200 (riskoff reversal)
        if close < ema200 * 0.99:
            continue  # already too extended, skip

        # Entry: next bar open
        next_bar = price_df.iloc[idx + 1]
        entry_px = next_bar["open"]
        sl_price = entry_px + atr * SL_ATR_MULT
        tp_price = entry_px - atr * TP_ATR_MULT

        result = "TIMEOUT"
        exit_px = None
        hold_days = 0
        for j in range(idx + 2, min(idx + 2 + MAX_HOLD_DAYS, len(price_df))):
            bar = price_df.iloc[j]
            hold_days += 1
            # Check SL first (bar high)
            if bar["high"] >= sl_price:
                result = "SL"
                exit_px = sl_price
                break
            # Check TP (bar low)
            if bar["low"] <= tp_price:
                result = "TP"
                exit_px = tp_price
                break
            # VIX reversal exit: if VIX falls back >10% from spike
            bar_date = bar["date"]
            if bar_date in vix_df.index:
                vix_now = vix_df.loc[bar_date, "vix_close"]
                vix_entry = vix_row["vix_close"]
                if vix_now < vix_entry * 0.90:
                    result = "VIX_REVERT"
                    exit_px = bar["close"]
                    break

        if result == "TIMEOUT":
            exit_px = price_df.iloc[min(idx + 1 + MAX_HOLD_DAYS, len(price_df) - 1)]["close"]

        if exit_px is None:
            continue

        pnl_raw = (entry_px - exit_px)  # short: profit = price falls
        trades.append({
            "date": date, "pair": pair,
            "vix_pct": round(float(vix_row["vix_pct"]) * 100, 2),
            "cfg_drop": cfg_drop,
            "entry": entry_px, "exit": exit_px,
            "result": result, "hold_days": hold_days,
            "pnl_raw": pnl_raw,
            "sl": sl_price, "tp": tp_price,
        })

    return pd.DataFrame(trades)


def print_results(df_all):
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
    print(f"Strategy 5: VIX Risk-Off JPY BT Results")
    print(f"{'='*60}")
    print(f"Period: {df_all['date'].min().date()} - {df_all['date'].max().date()}")
    print(f"Total trades: {len(df_all)}")
    print(f"Win rate:     {wr:.1f}%")
    print(f"PF:           {pf:.3f}")
    print(f"Max DD:       {dd:.4f} price units")
    print(f"VIX spike threshold: +{VIX_SPIKE_THRESHOLD*100:.0f}%")

    print(f"\n--- By Pair ---")
    for pair in df_all["pair"].unique():
        sub = df_all[df_all["pair"] == pair]
        sw = sub[sub["pnl_raw"] > 0]
        gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
        sub_wr = len(sw) / len(sub) * 100 if len(sub) > 0 else 0
        avg_hold = sub["hold_days"].mean()
        print(f"  {pair}: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}, avg_hold={avg_hold:.1f}d")

    print(f"\n--- By Result ---")
    for rt in df_all["result"].unique():
        sub = df_all[df_all["result"] == rt]
        avg_pnl = sub["pnl_raw"].mean()
        print(f"  {rt}: n={len(sub)}, avg_pnl={avg_pnl:.4f}")

    print(f"\n--- VIX Spike Distribution ---")
    print(f"  Avg VIX % change on entry: {df_all['vix_pct'].mean():.1f}%")
    print(f"  Max VIX % change: {df_all['vix_pct'].max():.1f}%")
    print(f"  Trades with Crypto F&G drop: {df_all['cfg_drop'].sum()}/{len(df_all)}")


def main():
    print("=== Strategy 5: VIX Risk-Off JPY BT ===")

    vix_df = load_vix()
    if vix_df is None:
        print("VIX data unavailable. Abort.")
        return

    cfg_df = load_crypto_fg()

    all_trades = []
    for pair in PAIRS:
        price_df = load_price_d1(pair)
        if price_df is None:
            print(f"  No D1 data for {pair}, skip.")
            continue
        print(f"\n[BT] {pair}...")
        t = run_bt(pair, price_df, vix_df, cfg_df)
        print(f"  Trades: {len(t)}")
        all_trades.append(t)

    df_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    print_results(df_all)

    if not df_all.empty:
        out = Path(__file__).parent / "vix_riskoff_jpy_bt_results.csv"
        df_all.to_csv(out, index=False)
        print(f"\nSaved: {out}")

        # Sensitivity analysis: different VIX thresholds
        print(f"\n--- Sensitivity: VIX threshold ---")
        for th in [0.10, 0.15, 0.20, 0.25]:
            sub = df_all[df_all["vix_pct"] >= th * 100]
            if len(sub) == 0:
                continue
            sw = sub[sub["pnl_raw"] > 0]
            gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
            sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
            sub_wr = len(sw) / len(sub) * 100
            print(f"  VIX>+{th*100:.0f}%: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}")


if __name__ == "__main__":
    main()
