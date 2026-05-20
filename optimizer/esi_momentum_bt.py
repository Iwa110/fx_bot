"""
Strategy B: Economic Surprise Index (ESI) x Momentum BT
Pairs: EURUSD / GBPUSD / USDJPY
Data: Forex Factory JSON (live) + price-momentum ESI proxy (historical BT)

DIY-ESI:
  Live:      FF calendar (https://nfs.faireconomy.media/ff_calendar_thisweek.json)
             Accumulate (Actual - Forecast) per currency over 30 days.
             NOTE: FF API returns current week only; historical BT uses proxy below.
  BT proxy:  720-bar (30-day) close momentum for each pair, z-scored to ±100 range.
             Positive = base-currency performing above trend (bullish base).
             Threshold ±50 maps to roughly 1.5 SD momentum.

Entry conditions (H1):
  1. ESI proxy sustained above +50 for >= 3 days (72 bars) -> long bias
     ESI proxy sustained below -50 for >= 3 days (72 bars) -> short bias
  2. H1 EMA20 crosses EMA50 in bias direction (cross within lookback window)
  3. Cooldown: 48 H1 bars (2 days) per pair

Exit: TP=3.0xATR(14), SL=1.5xATR(14)
      Stage2 trail: activate=0.70, distance=0.30

Output: optimizer/esi_momentum_bt_results.csv

Usage: python esi_momentum_bt.py
"""

import warnings
import numpy as np
import pandas as pd
import requests
from pathlib import Path

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent.parent / "data"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

PAIR_META = {
    "EURUSD": {"pip_unit": 0.0001, "spread": 0.0001},   # 1 pip spread
    "GBPUSD": {"pip_unit": 0.0001, "spread": 0.00012},
    "USDJPY": {"pip_unit": 0.01,   "spread": 0.02},
}

# ===== Strategy params =====
ESI_WINDOW_BARS    = 720    # ~30 days in H1
ESI_THRESHOLD      = 50.0   # ±50 on z-score-scaled proxy
ESI_CONFIRM_BARS   = 72     # 3 days sustained above threshold before entry
CROSS_LOOKBACK     = 48     # H1 bars: EMA cross must occur within this window
EMA_FAST           = 20
EMA_SLOW           = 50
ATR_PERIOD         = 14
SL_ATR_MULT        = 1.5
TP_ATR_MULT        = 3.0
STAGE2_ACTIVATE    = 0.70
STAGE2_DISTANCE    = 0.30
MAX_HOLD_BARS      = 300    # H1 bars (~12.5 days)
COOLDOWN_BARS      = 48     # H1 bars (2 days)

FF_API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


# ===== Live FF data helper (for reference / forward-use in live bot) =====

def fetch_ff_calendar():
    """
    Fetch current week FF calendar.
    Returns dict: { 'USD': cumulative_surprise, 'EUR': ..., 'GBP': ..., 'JPY': ... }
    NOTE: API only returns current week; used for live strategy, not historical BT.
    """
    try:
        resp = requests.get(FF_API_URL, timeout=15)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        print(f"  [FF] calendar fetch failed: {e}")
        return None

    surprises = {}
    for ev in events:
        ccy     = ev.get("country", "").upper()
        actual  = ev.get("actual", "")
        forecast = ev.get("forecast", "")
        if not actual or not forecast or actual == "" or forecast == "":
            continue
        try:
            a = float(str(actual).replace("K", "000").replace("M", "000000")
                                 .replace("%", "").replace("B", "000000000"))
            f = float(str(forecast).replace("K", "000").replace("M", "000000")
                                   .replace("%", "").replace("B", "000000000"))
            surprises[ccy] = surprises.get(ccy, 0.0) + (a - f)
        except (ValueError, TypeError):
            continue

    return surprises if surprises else None


def esi_from_ff_live(pair):
    """
    Compute pair ESI from live FF data.
    EURUSD: EUR_surprise - USD_surprise
    GBPUSD: GBP_surprise - USD_surprise
    USDJPY: USD_surprise - JPY_surprise
    Returns float or None.
    """
    sur = fetch_ff_calendar()
    if sur is None:
        return None
    base_map = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY")}
    base_ccy, quote_ccy = base_map.get(pair, (None, None))
    if base_ccy is None:
        return None
    return sur.get(base_ccy, 0.0) - sur.get(quote_ccy, 0.0)


# ===== Historical ESI proxy =====

def build_esi_proxy(close, window=ESI_WINDOW_BARS):
    """
    30-day momentum proxy for ESI.
    momentum = (close - close.shift(window)) / close.shift(window) * 100
    z-scored over trailing 3x window and scaled to ±100 range.
    """
    mom = (close - close.shift(window)) / close.shift(window) * 100
    roll_mean = mom.rolling(window * 3, min_periods=window).mean()
    roll_std  = mom.rolling(window * 3, min_periods=window).std()
    zscore    = (mom - roll_mean) / roll_std.replace(0, np.nan)
    # Clip and scale so ±1 SD maps to ±67 (threshold 50 ≈ 0.75 SD)
    proxy = (zscore * 67).clip(-100, 100)
    return proxy


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
    df["esi"]      = build_esi_proxy(close)
    df["atr"]      = calc_atr(df)
    # EMA cross signal: +1 = fast just crossed above slow, -1 = fast just crossed below slow
    df["ema_above"] = (df["ema_fast"] > df["ema_slow"]).astype(int)
    df["cross_up"]  = (df["ema_above"].diff() == 1).astype(int)  # golden cross
    df["cross_dn"]  = (df["ema_above"].diff() == -1).astype(int) # death cross
    return df


# ===== BT core =====

def run_bt_pair(pair, df):
    meta    = PAIR_META[pair]
    spread  = meta["spread"]
    df = build_indicators(df)
    df = df.reset_index(drop=True)

    min_start = max(ESI_WINDOW_BARS * 3, EMA_SLOW + ESI_CONFIRM_BARS + CROSS_LOOKBACK)

    trades    = []
    last_bar  = -COOLDOWN_BARS - 1
    n         = len(df)

    for i in range(min_start, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        atr = df["atr"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        # --- ESI proxy bias: sustained above threshold for >= 3 days ---
        esi_window = df["esi"].iloc[i - ESI_CONFIRM_BARS: i + 1]
        if esi_window.isna().any():
            continue

        all_long  = (esi_window >= ESI_THRESHOLD).all()
        all_short = (esi_window <= -ESI_THRESHOLD).all()
        if not all_long and not all_short:
            continue
        bias = "long" if all_long else "short"

        # --- EMA cross confirmation within lookback window ---
        cross_window_start = max(0, i - CROSS_LOOKBACK + 1)
        if bias == "long":
            recent_cross = df["cross_up"].iloc[cross_window_start: i + 1].sum() > 0
        else:
            recent_cross = df["cross_dn"].iloc[cross_window_start: i + 1].sum() > 0
        if not recent_cross:
            continue

        # --- Current EMA alignment ---
        ema_fast_cur = df["ema_fast"].iloc[i]
        ema_slow_cur = df["ema_slow"].iloc[i]
        if bias == "long"  and not (ema_fast_cur > ema_slow_cur):
            continue
        if bias == "short" and not (ema_fast_cur < ema_slow_cur):
            continue

        # --- Build trade ---
        sl_dist   = atr * SL_ATR_MULT
        tp_dist   = atr * TP_ATR_MULT
        direction = 1 if bias == "long" else -1
        close_cur = df["close"].iloc[i]
        entry_px  = close_cur + spread * direction

        sl_price = entry_px - sl_dist * direction
        tp_price = entry_px + tp_dist * direction

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
        esi_val = float(df["esi"].iloc[i])
        trades.append({
            "pair":      pair,
            "datetime":  df["datetime"].iloc[i] if "datetime" in df.columns else i,
            "bias":      bias,
            "esi":       round(esi_val, 1),
            "entry":     entry_px,
            "exit":      exit_px,
            "result":    result,
            "pnl_raw":   pnl_raw,
            "atr":       atr,
        })
        last_bar = i

    return pd.DataFrame(trades)


def load_price_1h(pair):
    path = DATA_DIR / f"{pair}_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    df = df.rename(columns={date_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    for col in ["open", "high", "low", "close"]:
        uc = col.capitalize()
        if col not in df.columns and uc in df.columns:
            df[col] = df[uc]
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
    return df


# ===== Results printing =====

def print_results(df_all):
    if df_all.empty:
        print("No trades generated.")
        return

    wins   = df_all[df_all["pnl_raw"] > 0]
    losses = df_all[df_all["pnl_raw"] <= 0]
    gw     = wins["pnl_raw"].sum()
    gl     = abs(losses["pnl_raw"].sum())
    pf     = gw / gl if gl > 0 else float("inf")
    wr     = len(wins) / len(df_all) * 100
    cum    = df_all["pnl_raw"].cumsum()
    max_dd = (cum - cum.cummax()).min()

    dt_col = "datetime" if "datetime" in df_all.columns else None

    print(f"\n{'='*60}")
    print(f"Strategy B: ESI Momentum BT")
    print(f"  ESI proxy: 30-day price momentum z-score (scaled ±100)")
    print(f"  Threshold: ±{ESI_THRESHOLD} sustained {ESI_CONFIRM_BARS//24}d, "
          f"EMA{EMA_FAST}/EMA{EMA_SLOW} cross within {CROSS_LOOKBACK}bars")
    print(f"{'='*60}")
    if dt_col:
        print(f"Period:       {df_all[dt_col].min().date()} - {df_all[dt_col].max().date()}")
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

    print(f"\n--- Sensitivity: ESI threshold ---")
    for th in [30.0, 50.0, 70.0]:
        sub = df_all[df_all["esi"].abs() >= th]
        if len(sub) == 0:
            print(f"  ESI_th={th}: no trades")
            continue
        sw  = sub[sub["pnl_raw"] > 0]
        gl2 = abs(sub[sub["pnl_raw"] <= 0]["pnl_raw"].sum())
        spf = sw["pnl_raw"].sum() / gl2 if gl2 > 0 else float("inf")
        swr = len(sw) / len(sub) * 100
        print(f"  ESI_th={th}: n={len(sub)}, WR={swr:.0f}%, PF={spf:.3f}")


# ===== Main =====

def main():
    print("=== Strategy B: ESI x Momentum BT ===")
    print(f"Pairs:   {PAIRS}")
    print(f"Params:  ESI_window={ESI_WINDOW_BARS}bars (~30d), threshold=±{ESI_THRESHOLD}")
    print(f"         confirm={ESI_CONFIRM_BARS}bars (~3d), cross_lookback={CROSS_LOOKBACK}bars")
    print(f"         SL={SL_ATR_MULT}xATR, TP={TP_ATR_MULT}xATR")
    print(f"         Stage2: activate={STAGE2_ACTIVATE}, dist={STAGE2_DISTANCE}")
    print(f"NOTE: ESI proxy is price-momentum based (FF calendar = current week only).")
    print(f"      For live bot, replace proxy with fetch_ff_calendar() + esi_from_ff_live().")

    # Check live FF data availability (informational)
    print("\n[Checking FF calendar API...]")
    ff_data = fetch_ff_calendar()
    if ff_data:
        print(f"  FF API: OK. Current week surprises: {ff_data}")
    else:
        print("  FF API: unavailable or no data (expected for historical BT).")

    # Run BT
    print("\n[Running backtests...]")
    all_trades = []
    for pair in PAIRS:
        df = load_price_1h(pair)
        if df is None:
            print(f"  {pair}: no 1h data at {DATA_DIR}/{pair}_1h.csv, skip")
            continue
        print(f"  {pair}: 1h bars={len(df)}, "
              f"{df['datetime'].iloc[0].date()} - {df['datetime'].iloc[-1].date()}")
        t = run_bt_pair(pair, df)
        print(f"    -> {len(t)} trades")
        all_trades.append(t)

    df_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    print_results(df_all)

    if not df_all.empty:
        out = Path(__file__).parent / "esi_momentum_bt_results.csv"
        df_all.to_csv(out, index=False)
        print(f"\nSaved: {out}")

        # Parameter grid search: vary threshold and confirm window
        print(f"\n{'='*60}")
        print("Grid search: ESI_threshold x confirm_days")
        print(f"{'='*60}")
        print(f"  {'th':>4} | {'confirm':>7} | {'n':>5} | {'WR':>5} | {'PF':>6}")
        print(f"  {'-'*35}")
        for th in [30.0, 40.0, 50.0, 60.0, 70.0]:
            for conf_days in [2, 3, 5]:
                conf_bars = conf_days * 24
                sub_trades = []
                for pair in PAIRS:
                    df = load_price_1h(pair)
                    if df is None:
                        continue
                    t = _run_bt_pair_params(pair, df, esi_threshold=th, confirm_bars=conf_bars)
                    sub_trades.append(t)
                sub_df = pd.concat(sub_trades, ignore_index=True) if sub_trades else pd.DataFrame()
                if sub_df.empty:
                    print(f"  {th:>4.0f} | {conf_days:>5}d  |     0 |     - |      -")
                    continue
                sw  = sub_df[sub_df["pnl_raw"] > 0]
                gl2 = abs(sub_df[sub_df["pnl_raw"] <= 0]["pnl_raw"].sum())
                spf = sw["pnl_raw"].sum() / gl2 if gl2 > 0 else float("inf")
                swr = len(sw) / len(sub_df) * 100 if len(sub_df) > 0 else 0
                print(f"  {th:>4.0f} | {conf_days:>5}d  | {len(sub_df):>5} | {swr:>4.0f}% | {spf:>6.3f}")


def _run_bt_pair_params(pair, df, esi_threshold=50.0, confirm_bars=72):
    """Helper for grid search with overridden ESI params."""
    meta    = PAIR_META[pair]
    spread  = meta["spread"]
    df = build_indicators(df)
    df = df.reset_index(drop=True)

    min_start = max(ESI_WINDOW_BARS * 3, EMA_SLOW + confirm_bars + CROSS_LOOKBACK)
    trades    = []
    last_bar  = -COOLDOWN_BARS - 1
    n         = len(df)

    for i in range(min_start, n):
        if i - last_bar < COOLDOWN_BARS:
            continue
        atr = df["atr"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        esi_window = df["esi"].iloc[i - confirm_bars: i + 1]
        if esi_window.isna().any():
            continue
        all_long  = (esi_window >= esi_threshold).all()
        all_short = (esi_window <= -esi_threshold).all()
        if not all_long and not all_short:
            continue
        bias = "long" if all_long else "short"

        cws = max(0, i - CROSS_LOOKBACK + 1)
        if bias == "long":
            recent_cross = df["cross_up"].iloc[cws: i + 1].sum() > 0
        else:
            recent_cross = df["cross_dn"].iloc[cws: i + 1].sum() > 0
        if not recent_cross:
            continue

        ema_fast_cur = df["ema_fast"].iloc[i]
        ema_slow_cur = df["ema_slow"].iloc[i]
        if bias == "long"  and not (ema_fast_cur > ema_slow_cur):
            continue
        if bias == "short" and not (ema_fast_cur < ema_slow_cur):
            continue

        sl_dist   = atr * SL_ATR_MULT
        tp_dist   = atr * TP_ATR_MULT
        direction = 1 if bias == "long" else -1
        close_cur = df["close"].iloc[i]
        entry_px  = close_cur + spread * direction
        sl_price  = entry_px - sl_dist * direction
        tp_price  = entry_px + tp_dist * direction

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
                    result = "TRAIL_SL" if activated else "SL"; exit_px = trail_sl; break
                if h >= tp_price:
                    result = "TP"; exit_px = tp_price; break
            else:
                progress = (entry_px - mid) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid + tp_dist * STAGE2_DISTANCE
                    if new_trail < trail_sl:
                        trail_sl = new_trail
                if h >= trail_sl:
                    result = "TRAIL_SL" if activated else "SL"; exit_px = trail_sl; break
                if l <= tp_price:
                    result = "TP"; exit_px = tp_price; break

        if result == "TIMEOUT":
            exit_px = df["close"].iloc[min(i + MAX_HOLD_BARS, n - 1)]
        if exit_px is None:
            continue

        pnl_raw = (exit_px - entry_px) * direction
        trades.append({
            "pair":    pair,
            "bias":    bias,
            "esi":     round(float(df["esi"].iloc[i]), 1),
            "result":  result,
            "pnl_raw": pnl_raw,
        })
        last_bar = i

    return pd.DataFrame(trades)


if __name__ == "__main__":
    main()
