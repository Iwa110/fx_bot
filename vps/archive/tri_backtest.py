# tri_backtest.py
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(r"C:\Users\Administrator\fx_bot\data")

PARAMS = [
    {"id": 0, "entry_th": 0.0008, "exit_th": 0.0001, "sl_th": 0.0012, "timeout": 24},
    {"id": 1, "entry_th": 0.0008, "exit_th": 0.0001, "sl_th": 0.0012, "timeout": 48},
    {"id": 2, "entry_th": 0.0008, "exit_th": 0.0001, "sl_th": 0.0012, "timeout": 96},
    {"id": 3, "entry_th": 0.0010, "exit_th": 0.0001, "sl_th": 0.0015, "timeout": 24},
    {"id": 4, "entry_th": 0.0010, "exit_th": 0.0001, "sl_th": 0.0015, "timeout": 48},
    {"id": 5, "entry_th": 0.0006, "exit_th": 0.0001, "sl_th": 0.0010, "timeout": 24},
]

def load_close(pair: str) -> pd.Series:
    path = DATA_DIR / f"{pair.replace('=X', '')}_1h.csv"
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index)
    return df["close"].dropna()  # 小文字に修正

def run_backtest(eurusd, gbpusd, eurgbp, entry_th, exit_th, sl_th):
    theory = eurusd / gbpusd
    spread = eurgbp - theory
    spread_vals = spread.values

    # デバッグ：最初のエントリーを追跡
    first_entry_printed = False
    
    trades = []
    in_pos = False
    entry_bar = None
    entry_spread = None
    direction = None

    n = len(spread_vals)
    for i in range(n):
        s = spread_vals[i]
        if np.isnan(s):
            continue

        if not in_pos:
            if s > entry_th:
                in_pos = True; direction = -1; entry_spread = s; entry_bar = i
                if not first_entry_printed:
                    print(f"  SELL entry: bar={i}, spread={s:.6f}, entry_th={entry_th}")
                    first_entry_printed = True
            elif s < -entry_th:
                in_pos = True; direction = 1; entry_spread = s; entry_bar = i
                if not first_entry_printed:
                    print(f"  BUY  entry: bar={i}, spread={s:.6f}, entry_th={-entry_th}")
                    first_entry_printed = True
        else:
            pnl_raw = abs(entry_spread) - abs(s)
            exit_flag = False
            # 利確：spreadが収束
            if abs(s) <= exit_th:
                exit_flag = True
                result = pnl_raw
            # 損切り：保有時間超過（例：48bars=2日）
            elif (i - entry_bar) >= 48:
                exit_flag = True
                result = pnl_raw  # 収束していなければマイナスもあり得る
            if exit_flag:
                trades.append({
                    "pnl": result,
                    "bars": i - entry_bar,
                    "win": result > 0,
                })
                in_pos = False

    if not trades:
        return {"trades": 0, "winrate": 0, "PF": 0, "avg_bars": 0, "max_dd": 0}

    df_t = pd.DataFrame(trades)
    wins = df_t[df_t["win"]]["pnl"].sum()
    losses = abs(df_t[~df_t["win"]]["pnl"].sum())
    pf = wins / losses if losses > 0 else np.inf

    # Max DD（累積PnLベース）
    cum = df_t["pnl"].cumsum()
    rolling_max = cum.cummax()
    dd = (cum - rolling_max)
    max_dd = dd.min()

    return {
        "trades": len(df_t),
        "winrate": round(df_t["win"].mean() * 100, 1),
        "PF": round(pf, 2),
        "avg_bars": round(df_t["bars"].mean(), 1),
        "max_dd": round(max_dd, 5),
    }

def main():
    print("Loading data...")
    eurusd = load_close("EURUSD=X")
    gbpusd = load_close("GBPUSD=X")
    eurgbp = load_close("EURGBP=X")

    common = eurusd.index.intersection(gbpusd.index).intersection(eurgbp.index)
    eurusd = eurusd.loc[common]
    gbpusd = gbpusd.loc[common]
    eurgbp = eurgbp.loc[common]
    print(f"Aligned bars: {len(common)}  ({common[0]} ~ {common[-1]})\n")

    # デバッグ：spread確認
    theory = eurusd / gbpusd
    spread = eurgbp - theory
    print(f"Spread stats:")
    print(f"  mean : {spread.mean():.6f}")
    print(f"  std  : {spread.std():.6f}")
    print(f"  min  : {spread.min():.6f}")
    print(f"  max  : {spread.max():.6f}")
    print(f"  |spread|>0.0025 count: {(spread.abs() > 0.0025).sum()}")
    print(f"  |spread|>0.0030 count: {(spread.abs() > 0.0030).sum()}")

# ↓ここから追加（spreadデバッグprintの後に）
    rows = []
    for p in PARAMS:
        res = run_backtest(eurusd, gbpusd, eurgbp,
                           p["entry_th"], p["exit_th"], p["sl_th"])
        rows.append({
            "#": p["id"],
            "entry_th": p["entry_th"],
            "exit_th":  p["exit_th"],
            "sl_th":    p["sl_th"],
            **res,
        })

    df = pd.DataFrame(rows).set_index("#")
    pd.set_option("display.float_format", "{:.5f}".format)
    print("\n=== TRI Backtest Results ===")
    print(df.to_string())

if __name__ == "__main__":
    main()