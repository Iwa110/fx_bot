"""
Strategy 3: COT Extreme x Daily Trend Backtest
CFTC TFF Leveraged Funds COT Index (156-week rolling)
Data source: CFTC Socrata API (publicreporting.cftc.gov)
Entry: COT Index >90 (short) or <10 (long) + EMA HTF confirmation
D1 price data, 2023-2026
"""

import json
import warnings
import requests
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent.parent / "data"

# CFTC contract market codes for FX pairs (TFF FutOnly)
PAIR_CFG = {
    "EURUSD": {"cftc_code": "099741", "sign": 1},   # EUR futures: long EUR = price up
    "GBPUSD": {"cftc_code": "096742", "sign": 1},   # GBP futures: long GBP = price up
    "USDJPY": {"cftc_code": "097741", "sign": -1},  # JPY futures: long JPY = USDJPY down
    "AUDUSD": {"cftc_code": "232741", "sign": 1},   # AUD futures
}

COT_INDEX_LOOKBACK = 156  # weeks (~3 years)
EXTREME_HIGH = 80   # looser threshold to get more trades
EXTREME_LOW = 20
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP1_RR = 1.0
TP2_RR = 2.0
MAX_HOLD_DAYS = 14

SOCRATA_BASE = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"


def download_cot_socrata(cftc_codes, start_date="2018-01-01"):
    """Download TFF FutOnly data for specified CFTC codes via Socrata API"""
    all_records = []
    codes_str = ",".join([f"'{c}'" for c in cftc_codes])
    offset = 0
    limit = 5000
    while True:
        params = {
            "$where": f"cftc_contract_market_code in({codes_str}) AND report_date_as_yyyy_mm_dd >= '{start_date}' AND futonly_or_combined = 'FutOnly'",
            "$select": "report_date_as_yyyy_mm_dd,cftc_contract_market_code,lev_money_positions_long,lev_money_positions_short",
            "$limit": limit,
            "$offset": offset,
            "$order": "report_date_as_yyyy_mm_dd ASC",
        }
        try:
            resp = requests.get(SOCRATA_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    API error at offset {offset}: {e}")
            break
        if not data:
            break
        all_records.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return all_records


def build_cot_df(records):
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None)
    df["code"] = df["cftc_contract_market_code"].str.strip()
    df["lev_long"] = pd.to_numeric(df["lev_money_positions_long"], errors="coerce")
    df["lev_short"] = pd.to_numeric(df["lev_money_positions_short"], errors="coerce")
    df["net"] = df["lev_long"] - df["lev_short"]
    return df[["date", "code", "net"]].dropna().sort_values("date").reset_index(drop=True)


def build_cot_index(cot_df, cftc_code, lookback=156):
    s = cot_df[cot_df["code"] == cftc_code].set_index("date")["net"]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    roll_min = s.rolling(lookback, min_periods=max(26, lookback // 4)).min()
    roll_max = s.rolling(lookback, min_periods=max(26, lookback // 4)).max()
    idx = (s - roll_min) / (roll_max - roll_min + 1e-8) * 100
    idx.name = "cot_index"
    return idx


def load_price_d1(pair):
    path = DATA_DIR / f"{pair}_D1.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    # Find OHLC columns - handle any naming
    col_map = {}
    for c in df.columns:
        lc = c.lower()
        if "date" in lc and "date" not in col_map:
            col_map["date"] = c
        elif lc == "open" and "open" not in col_map:
            col_map["open"] = c
        elif lc == "high" and "high" not in col_map:
            col_map["high"] = c
        elif lc == "low" and "low" not in col_map:
            col_map["low"] = c
        elif lc == "close" and "close" not in col_map:
            col_map["close"] = c
    df = df[[col_map[k] for k in ["date", "open", "high", "low", "close"] if k in col_map]].copy()
    df.columns = ["date", "open", "high", "low", "close"][:len(df.columns)]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df["atr"] = compute_atr(df, ATR_PERIOD)
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200, min_periods=50).mean()
    return df


def compute_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period).mean()


def run_cot_bt(pair, cot_index_series, price_df, sign=1):
    trades = []
    # Weekly COT index (resample to weekly Fri, forward-fill for daily use)
    cot_weekly = cot_index_series.resample("W-FRI").last().dropna()

    for i in range(len(cot_weekly)):
        cot_date = cot_weekly.index[i]
        cot_val = float(cot_weekly.iloc[i])
        if np.isnan(cot_val):
            continue

        if cot_val > EXTREME_HIGH:
            direction = -1 * sign  # COT extreme long -> fade -> short price
        elif cot_val < EXTREME_LOW:
            direction = 1 * sign   # COT extreme short -> fade -> long price
        else:
            continue

        # Trade next Monday open (3 days after Friday COT report)
        trade_start = cot_date + pd.Timedelta(days=3)
        future_bars = price_df[price_df["date"] >= trade_start].head(20)
        if len(future_bars) < 5:
            continue

        entry_bar = future_bars.iloc[0]
        atr = entry_bar["atr"]
        if pd.isna(atr) or atr == 0:
            continue

        # HTF filter: EMA50/EMA200 trend must align with direction
        close = entry_bar["close"]
        ema50 = entry_bar["ema50"]
        ema200 = entry_bar["ema200"]
        if pd.isna(ema50) or pd.isna(ema200):
            continue
        trend_up = close > ema50
        trend_dn = close < ema50
        if direction > 0 and not trend_up:
            continue
        if direction < 0 and not trend_dn:
            continue

        entry_px = float(entry_bar["open"])
        sl = atr * SL_ATR_MULT
        tp1 = atr * TP1_RR
        tp2 = atr * TP2_RR

        sl_price = entry_px - sl * direction
        tp1_price = entry_px + tp1 * direction
        tp2_price = entry_px + tp2 * direction

        result = "TIMEOUT"
        exit_px = None
        hold_days = 0
        for _, bar in future_bars.iloc[1:].iterrows():
            hold_days += 1
            high = float(bar["high"])
            low = float(bar["low"])
            if direction > 0:
                if low <= sl_price:
                    result = "SL"; exit_px = sl_price; break
                if high >= tp2_price:
                    result = "TP2"; exit_px = tp2_price; break
                if high >= tp1_price:
                    result = "TP1"; exit_px = tp1_price; break
            else:
                if high >= sl_price:
                    result = "SL"; exit_px = sl_price; break
                if low <= tp2_price:
                    result = "TP2"; exit_px = tp2_price; break
                if low <= tp1_price:
                    result = "TP1"; exit_px = tp1_price; break
            if hold_days >= MAX_HOLD_DAYS:
                result = "TIMEOUT"; exit_px = float(bar["close"]); break

        if result == "TIMEOUT" and exit_px is None:
            exit_px = float(future_bars.iloc[-1]["close"])

        if exit_px is None:
            continue

        pnl_raw = (exit_px - entry_px) * direction

        trades.append({
            "date": cot_date, "pair": pair,
            "direction": "LONG" if direction > 0 else "SHORT",
            "cot_index": round(cot_val, 1),
            "entry": entry_px, "exit": exit_px,
            "result": result, "hold_days": hold_days,
            "pnl_raw": pnl_raw,
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
    print(f"Strategy 3: COT Extreme x Daily Trend BT Results")
    print(f"{'='*60}")
    print(f"COT extreme thresholds: >{EXTREME_HIGH} (short) / <{EXTREME_LOW} (long)")
    print(f"Period: {df_all['date'].min().date()} - {df_all['date'].max().date()}")
    print(f"Total trades: {len(df_all)}")
    print(f"Win rate:     {wr:.1f}%")
    print(f"PF:           {pf:.3f}")
    print(f"Max DD:       {dd:.5f} price units")

    print(f"\n--- By Pair ---")
    for pair in df_all["pair"].unique():
        sub = df_all[df_all["pair"] == pair]
        sw = sub[sub["pnl_raw"] > 0]
        gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
        sub_wr = len(sw) / len(sub) * 100 if len(sub) > 0 else 0
        avg_cot = sub["cot_index"].mean()
        print(f"  {pair}: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}, avg_COT={avg_cot:.0f}")

    print(f"\n--- By Direction ---")
    for d in df_all["direction"].unique():
        sub = df_all[df_all["direction"] == d]
        sw = sub[sub["pnl_raw"] > 0]
        gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
        print(f"  {d}: n={len(sub)}, PF={sub_pf:.3f}")

    print(f"\n--- By Result ---")
    for rt in df_all["result"].unique():
        sub = df_all[df_all["result"] == rt]
        print(f"  {rt}: n={len(sub)}, avg_pnl={sub['pnl_raw'].mean():.5f}")


def main():
    print("=== Strategy 3: COT Extreme BT ===")
    codes = [cfg["cftc_code"] for cfg in PAIR_CFG.values()]

    print(f"\n[1] Downloading CFTC TFF COT data (Socrata API)...")
    records = download_cot_socrata(codes, start_date="2018-01-01")
    if not records:
        print("No data returned from API.")
        return
    print(f"  Downloaded {len(records)} records")

    cot_df = build_cot_df(records)
    print(f"  Parsed: {len(cot_df)} valid records, codes: {cot_df['code'].unique()}")

    print(f"\n[2] Running backtests...")
    all_trades = []
    for pair, cfg in PAIR_CFG.items():
        price_df = load_price_d1(pair)
        if price_df is None:
            print(f"  No D1 data for {pair}, skip.")
            continue
        cot_idx = build_cot_index(cot_df, cfg["cftc_code"], COT_INDEX_LOOKBACK)
        n_extreme = ((cot_idx > EXTREME_HIGH) | (cot_idx < EXTREME_LOW)).sum()
        print(f"  {pair}: COT weeks={len(cot_idx)}, extreme_signals={n_extreme}")
        t = run_cot_bt(pair, cot_idx, price_df, sign=cfg["sign"])
        print(f"    -> Trades: {len(t)}")
        all_trades.append(t)

    df_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    print_results(df_all)

    if not df_all.empty:
        out = Path(__file__).parent / "cot_extreme_bt_results.csv"
        df_all.to_csv(out, index=False)
        print(f"\nSaved: {out}")

        print(f"\n--- Sensitivity: COT thresholds ---")
        for hi, lo in [(90, 10), (80, 20), (75, 25), (70, 30)]:
            sub = df_all[(df_all["cot_index"] > hi) | (df_all["cot_index"] < lo)]
            if len(sub) == 0:
                print(f"  >{hi}/<{lo}: no trades")
                continue
            sw = sub[sub["pnl_raw"] > 0]
            gl = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
            sub_pf = sw["pnl_raw"].sum() / gl if gl > 0 else float("inf")
            sub_wr = len(sw) / len(sub) * 100
            print(f"  >{hi}/<{lo}: n={len(sub)}, WR={sub_wr:.0f}%, PF={sub_pf:.3f}")


if __name__ == "__main__":
    main()
