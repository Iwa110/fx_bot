def load_close(pair: str) -> pd.Series:
    # まず候補ファイル名を探す
    candidates = [
        DATA_DIR / f"{pair}_1h.csv",
        DATA_DIR / f"{pair.replace('=X', '')}_1h.csv",  # EURUSD_1h.csv
        DATA_DIR / f"{pair.replace('=X', '')}=X_1h.csv",
    ]
    
    path = None
    for c in candidates:
        if c.exists():
            path = c
            break
    
    if path is None:
        # dataフォルダの中身を表示して終了
        print(f"\n[ERROR] {pair} のCSVが見つかりません")
        print(f"DATA_DIR内のファイル一覧:")
        for f in sorted(DATA_DIR.glob("*.csv")):
            print(f"  {f.name}")
        raise FileNotFoundError(f"{pair} not found in {DATA_DIR}")
    
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
    return df["Close"].dropna()