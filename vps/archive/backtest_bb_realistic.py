"""
backtest_bb_realistic.py
BB逆張り戦略 リアルバックテスト（VPS Python実行用）
======================================================
- 5分足BB(period=20, σ=1.5) 逆張り
- HTFフィルター: 1h足BB σ±1.0超えでスキップ
- RSIフィルター: sell>=55 / buy<=45（period=14）
- クールダウン: SL後15分間は同ペア再エントリーブロック
- TrailSL: 3段階（30秒ごとチェックを5分足内で分割シミュレート）
- コスト: スプレッド + 手数料（TitanFX Zeroブレード）
- Walk-forward: 60%訓練 / 40%検証
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# ══════════════════════════════════════════
# 定数
# ══════════════════════════════════════════
INITIAL  = 1_000_000
USDJPY   = 150.0
LOT      = 10_000       # 通貨単位（0.1lot相当）

BB_PERIOD = 20
BB_SIGMA  = 1.5

HTF_PERIOD     = 20
HTF_SIGMA      = 1.5
HTF_RANGE_LIMIT = 1.0   # 1h足σ位置がこれ以内ならレンジ

RSI_PERIOD  = 14
RSI_SELL_MIN = 55
RSI_BUY_MAX  = 45

COOLDOWN_MINUTES = 15

# TrailSL設定（BB戦略のみ）
STAGE1_ACTIVATE  = 0.3   # ATR×0.3でBEライン
STAGE2_ACTIVATE  = 0.7   # ATR×0.7で小利益確定
STAGE2_LOCK      = 0.3   # Stage2 SL: entry + ATR×0.3
STAGE3_ACTIVATE  = 1.0   # ATR×1.0でトレーリング開始
STAGE3_DISTANCE  = 1.0   # SL = current - ATR×1.0
MIN_UPDATE_MULT  = 0.05  # 最小更新幅: ATR×0.05
TRAIL_INTERVAL_SEC = 30  # 30秒ごとにチェック
BAR_SECONDS_5M   = 300   # 5分足 = 300秒
TRAIL_CHECKS_PER_BAR = BAR_SECONDS_5M // TRAIL_INTERVAL_SEC  # = 10回/バー

# コスト設定
SPREAD_JPY   = 1.5    # JPYクロス スプレッド（pips）
SPREAD_OTHER = 0.8    # 非JPYペア スプレッド（pips）
COMMISSION   = 0.14   # 手数料（片道pips、TitanFX Zeroブレード）

# TP/SL（ATR倍率）
TP_MULT = 3.0
SL_MULT = 2.0

# 対象ペア
PAIRS = {
    'USDJPY=X': {'name': 'USDJPY', 'is_jpy': True},
    'EURJPY=X': {'name': 'EURJPY', 'is_jpy': True},
    'GBPJPY=X': {'name': 'GBPJPY', 'is_jpy': True},
    'AUDJPY=X': {'name': 'AUDJPY', 'is_jpy': True},
    'EURUSD=X': {'name': 'EURUSD', 'is_jpy': False},
    'GBPUSD=X': {'name': 'GBPUSD', 'is_jpy': False},
    'USDCAD=X': {'name': 'USDCAD', 'is_jpy': False},
}

# ══════════════════════════════════════════
# データ取得
# ══════════════════════════════════════════
def get_data() -> dict:
    print("データ取得中（5分足・60日 / 1時間足・60日）...")
    data = {}
    for ticker, cfg in PAIRS.items():
        name = cfg['name']
        try:
            # 5分足
            df5 = yf.download(ticker, period='60d', interval='5m', progress=False)
            if df5.empty:
                print(f"  {name}: 5m データなし → スキップ")
                continue
            if hasattr(df5.columns, 'levels'):
                df5.columns = df5.columns.droplevel(1)
            df5.columns = [c.lower() for c in df5.columns]
            df5 = df5.dropna(subset=['close'])
            df5.index = pd.to_datetime(df5.index)

            # 1時間足（HTFフィルター用）
            df1h = yf.download(ticker, period='60d', interval='1h', progress=False)
            if df1h.empty:
                print(f"  {name}: 1h データなし → スキップ")
                continue
            if hasattr(df1h.columns, 'levels'):
                df1h.columns = df1h.columns.droplevel(1)
            df1h.columns = [c.lower() for c in df1h.columns]
            df1h = df1h.dropna(subset=['close'])
            df1h.index = pd.to_datetime(df1h.index)

            data[name] = {'5m': df5, '1h': df1h, 'is_jpy': cfg['is_jpy']}
            print(f"  {name}: 5m={len(df5)}本 / 1h={len(df1h)}本")
        except Exception as e:
            print(f"  {name}: エラー {e}")
    print("取得完了\n")
    return data

# ══════════════════════════════════════════
# インジケーター計算
# ══════════════════════════════════════════
def calc_bb(close: pd.Series, period: int, sigma: float) -> pd.DataFrame:
    """BB上限・下限・MA・σ位置を計算"""
    ma    = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = ma + sigma * std
    lower = ma - sigma * std
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    return pd.DataFrame({'ma': ma, 'upper': upper, 'lower': lower,
                         'sigma_pos': sigma_pos})

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

# ══════════════════════════════════════════
# HTFシグマ位置を5分足にマップ
# ══════════════════════════════════════════
def build_htf_sigma(df5: pd.DataFrame, df1h: pd.DataFrame) -> pd.Series:
    """
    1時間足のσ位置を計算し、5分足インデックスに前方埋め（ffill）でマップ。
    先読み防止: 1時間足は1本シフト（確定バーを使用）
    """
    bb1h = calc_bb(df1h['close'], HTF_PERIOD, HTF_SIGMA)
    sigma_shifted = bb1h['sigma_pos'].shift(1)  # 先読み防止

    # タイムゾーン統一
    if df5.index.tz is None and sigma_shifted.index.tz is not None:
        df5_idx = df5.index.tz_localize('UTC')
    elif df5.index.tz is not None and sigma_shifted.index.tz is None:
        df5_idx = df5.index.tz_localize(None)
    else:
        df5_idx = df5.index

    # reindexして前方埋め
    combined = sigma_shifted.reindex(
        sigma_shifted.index.union(df5_idx)
    ).ffill()
    result = combined.reindex(df5_idx)
    result.index = df5.index
    return result

# ══════════════════════════════════════════
# コスト計算
# ══════════════════════════════════════════
def calc_cost(is_jpy: bool) -> float:
    """片道コスト（pips）= スプレッド/2 + 手数料"""
    spread = SPREAD_JPY if is_jpy else SPREAD_OTHER
    return spread / 2 + COMMISSION

# ══════════════════════════════════════════
# TrailSL計算（1チェック分）
# ══════════════════════════════════════════
def update_sl(current_sl: float, entry: float, direction: int,
              current_price: float, atr: float) -> tuple:
    """
    TrailSL更新計算。
    戻り値: (new_sl, stage_str) or (current_sl, '')
    """
    profit_dist = (current_price - entry) * direction
    min_upd     = atr * MIN_UPDATE_MULT
    new_sl      = current_sl
    stage       = ''

    # Stage1: BEライン
    if profit_dist >= atr * STAGE1_ACTIVATE:
        candidate   = entry
        improvement = (candidate - current_sl) * direction
        if improvement > min_upd:
            new_sl = candidate
            stage  = 'S1'

    # Stage2: 小利益確定
    if profit_dist >= atr * STAGE2_ACTIVATE:
        candidate   = entry + atr * STAGE2_LOCK * direction
        improvement = (candidate - current_sl) * direction
        if improvement > min_upd:
            if new_sl == current_sl or (candidate - new_sl) * direction > 0:
                new_sl = candidate
                stage  = 'S2'

    # Stage3: トレーリング
    if profit_dist >= atr * STAGE3_ACTIVATE:
        candidate   = current_price - atr * STAGE3_DISTANCE * direction
        improvement = (candidate - current_sl) * direction
        if improvement > min_upd:
            if new_sl == current_sl or (candidate - new_sl) * direction > 0:
                new_sl = candidate
                stage  = 'S3'

    return new_sl, stage

# ══════════════════════════════════════════
# バックテスト本体
# ══════════════════════════════════════════
def backtest(df5: pd.DataFrame, htf_sigma: pd.Series,
             is_jpy: bool, include_cost: bool = True) -> dict:
    """
    1バーずつ処理。各バー内でTrailSLを10回チェック。
    """
    pip      = 0.01 if is_jpy else 0.0001
    cost_pip = calc_cost(is_jpy) if include_cost else 0.0
    cost_dist = cost_pip * pip

    # インジケーター事前計算
    bb  = calc_bb(df5['close'], BB_PERIOD, BB_SIGMA)
    rsi = calc_rsi(df5['close'], RSI_PERIOD)
    atr = calc_atr(df5)

    # 先読み防止: 1本シフト
    bb_shifted   = bb.shift(1)
    rsi_shifted  = rsi.shift(1)
    atr_shifted  = atr.shift(1)
    htf_shifted  = htf_sigma  # build_htf_sigmaで既にshift済み

    balance  = INITIAL
    trades   = []          # (pnl, exit_type)
    position = None
    cooldown = {}          # symbol → cooldown終了時刻（ここでは index番号）

    n = len(df5)
    for i in range(BB_PERIOD + RSI_PERIOD + 5, n):
        row       = df5.iloc[i]
        bar_time  = df5.index[i]
        high      = row['high']
        low       = row['low']
        close     = row['close']

        htf_sig   = htf_shifted.iloc[i]
        sig_pos   = bb_shifted['sigma_pos'].iloc[i]
        upper     = bb_shifted['upper'].iloc[i]
        lower     = bb_shifted['lower'].iloc[i]
        rsi_val   = rsi_shifted.iloc[i]
        atr_val   = atr_shifted.iloc[i]

        if pd.isna(sig_pos) or pd.isna(rsi_val) or pd.isna(atr_val) or atr_val <= 0:
            continue

        # ── ポジション管理（バー内TrailSLチェック）──────────────
        if position is not None:
            entry     = position['entry']
            direction = position['direction']
            tp        = position['tp']
            sl        = position['sl']
            exit_type = None
            pnl       = None

            # バーの中を10分割（30秒間隔を模擬）
            # 簡易: 高値・安値を線形補間してTrailSLを順次更新
            for check in range(TRAIL_CHECKS_PER_BAR):
                frac = (check + 1) / TRAIL_CHECKS_PER_BAR
                # 価格経路: buy想定は low→high, sell想定は high→low を近似
                if direction == 1:
                    simulated_price = low + (high - low) * frac
                else:
                    simulated_price = high - (high - low) * frac

                # TrailSL更新
                new_sl, _ = update_sl(sl, entry, direction, simulated_price, atr_val)
                sl = new_sl

                # TP/SL判定
                if direction == 1:
                    if simulated_price >= tp:
                        exit_type = 'TP'
                        break
                    if simulated_price <= sl:
                        exit_type = 'TrailSL' if sl > entry - atr_val * SL_MULT else 'SL'
                        break
                else:
                    if simulated_price <= tp:
                        exit_type = 'TP'
                        break
                    if simulated_price >= sl:
                        exit_type = 'TrailSL' if sl < entry + atr_val * SL_MULT else 'SL'
                        break

            if exit_type:
                if exit_type == 'TP':
                    gross_dist = position['tp_dist']
                else:
                    # TrailSL or SL: SL位置から損益計算
                    gross_dist = (sl - entry) * direction
                    if gross_dist < 0:
                        gross_dist = -abs(gross_dist)  # 損失
                    else:
                        gross_dist = abs(gross_dist)   # 利益

                # コスト控除
                if exit_type == 'TP':
                    net_dist = gross_dist - cost_dist * 2  # 往復
                elif gross_dist >= 0:
                    net_dist = gross_dist - cost_dist * 2
                else:
                    net_dist = gross_dist - cost_dist * 2

                if is_jpy:
                    pnl = net_dist / pip * LOT / USDJPY * USDJPY  # 円換算
                    pnl = net_dist / pip * LOT  # JPY口座はそのまま円
                else:
                    pnl = net_dist / pip * LOT * 0.01 * USDJPY   # 概算円換算

                balance += pnl
                trades.append({'pnl': pnl, 'exit': exit_type})

                if exit_type == 'SL':
                    cooldown[position['symbol']] = i + int(COOLDOWN_MINUTES * 60 / 300)

                position = None

        # ── エントリーシグナル ────────────────────────────────
        if position is not None:
            continue

        # HTFフィルター
        if not pd.isna(htf_sig) and abs(htf_sig) > HTF_RANGE_LIMIT:
            continue

        # BBタッチ判定
        direction = None
        if close >= upper:
            direction = -1   # SELL
        elif close <= lower:
            direction = 1    # BUY

        if direction is None:
            continue

        # RSIフィルター
        if direction == -1 and rsi_val < RSI_SELL_MIN:
            continue
        if direction ==  1 and rsi_val > RSI_BUY_MAX:
            continue

        # クールダウン判定
        sym = df5.attrs.get('symbol', 'UNK')
        if cooldown.get(sym, 0) > i:
            continue

        # TP/SL設定
        tp_dist = atr_val * TP_MULT
        sl_dist = atr_val * SL_MULT
        if direction == 1:
            entry_price = close + cost_dist   # ask（スプレッド考慮）
            tp = entry_price + tp_dist
            sl = entry_price - sl_dist
        else:
            entry_price = close - cost_dist   # bid
            tp = entry_price - tp_dist
            sl = entry_price + sl_dist

        position = {
            'direction': direction,
            'entry':     entry_price,
            'tp':        tp,
            'sl':        sl,
            'tp_dist':   tp_dist,
            'sl_dist':   sl_dist,
            'symbol':    sym,
            'bar_idx':   i,
        }

    # ── 統計計算 ──────────────────────────────────────────────
    if len(trades) < 5:
        return {'sharpe': -99, 'ret': 0, 'dd': 0, 'n': len(trades),
                'win_rate': 0, 'tp_rate': 0, 'trail_rate': 0, 'sl_rate': 0,
                'final': INITIAL}

    pnls     = [t['pnl'] for t in trades]
    exits    = [t['exit'] for t in trades]
    final    = INITIAL + sum(pnls)
    ret_pct  = (final - INITIAL) / INITIAL * 100

    n        = len(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / n * 100
    tp_rate  = exits.count('TP')    / n * 100
    trail_rt = exits.count('TrailSL') / n * 100
    sl_rate  = exits.count('SL')    / n * 100

    # 年率換算: 60日データから
    days_held = 60
    ret_annual = ret_pct / days_held * 365

    # 最大DD
    peak = INITIAL
    bal  = INITIAL
    dds  = []
    for p in pnls:
        bal += p
        peak = max(peak, bal)
        dds.append((peak - bal) / peak * 100 if peak > 0 else 0)
    max_dd = max(dds)

    sharpe = 0
    if len(pnls) > 1 and np.std(pnls) > 0:
        # 年率換算: 5分足1年≒105120本
        bars_per_year = 105120
        bars_used     = n
        scale         = np.sqrt(bars_per_year / max(bars_used, 1))
        sharpe = round((np.mean(pnls) / np.std(pnls)) * scale, 2)

    return {
        'sharpe':     sharpe,
        'ret_total':  round(ret_pct, 2),
        'ret_annual': round(ret_annual, 2),
        'dd':         round(max_dd, 2),
        'n':          n,
        'win_rate':   round(win_rate, 1),
        'tp_rate':    round(tp_rate, 1),
        'trail_rate': round(trail_rt, 1),
        'sl_rate':    round(sl_rate, 1),
        'final':      round(final),
    }

# ══════════════════════════════════════════
# Walk-forward実行
# ══════════════════════════════════════════
def walk_forward(df5: pd.DataFrame, df1h: pd.DataFrame,
                 is_jpy: bool, label: str) -> dict:
    print(f"{label} バックテスト中...")

    # HTFシグマ位置を事前計算
    try:
        htf_sigma = build_htf_sigma(df5, df1h)
    except Exception as e:
        print(f"  {label}: HTFシグマ計算エラー {e}")
        htf_sigma = pd.Series(np.nan, index=df5.index)

    # symbolをattrsに保存（クールダウン用）
    df5.attrs['symbol'] = label

    split    = int(len(df5) * 0.6)
    train5   = df5.iloc[:split].copy()
    test5    = df5.iloc[split:].copy()
    train5.attrs['symbol'] = label
    test5.attrs['symbol']  = label
    htf_tr   = htf_sigma.iloc[:split]
    htf_te   = htf_sigma.iloc[split:]

    # コスト込み・なし両方
    train_raw  = backtest(train5, htf_tr, is_jpy, include_cost=False)
    train_cost = backtest(train5, htf_tr, is_jpy, include_cost=True)
    test_raw   = backtest(test5,  htf_te, is_jpy, include_cost=False)
    test_cost  = backtest(test5,  htf_te, is_jpy, include_cost=True)

    return {
        'train_no_cost': train_raw,
        'train_with_cost': train_cost,
        'test_no_cost':  test_raw,
        'test_with_cost': test_cost,
    }

# ══════════════════════════════════════════
# 結果表示
# ══════════════════════════════════════════
def print_results(all_results: dict):
    print("\n" + "=" * 65)
    print("【BB逆張り戦略 リアルバックテスト結果】")
    print("  フィルター: HTF(1hBB±1σ) / RSI(sell>=55/buy<=45) / CD15分")
    print("  TrailSL: 3段階(Stage1/2/3) / 30秒チェック模擬")
    print("=" * 65)

    viable = []

    for pair, res in all_results.items():
        tc = res['test_with_cost']
        tn = res['test_no_cost']
        train_c = res['train_with_cost']
        print(f"\n■ {pair}")
        print(f"  {'':10}  {'Sharpe':>7} {'年率%':>7} {'MaxDD%':>7} {'N':>5} {'勝率%':>6} {'TP%':>6} {'Trail%':>7} {'SL%':>6}")
        print(f"  {'訓練(コスト込)':10}  {train_c['sharpe']:>7.2f} {train_c['ret_annual']:>7.1f} {train_c['dd']:>7.1f} {train_c['n']:>5} {train_c['win_rate']:>6.1f} {train_c['tp_rate']:>6.1f} {train_c['trail_rate']:>7.1f} {train_c['sl_rate']:>6.1f}")
        print(f"  {'検証(コストなし)':10}  {tn['sharpe']:>7.2f} {tn['ret_annual']:>7.1f} {tn['dd']:>7.1f} {tn['n']:>5} {tn['win_rate']:>6.1f} {tn['tp_rate']:>6.1f} {tn['trail_rate']:>7.1f} {tn['sl_rate']:>6.1f}")
        print(f"  {'検証(コスト込) ':10}  {tc['sharpe']:>7.2f} {tc['ret_annual']:>7.1f} {tc['dd']:>7.1f} {tc['n']:>5} {tc['win_rate']:>6.1f} {tc['tp_rate']:>6.1f} {tc['trail_rate']:>7.1f} {tc['sl_rate']:>6.1f}  ← 重要")

        cost_impact = tn['ret_annual'] - tc['ret_annual']
        print(f"  コスト影響: -{cost_impact:.1f}%/年")

        if tc['sharpe'] > 0.5 and tc['dd'] < 30 and tc['n'] >= 10:
            print(f"  判定: ✅ 採用候補")
            viable.append((pair, tc['sharpe'], tc['ret_annual'], tc['dd']))
        elif tc['sharpe'] > 0:
            print(f"  判定: ⚠️  要観察")
        else:
            print(f"  判定: ❌ 不採用")

    print("\n" + "=" * 65)
    print("【総評】")
    if viable:
        viable.sort(key=lambda x: x[1], reverse=True)
        print(f"  採用候補: {len(viable)}ペア")
        for pair, sharpe, ret, dd in viable:
            print(f"    {pair}: Sharpe={sharpe:.2f} / 年率={ret:+.1f}% / DD={dd:.1f}%")
    else:
        print("  採用候補なし（フィルター強化 or パラメーター見直し推奨）")
    print("=" * 65)

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    print("BB逆張り リアルバックテスト開始")
    print(f"  BB: period={BB_PERIOD} / σ={BB_SIGMA}")
    print(f"  HTF: 1h BB ±{HTF_RANGE_LIMIT}σ超でスキップ")
    print(f"  RSI: sell>={RSI_SELL_MIN} / buy<={RSI_BUY_MAX}")
    print(f"  TrailSL: S1(ATR×{STAGE1_ACTIVATE}) / S2(ATR×{STAGE2_ACTIVATE}) / S3(ATR×{STAGE3_ACTIVATE})")
    print(f"  コスト: JPY={SPREAD_JPY}pips+{COMMISSION}pips / 非JPY={SPREAD_OTHER}pips+{COMMISSION}pips\n")

    data = get_data()
    if not data:
        print("データ取得失敗 → 終了")
        return

    all_results = {}
    for name, cfg in data.items():
        result = walk_forward(
            cfg['5m'], cfg['1h'], cfg['is_jpy'], label=name
        )
        all_results[name] = result

    print_results(all_results)

    out_path = r'C:\Users\Administrator\fx_bot\vps\backtest_bb_realistic_result.json'
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n結果を {out_path} に保存しました")
    except Exception as e:
        print(f"JSON保存エラー: {e}")
        # カレントディレクトリに保存
        with open('backtest_bb_realistic_result.json', 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        print("カレントディレクトリに保存しました")

if __name__ == '__main__':
    main()