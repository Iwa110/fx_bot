"""
Strategy D: Interest Rate Differential x Cross-JPY Momentum BT
Pairs: USDJPY / GBPJPY / AUDJPY
Data:
  FRED API (free key at fred.stlouisfed.org)
    USD: DGS2              -- US 2-yr Treasury, daily
    JPY: IRLTST01JPM156N   -- Japan ST rate, monthly (forward-filled)
    GBP: IRLTST01GBM156N   -- UK ST rate,    monthly (forward-filled)
    AUD: IRLTST01AUM156N   -- Australia ST rate, monthly (forward-filled)
  VIX: yfinance ^VIX (daily close)
  Price: local CSV *_1h.csv

Entry conditions (H1):
  1. 7-day spread change > +0.10%pt -> long bias
     7-day spread change < -0.10%pt -> short bias
  2. H1 EMA20 > EMA50 (long) / EMA20 < EMA50 (short)
  3. Price crosses BB mid (MA20) in entry direction
  4. VIX daily close < 25

Exit: TP=2.5xATR(14), SL=1.2xATR(14)
      Stage2 trail: activate=0.70, distance=0.30

Usage:
  python interest_rate_mom_bt.py [--api-key YOUR_FRED_KEY]
  Or set env var: FRED_API_KEY=your_key
"""

import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from pathlib import Path

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent.parent / "data"

# ===== FRED series =====
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {
    "USD": "DGS2",
    "JPY": "IRLTST01JPM156N",
    "GBP": "IRLTST01GBM156N",
    "AUD": "IRLTST01AUM156N",
}
FRED_START = "2021-01-01"

# ===== Pair config =====
PAIR_CFG = {
    "USDJPY": {"base": "USD", "quote": "JPY", "pip_unit": 0.01,   "spread": 0.03},
    "GBPJPY": {"base": "GBP", "quote": "JPY", "pip_unit": 0.01,   "spread": 0.05},
    "AUDJPY": {"base": "AUD", "quote": "JPY", "pip_unit": 0.01,   "spread": 0.04},
}

# ===== Strategy params =====
SPREAD_CHANGE_TH   = 0.10   # %pt threshold for 7-day spread change
SPREAD_CHANGE_DAYS = 7
VIX_MAX            = 25.0
EMA_FAST           = 20
EMA_SLOW           = 50
BB_PERIOD          = 20
ATR_PERIOD         = 14
SL_ATR_MULT        = 1.2
TP_ATR_MULT        = 2.5
STAGE2_ACTIVATE    = 0.70
STAGE2_DISTANCE    = 0.30
MAX_HOLD_BARS      = 200    # H1 bars (~8 days)
COOLDOWN_BARS      = 24     # H1 bars (1 day)


# ===== Data loading =====

def download_fred(series_id, api_key, start=FRED_START):
    params = {
        "series_id":         series_id,
        "api_key":           api_key,
        "observation_start": start,
        "file_type":         "json",
    }
    try:
        resp = requests.get(FRED_BASE, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("observations", [])
        df = pd.DataFrame(data)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna().set_index("date")["value"].sort_index()
        return df
    except Exception as e:
        print(f"  [FRED] {series_id} download error: {e}")
        return None


def build_rate_spreads(api_key):
    """
    Download all FRED series and build daily spread per pair.
    Monthly series are forward-filled to daily.
    Returns dict: { "USDJPY": pd.Series(daily spread), ... }
    """
    print("[1] Downloading FRED interest rate data...")
    rates = {}
    for ccy, sid in FRED_SERIES.items():
        s = download_fred(sid, api_key)
        if s is None:
            print(f"  [WARN] {ccy} ({sid}) unavailable")
            return None
        # Resample to daily, forward-fill monthly data
        daily_idx = pd.date_range(start=s.index.min(), end=s.index.max(), freq="D")
        s = s.reindex(daily_idx).ffill()
        rates[ccy] = s
        print(f"  {ccy} ({sid}): {len(s)} daily obs, last={s.iloc[-1]:.3f}%")

    spreads = {}
    for pair, cfg in PAIR_CFG.items():
        base, quote = cfg["base"], cfg["quote"]
        if base not in rates or quote not in rates:
            print(f"  [WARN] {pair}: missing rate data")
            continue
        # Align on common dates
        combined = pd.concat([rates[base].rename("base"), rates[quote].rename("quote")], axis=1).dropna()
        spread = combined["base"] - combined["quote"]
        spreads[pair] = spread
        print(f"  {pair} spread ({base}-{quote}): {spread.iloc[-1]:.3f}%pt, last_date={spread.index[-1].date()}")
    return spreads


def load_vix(start=FRED_START):
    print("[2] Downloading VIX via yfinance...")
    try:
        raw = yf.download("^VIX", start=start, end="2026-12-31", progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.droplevel(1)
        close = raw["Close"].squeeze()
        vix = close.copy()
        vix.index = pd.to_datetime(vix.index).tz_localize(None).normalize()
        vix.name = "vix"
        print(f"  VIX: {len(vix)} bars, {vix.index[0].date()} - {vix.index[-1].date()}")
        return vix
    except Exception as e:
        print(f"  [WARN] VIX download failed: {e}")
        return None


def load_price_1h(pair):
    path = DATA_DIR / f"{pair}_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    # Handle various date column names
    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    df = df.rename(columns={date_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    for col in ["open", "high", "low", "close"]:
        lc = col
        uc = col.capitalize()
        if lc not in df.columns and uc in df.columns:
            df[lc] = df[uc]
        df[lc] = pd.to_numeric(df[lc], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
    return df


# ===== Indicators =====

def calc_atr(df, period=ATR_PERIOD):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def build_indicators(df):
    close = df["close"]
    df = df.copy()
    df["ema_fast"] = close.ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=EMA_SLOW, adjust=False).mean()
    df["bb_mid"]   = close.rolling(BB_PERIOD).mean()
    df["atr"]      = calc_atr(df)
    return df


# ===== BT core =====

def run_bt_pair(pair, df, spread_series, vix_series):
    """
    Run BT for one pair.
    Returns DataFrame of trades.
    """
    cfg    = PAIR_CFG[pair]
    spread = cfg["spread"] * cfg["pip_unit"]  # fixed spread cost

    df = build_indicators(df)
    df = df.reset_index(drop=True)

    # Precompute daily date for lookup
    df["date_only"] = df["datetime"].dt.normalize()

    # Spread bias lookup: 7-day change per daily date
    spread_7d_change = spread_series.diff(SPREAD_CHANGE_DAYS)
    # spread_7d_change index is daily, we look up by date

    trades       = []
    last_bar     = -COOLDOWN_BARS - 1
    n            = len(df)

    for i in range(max(EMA_SLOW + 1, BB_PERIOD + 1, ATR_PERIOD + 1), n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        atr = df["atr"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        dt       = df["datetime"].iloc[i]
        dt_date  = df["date_only"].iloc[i]

        # --- VIX filter ---
        if vix_series is not None:
            vix_idx = vix_series.index.searchsorted(dt_date, side="right") - 1
            if vix_idx >= 0:
                vix_val = float(vix_series.iloc[vix_idx])
                if not pd.isna(vix_val) and vix_val >= VIX_MAX:
                    continue

        # --- Interest rate spread bias ---
        sp_idx = spread_7d_change.index.searchsorted(dt_date, side="right") - 1
        if sp_idx < 0:
            continue
        sp_chg = float(spread_7d_change.iloc[sp_idx])
        if pd.isna(sp_chg):
            continue
        if sp_chg > SPREAD_CHANGE_TH:
            bias = "long"
        elif sp_chg < -SPREAD_CHANGE_TH:
            bias = "short"
        else:
            continue

        # --- EMA trend alignment ---
        ema_fast_cur  = df["ema_fast"].iloc[i]
        ema_slow_cur  = df["ema_slow"].iloc[i]
        if bias == "long"  and not (ema_fast_cur > ema_slow_cur):
            continue
        if bias == "short" and not (ema_fast_cur < ema_slow_cur):
            continue

        # --- BB mid bounce (price crosses mid in entry direction) ---
        close_prev = df["close"].iloc[i - 1]
        close_cur  = df["close"].iloc[i]
        bb_mid     = df["bb_mid"].iloc[i]
        if pd.isna(bb_mid):
            continue
        if bias == "long"  and not (close_prev < bb_mid <= close_cur):
            continue
        if bias == "short" and not (close_prev > bb_mid >= close_cur):
            continue

        # --- Build trade ---
        sl_dist  = atr * SL_ATR_MULT
        tp_dist  = atr * TP_ATR_MULT
        direction = 1 if bias == "long" else -1
        entry_px  = close_cur + spread * direction

        sl_price = entry_px - sl_dist * direction
        tp_price = entry_px + tp_dist * direction

        # Stage2 trailing SL simulation
        trail_sl  = sl_price
        activated = False
        result    = "TIMEOUT"
        exit_px   = None

        for j in range(i + 1, min(i + MAX_HOLD_BARS + 1, n)):
            h   = df["high"].iloc[j]
            l   = df["low"].iloc[j]
            mid = (h + l) / 2.0

            if direction > 0:
                progress = (mid - entry_px) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid - tp_dist * STAGE2_DISTANCE
                    if new_trail > trail_sl:
                        trail_sl = new_trail
                if l <= trail_sl:
                    result  = "TRAIL_SL" if activated else "SL"
                    exit_px = trail_sl
                    break
                if h >= tp_price:
                    result  = "TP"
                    exit_px = tp_price
                    break
            else:
                progress = (entry_px - mid) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid + tp_dist * STAGE2_DISTANCE
                    if new_trail < trail_sl:
                        trail_sl = new_trail
                if h >= trail_sl:
                    result  = "TRAIL_SL" if activated else "SL"
                    exit_px = trail_sl
                    break
                if l <= tp_price:
                    result  = "TP"
                    exit_px = tp_price
                    break

        if result == "TIMEOUT":
            exit_px = df["close"].iloc[min(i + MAX_HOLD_BARS, n - 1)]

        if exit_px is None:
            continue

        pnl_raw = (exit_px - entry_px) * direction
        trades.append({
            "pair":      pair,
            "datetime":  dt,
            "bias":      bias,
            "sp_chg":    round(sp_chg, 4),
            "entry":     entry_px,
            "exit":      exit_px,
            "result":    result,
            "pnl_raw":   pnl_raw,
            "atr":       atr,
            "sl_dist":   sl_dist,
            "tp_dist":   tp_dist,
        })
        last_bar = i

    return pd.DataFrame(trades)


# ===== Results printing =====

def print_results(df_all):
    if df_all.empty:
        print("No trades generated.")
        return

    wins     = df_all[df_all["pnl_raw"] > 0]
    losses   = df_all[df_all["pnl_raw"] <= 0]
    gw       = wins["pnl_raw"].sum()
    gl       = abs(losses["pnl_raw"].sum())
    pf       = gw / gl if gl > 0 else float("inf")
    wr       = len(wins) / len(df_all) * 100
    cum      = df_all["pnl_raw"].cumsum()
    max_dd   = (cum - cum.cummax()).min()

    print(f"\n{'='*60}")
    print(f"Strategy D: Interest Rate Differential x Cross-JPY Momentum")
    print(f"{'='*60}")
    print(f"Period:       {df_all['datetime'].min().date()} - {df_all['datetime'].max().date()}")
    print(f"Total trades: {len(df_all)}")
    print(f"Win rate:     {wr:.1f}%")
    print(f"PF:           {pf:.3f}")
    print(f"Max DD:       {max_dd:.5f} price units")

    print(f"\n--- By Pair ---")
    for pair in df_all["pair"].unique():
        sub = df_all[df_all["pair"] == pair]
        sw  = sub[sub["pnl_raw"] > 0]
        gl2 = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        spf = sw["pnl_raw"].sum() / gl2 if gl2 > 0 else float("inf")
        swr = len(sw) / len(sub) * 100
        print(f"  {pair}: n={len(sub)}, WR={swr:.0f}%, PF={spf:.3f}")

    print(f"\n--- By Direction ---")
    for d in df_all["bias"].unique():
        sub = df_all[df_all["bias"] == d]
        sw  = sub[sub["pnl_raw"] > 0]
        gl2 = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        spf = sw["pnl_raw"].sum() / gl2 if gl2 > 0 else float("inf")
        print(f"  {d}: n={len(sub)}, PF={spf:.3f}")

    print(f"\n--- By Result ---")
    for rt in sorted(df_all["result"].unique()):
        sub = df_all[df_all["result"] == rt]
        print(f"  {rt}: n={len(sub)} ({len(sub)/len(df_all)*100:.0f}%)")

    print(f"\n--- Sensitivity: SPREAD_CHANGE_TH ---")
    for th in [0.05, 0.10, 0.15, 0.20]:
        sub = df_all[df_all["sp_chg"].abs() >= th]
        if len(sub) == 0:
            print(f"  th={th}: no trades")
            continue
        sw  = sub[sub["pnl_raw"] > 0]
        gl2 = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        spf = sw["pnl_raw"].sum() / gl2 if gl2 > 0 else float("inf")
        swr = len(sw) / len(sub) * 100
        print(f"  th={th}: n={len(sub)}, WR={swr:.0f}%, PF={spf:.3f}")


# ===== Main =====

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.environ.get("FRED_API_KEY", ""),
                        help="FRED API key (free at fred.stlouisfed.org)")
    args = parser.parse_args()

    if not args.api_key:
        print("[ERROR] FRED API key required.")
        print("  Get a free key at https://fred.stlouisfed.org/")
        print("  Pass via: --api-key YOUR_KEY  or  FRED_API_KEY=YOUR_KEY python ...")
        sys.exit(1)

    print("=== Strategy D: Interest Rate Differential x Cross-JPY Momentum BT ===")
    print(f"Pairs:  {list(PAIR_CFG.keys())}")
    print(f"Params: spread_change_th={SPREAD_CHANGE_TH}%pt, VIX<{VIX_MAX}")
    print(f"        EMA_fast={EMA_FAST}, EMA_slow={EMA_SLOW}, BB_period={BB_PERIOD}")
    print(f"        SL={SL_ATR_MULT}xATR, TP={TP_ATR_MULT}xATR")
    print(f"        Stage2: activate={STAGE2_ACTIVATE}, dist={STAGE2_DISTANCE}")

    # Download external data
    spreads = build_rate_spreads(args.api_key)
    if spreads is None:
        print("[ERROR] Failed to build rate spreads. Check FRED API key.")
        sys.exit(1)

    vix = load_vix()

    # Run BT per pair
    print("\n[3] Running backtests...")
    all_trades = []
    for pair in PAIR_CFG:
        if pair not in spreads:
            print(f"  {pair}: spread data missing, skip")
            continue
        df = load_price_1h(pair)
        if df is None:
            print(f"  {pair}: no 1h data at {DATA_DIR}/{pair}_1h.csv, skip")
            continue
        print(f"  {pair}: 1h bars={len(df)}, {df['datetime'].iloc[0].date()} - {df['datetime'].iloc[-1].date()}")
        t = run_bt_pair(pair, df, spreads[pair], vix)
        print(f"    -> {len(t)} trades")
        all_trades.append(t)

    df_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    print_results(df_all)

    if not df_all.empty:
        out = Path(__file__).parent / "interest_rate_mom_bt_results.csv"
        df_all.to_csv(out, index=False)
        print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
