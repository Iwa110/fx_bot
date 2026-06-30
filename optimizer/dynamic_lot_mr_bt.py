"""
dynamic_lot_mr_bt.py - 相関クロスの「動的ロット制御」平均回帰バックテスト。

対象: AUDCAD / EURGBP (本プロジェクトで構造的エッジが確認済みの相関クロス)。
時間足: 15m または 1h。

ベースロジック (Z-score 平均回帰の逆張り):
    期間 N の移動平均からの乖離を Z-score = (close - MA) / SD で計算。
    |Z| >= z_in でエントリー (Z>0=買われすぎ→short / Z<0=売られすぎ→long)。
    1ポジション同時保有 (no pyramiding)。指標は確定足、約定は次足始値。

動的ロット制御 (Dynamic Position Sizing):
    1) Z-score スケーリング: 乖離が大きいほど平均回帰圧力が高いとみなしロットを厚くする。
         lot_z = clip(1 + zk * (|Z| - z_in), 1, max_lot)   (zk=1.0 → z_in+0.5 で 1.5)
    2) ボラティリティ・アジャストメント: 直近 ATR の歴史的パーセンタイルで補正。
         - squeeze (ATR pct < squeeze_lo): 異常な低ボラからの乖離はダマシになりやすく lot を下げる。
         - extreme (ATR pct > vol_hi): 過大ボラの乖離も危険で lot を下げる。
         - 適度な帯域: 等倍 (1.0)。
    sizing_mode: fixed(=ベースライン) / zscale / voladj / combo。

決済:
    TP: 平均(MA)への回帰 or 反対側 ±tp_sigma バンド (z_tp で指定)。
    SL: (a) タイムストップ N 期間 (max_hold) で強制成行、
        (b) 極端な異常値 |Z| >= z_stop (= ±4σ 等) のハードストップ。

検証規律:
    - Lookahead 排除: MA/SD/ATR は確定足(close)で算出、約定は次足始値。
      保有中の TP/SL 判定は t-1 の MA/SD/ATR を参照しバー高安でヒット判定。
    - フルコスト: spread+slippage(pips) を往復で lot 加重して控除。
    - IS=2015-2021 / OOS=2022-2026 分割で頑健性を確認。
    - 動的ロット vs 固定ロット(ベースライン) の OOS PF・最大DD 差分を出力。

使用法:
    python3 optimizer/dynamic_lot_mr_bt.py
    python3 optimizer/dynamic_lot_mr_bt.py --tf 15m --pairs AUDCAD EURGBP
    python3 optimizer/dynamic_lot_mr_bt.py --sweep        # 主要パラメータをグリッド探索
"""

import argparse
import os

import numpy as np
import pandas as pd

import liquidity_sweep_bt as LS   # データロード・pip/コスト定義を再利用

HERE = os.path.dirname(os.path.abspath(__file__))
IS_END = pd.Timestamp('2022-01-01', tz='UTC')      # IS=2015-2021, OOS=2022-
WFO_YEARS = [2022, 2023, 2024, 2025, 2026]


# ----------------------------------------------------------------------------
# 上位足(HTF)レジーム指標 (Phase 5)
# ----------------------------------------------------------------------------
def add_htf_regime(df, htf_tf='4h', adx_n=14, slope_ma=50, slope_lb=10):
    """1h足を htf_tf にリサンプルして ADX(14) と SMA傾き(ATR正規化) を算出し、
    1h足インデックスへ map して返す。

    Lookahead 排除: HTF指標は shift(1) で「直近の確定済みHTFバー」のみ使用し、
    その後 1h へ前方フィル。進行中のHTFバーは決して参照しない。
    """
    agg = df.resample(htf_tf).agg({'open': 'first', 'high': 'max',
                                   'low': 'min', 'close': 'last'}).dropna()
    h, l, c = agg['high'], agg['low'], agg['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / adx_n, adjust=False).mean()
    # --- ADX (Wilder) ---
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm = pd.Series(plus_dm, index=agg.index).ewm(alpha=1.0 / adx_n, adjust=False).mean()
    minus_dm = pd.Series(minus_dm, index=agg.index).ewm(alpha=1.0 / adx_n, adjust=False).mean()
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100.0 * plus_dm / atr_safe
    minus_di = 100.0 * minus_dm / atr_safe
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / adx_n, adjust=False).mean()
    # --- SMA 傾き (ATR正規化, slope_lb 本前との差) ---
    sma = c.rolling(slope_ma).mean()
    slope = (sma - sma.shift(slope_lb)) / atr_safe
    # --- 確定済みHTFバーのみ -> 1h へ ffill (lookahead 無し) ---
    adx_1h = adx.shift(1).reindex(df.index, method='ffill')
    slope_1h = slope.shift(1).reindex(df.index, method='ffill')
    return adx_1h, slope_1h


# ----------------------------------------------------------------------------
# 指標
# ----------------------------------------------------------------------------
def add_indicators(df, n, atr_n, atr_lookback, ema_span=5, rsi_n=7,
                   htf_tf='4h', htf_adx_n=14, htf_slope_ma=50, htf_slope_lb=10):
    """MA/SD/Z-score(close基準) + ATR・ATRパーセンタイル + 反転確認用の短期EMA/短期RSI
    + 上位足(HTF)レジーム(ADX/SMA傾き)。

    全て確定足(close)ベースで lookahead 無し。EMA/RSI は反転確認フィルタ(パターンA/C)用、
    HTF は Phase5 レジームフィルタ用。
    """
    out = df.copy()
    c = out['close']
    ma = c.rolling(n).mean()
    sd = c.rolling(n).std(ddof=0)
    out['ma'] = ma
    out['sd'] = sd
    out['z'] = (c - ma) / sd.replace(0, np.nan)
    # True Range -> ATR (Wilder 風: ewm)
    pc = c.shift(1)
    tr = pd.concat([(out['high'] - out['low']),
                    (out['high'] - pc).abs(),
                    (out['low'] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / atr_n, adjust=False).mean()
    out['atr'] = atr
    # ATR の歴史的パーセンタイル (過去 atr_lookback 本での順位, lookahead 無し)
    out['atr_pct'] = atr.rolling(atr_lookback).apply(
        lambda w: (w[-1] >= w).mean(), raw=True)
    # 反転確認: 短期EMA (パターンA) と 短期RSI (パターンC)
    out['ema_s'] = c.ewm(span=ema_span, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    ag = gain.ewm(alpha=1.0 / rsi_n, adjust=False).mean()
    al = loss.ewm(alpha=1.0 / rsi_n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    out['rsi'] = 100.0 - 100.0 / (1.0 + rs)
    out['rsi'] = out['rsi'].fillna(50.0)
    # 上位足レジーム (Phase5)
    adx_1h, slope_1h = add_htf_regime(df, htf_tf, htf_adx_n, htf_slope_ma, htf_slope_lb)
    out['htf_adx'] = adx_1h
    out['htf_slope'] = slope_1h
    return out


def htf_allows(cfg, i, htf_adx, htf_slope):
    """Phase5 HTFレジームゲート: レンジ相場のみエントリー許可。"""
    mode = cfg.get('htf_filter', 'none')
    if mode == 'none':
        return True
    if mode == 'adx':
        v = htf_adx[i]
        return (not np.isnan(v)) and v < cfg['adx_max']
    if mode == 'slope':
        v = htf_slope[i]
        return (not np.isnan(v)) and abs(v) <= cfg['slope_max']
    return True


# ----------------------------------------------------------------------------
# 反転確認トリガ (パターンA: 短期EMAクロス / B: ローソク足 / C: RSIフック)
# 全て確定足 j で判定 -> 約定は j+1 始値 (lookahead 無し)。side=反転方向。
# ----------------------------------------------------------------------------
def trig_ema(side, j, c, ema):
    """パターンA: 価格が短期EMAを反転方向にクロス。"""
    if j < 1 or np.isnan(ema[j]) or np.isnan(ema[j - 1]):
        return False
    if side == 'long':
        return c[j] > ema[j] and c[j - 1] <= ema[j - 1]
    return c[j] < ema[j] and c[j - 1] >= ema[j - 1]


def trig_rsi(side, j, rsi, rsi_os, rsi_ob):
    """パターンC: RSIが極値から基準へフック(反転の初動)。"""
    if j < 1 or np.isnan(rsi[j]) or np.isnan(rsi[j - 1]):
        return False
    if side == 'long':
        return rsi[j - 1] <= rsi_os and rsi[j] > rsi[j - 1]
    return rsi[j - 1] >= rsi_ob and rsi[j] < rsi[j - 1]


def trig_candle(side, j, o, h, l, c):
    """パターンB: 反転を示すプライスアクション(包み足 or ピンバー)が確定。"""
    if j < 1:
        return False
    body = abs(c[j] - o[j])
    rng = h[j] - l[j]
    if rng <= 0:
        return False
    if side == 'long':
        bull_engulf = (c[j] > o[j] and c[j - 1] < o[j - 1] and
                       c[j] >= o[j - 1] and o[j] <= c[j - 1])
        lower_wick = min(o[j], c[j]) - l[j]
        pinbar = lower_wick >= 2.0 * body and c[j] >= l[j] + 0.6 * rng
        return bull_engulf or pinbar
    bear_engulf = (c[j] < o[j] and c[j - 1] > o[j - 1] and
                   c[j] <= o[j - 1] and o[j] >= c[j - 1])
    upper_wick = h[j] - max(o[j], c[j])
    pinbar = upper_wick >= 2.0 * body and c[j] <= h[j] - 0.6 * rng
    return bear_engulf or pinbar


def find_confirmed_entry(side, setup_i, arrays, cfg, n):
    """乖離(setup)成立後、confirm_window 本以内に反転トリガが確定したバー j を探し、
    約定バー j+1 を返す。見つからない/平均回帰済みなら None。"""
    o, h, l, c, ema, rsi, z = arrays
    mode = cfg['confirm_mode']
    window = cfg['confirm_window']
    os_, ob_ = cfg['rsi_os'], cfg['rsi_ob']
    for j in range(setup_i + 1, min(setup_i + 1 + window, n - 1)):
        # 既にMAを越えて反転完了 = setup消費済み -> 中止 (落ちた後に買わない)
        if side == 'long' and z[j] >= 0:
            return None
        if side == 'short' and z[j] <= 0:
            return None
        if mode == 'ema':
            ok = trig_ema(side, j, c, ema)
        elif mode == 'candle':
            ok = trig_candle(side, j, o, h, l, c)
        elif mode == 'rsi':
            ok = trig_rsi(side, j, rsi, os_, ob_)
        else:
            ok = True
        if ok:
            return j + 1
    return None


# ----------------------------------------------------------------------------
# 動的ロット
# ----------------------------------------------------------------------------
def position_lot(z_abs, atr_pct, cfg):
    """エントリー時の Z と ATR パーセンタイルから建てロットを決める。"""
    mode = cfg['sizing_mode']
    lot = 1.0
    if mode in ('zscale', 'combo'):
        lot = 1.0 + cfg['zk'] * (z_abs - cfg['z_in'])
        lot = float(np.clip(lot, 1.0, cfg['max_lot']))
    if mode in ('voladj', 'combo'):
        vol_mult = 1.0
        if not np.isnan(atr_pct):
            if atr_pct < cfg['squeeze_lo']:
                vol_mult = cfg['squeeze_mult']     # 低ボラ(スクイーズ)= ダマシ多 → 縮小
            elif atr_pct > cfg['vol_hi']:
                vol_mult = cfg['vol_hi_mult']      # 過大ボラ = 危険 → 縮小
        lot *= vol_mult
    return max(lot, 0.0)


# ----------------------------------------------------------------------------
# バックテスト・エンジン
# ----------------------------------------------------------------------------
def run_bt(df, pip, cost_pips, cfg):
    """Z-score 平均回帰 + 動的ロット。1ポジション。次足始値約定・lookahead 無し。"""
    o = df['open'].to_numpy()
    h = df['high'].to_numpy()
    l = df['low'].to_numpy()
    ma = df['ma'].to_numpy()
    sd = df['sd'].to_numpy()
    z = df['z'].to_numpy()
    atr_pct = df['atr_pct'].to_numpy()
    c = df['close'].to_numpy()
    ema = df['ema_s'].to_numpy() if 'ema_s' in df else np.full(len(df), np.nan)
    rsi = df['rsi'].to_numpy() if 'rsi' in df else np.full(len(df), np.nan)
    htf_adx = df['htf_adx'].to_numpy() if 'htf_adx' in df else np.full(len(df), np.nan)
    htf_slope = df['htf_slope'].to_numpy() if 'htf_slope' in df else np.full(len(df), np.nan)
    idx = df.index
    n = len(df)

    z_in = cfg['z_in']
    z_tp = cfg['z_tp']           # |Z|<=z_tp で利確 (0=MA回帰, 1=反対1σ手前 など)
    z_stop = cfg['z_stop']       # |Z|>=z_stop でハードストップ
    max_hold = cfg['max_hold']
    cost = cost_pips * pip
    confirm_mode = cfg.get('confirm_mode', 'none')
    arrays = (o, h, l, c, ema, rsi, z)

    trades = []
    i = z_warmup(df)
    while i < n - 1:
        zi = z[i]
        if np.isnan(zi) or np.isnan(sd[i]) or sd[i] <= 0:
            i += 1
            continue
        side = None
        if zi >= z_in:
            side = 'short'
        elif zi <= -z_in:
            side = 'long'
        if side is None:
            i += 1
            continue

        # Phase5 HTFレジームゲート (確認待ち無し = エントリー価格は劣化しない)
        if not htf_allows(cfg, i, htf_adx, htf_slope):
            i += 1
            continue

        lot = position_lot(abs(zi), atr_pct[i], cfg)
        if lot <= 0:
            i += 1
            continue

        # --- 反転確認フィルタ (none=即エントリー / ema/candle/rsi=確認後) ---
        if confirm_mode == 'none':
            entry_idx = i + 1
        else:
            entry_idx = find_confirmed_entry(side, i, arrays, cfg, n)
            if entry_idx is None:
                i += 1
                continue
        entry = o[entry_idx]

        # --- 保有監視: 各バー j で t-1(=j-1) の MA/SD を基準に TP/SL 価格を決める ---
        exit_price = None
        exit_idx = None
        exit_reason = None
        mae_adv = 0.0                           # 保有中の最大逆行(価格, 計測のみ)
        for j in range(entry_idx, n):
            adv = (h[j] - entry) if side == 'short' else (entry - l[j])
            if adv > mae_adv:
                mae_adv = adv
            ref = j - 1                         # 確定済みバー (lookahead 無し)
            mref, sref = ma[ref], sd[ref]
            if np.isnan(mref) or np.isnan(sref) or sref <= 0:
                continue
            if side == 'short':
                # ハードストップ: 価格が +z_stop シグマに到達
                sl_px = mref + z_stop * sref
                # 利確: |Z|<=z_tp に対応する価格 (MA + z_tp*SD)
                tp_px = mref + z_tp * sref
                hit_sl = h[j] >= sl_px
                hit_tp = l[j] <= tp_px
            else:
                sl_px = mref - z_stop * sref
                tp_px = mref - z_tp * sref
                hit_sl = l[j] <= sl_px
                hit_tp = h[j] >= tp_px
            if hit_sl:                          # 同足両ヒットは SL 優先 (保守的)
                exit_price, exit_idx, exit_reason = sl_px, j, 'zstop'
                break
            if hit_tp:
                exit_price, exit_idx, exit_reason = tp_px, j, 'tp'
                break
            if j - entry_idx >= max_hold:        # タイムストップ -> 次足始値で成行
                k = min(j + 1, n - 1)
                exit_price, exit_idx, exit_reason = o[k], k, 'time'
                break
        if exit_price is None:
            exit_price, exit_idx, exit_reason = c[-1], n - 1, 'eod'

        gross = (entry - exit_price) if side == 'short' else (exit_price - entry)
        gross_pips = gross / pip
        net_pips = (gross_pips - cost_pips) * lot     # コストも lot 加重
        trades.append({
            'side': side, 'entry_t': idx[entry_idx], 'exit_t': idx[exit_idx],
            'entry': entry, 'exit': exit_price, 'lot': lot, 'z_in_actual': zi,
            'hold_bars': int(exit_idx - entry_idx), 'gross_pips': gross_pips,
            'net_pips': net_pips, 'reason': exit_reason,
            'mae_lotpips': max(mae_adv, 0.0) / pip * lot,   # lot加重 MAE(pips)
        })
        i = exit_idx + 1

    return _metrics(trades), trades


def run_bt_tiered(df, pip, cost_pips, cfg):
    """Phase6 層化(Tiered)エントリー。1シグナルを 2段(各 tier_lot)に空間分割。
       第1段: |Z|>=z_in、第2段: 保有中にさらに逆行し |Z|>=z_in2。均等ロット(非マーチン)。
       決済: 全段を MA(Z=0)回帰 or ハードストップ(|Z|>=z_stop) or タイムストップで一括。
       次足始値約定・lookahead 無し。HTFゲートも適用可。"""
    o = df['open'].to_numpy()
    h = df['high'].to_numpy()
    l = df['low'].to_numpy()
    c = df['close'].to_numpy()
    ma = df['ma'].to_numpy()
    sd = df['sd'].to_numpy()
    z = df['z'].to_numpy()
    htf_adx = df['htf_adx'].to_numpy() if 'htf_adx' in df else np.full(len(df), np.nan)
    htf_slope = df['htf_slope'].to_numpy() if 'htf_slope' in df else np.full(len(df), np.nan)
    idx = df.index
    n = len(df)
    z_in, z_in2 = cfg['z_in'], cfg['z_in2']
    z_tp, z_stop, max_hold = cfg['z_tp'], cfg['z_stop'], cfg['max_hold']
    tier_lot = cfg['tier_lot']

    trades = []
    i = z_warmup(df)
    while i < n - 1:
        zi = z[i]
        if np.isnan(zi) or np.isnan(sd[i]) or sd[i] <= 0:
            i += 1
            continue
        side = 'short' if zi >= z_in else ('long' if zi <= -z_in else None)
        if side is None:
            i += 1
            continue
        if not htf_allows(cfg, i, htf_adx, htf_slope):
            i += 1
            continue

        e1 = i + 1
        legs = [o[e1]]               # 各レッグ建値 (ロットは tier_lot で均等)
        tier2_done = False
        exit_price = exit_idx = reason = None
        j = e1
        while j < n:
            ref = j - 1
            mref, sref = ma[ref], sd[ref]
            valid = not (np.isnan(mref) or np.isnan(sref) or sref <= 0)
            if valid:
                # 第2段: 確定足 j-1 でさらに逆行 -> j 始値で追加 (1回のみ)
                if not tier2_done and j > e1:
                    zp = z[j - 1]
                    deeper = ((side == 'short' and zp >= z_in2) or
                              (side == 'long' and zp <= -z_in2))
                    if deeper:
                        legs.append(o[j])
                        tier2_done = True
                if side == 'short':
                    sl_px, tp_px = mref + z_stop * sref, mref + z_tp * sref
                    hit_sl, hit_tp = h[j] >= sl_px, l[j] <= tp_px
                else:
                    sl_px, tp_px = mref - z_stop * sref, mref - z_tp * sref
                    hit_sl, hit_tp = l[j] <= sl_px, h[j] >= tp_px
                if hit_sl:
                    exit_price, exit_idx, reason = sl_px, j, 'zstop'
                    break
                if hit_tp:
                    exit_price, exit_idx, reason = tp_px, j, 'tp'
                    break
            if j - e1 >= max_hold:
                k = min(j + 1, n - 1)
                exit_price, exit_idx, reason = o[k], k, 'time'
                break
            j += 1
        if exit_price is None:
            exit_price, exit_idx, reason = c[-1], n - 1, 'eod'

        net = 0.0
        for ep in legs:
            g = (ep - exit_price) if side == 'short' else (exit_price - ep)
            net += (g / pip - cost_pips) * tier_lot     # 各レッグが往復コスト負担
        trades.append({
            'side': side, 'entry_t': idx[e1], 'exit_t': idx[exit_idx],
            'entry': legs[0], 'avg_entry': float(np.mean(legs)), 'n_legs': len(legs),
            'lot': len(legs) * tier_lot, 'z_in_actual': zi,
            'hold_bars': int(exit_idx - e1), 'net_pips': net, 'reason': reason,
        })
        i = exit_idx + 1
    return _metrics(trades), trades


def run_bt_tiered3(df, pip, cost_pips, cfg):
    """Phase7 3段・不等分割(確率加重配分)エントリー。
       Tier1 z_tiers[0]=lot_tiers[0] / Tier2 / Tier3 を逆行に応じ追加 (最大合計=sum(lot_tiers))。
       決済A(exit_mode='A'): 全段を MA(Z=0)回帰で一括。
       決済B(exit_mode='B'): 最深 Tier3 のみ Z=partial_z で部分利確、残り(T1/T2)は MA で決済。
       SL共通: タイムストップ max_hold or |Z|>=z_stop で全決済。次足始値約定・lookahead 無し。"""
    o = df['open'].to_numpy()
    h = df['high'].to_numpy()
    l = df['low'].to_numpy()
    c = df['close'].to_numpy()
    ma = df['ma'].to_numpy()
    sd = df['sd'].to_numpy()
    z = df['z'].to_numpy()
    htf_adx = df['htf_adx'].to_numpy() if 'htf_adx' in df else np.full(len(df), np.nan)
    htf_slope = df['htf_slope'].to_numpy() if 'htf_slope' in df else np.full(len(df), np.nan)
    atr_pct = df['atr_pct'].to_numpy() if 'atr_pct' in df else np.full(len(df), np.nan)
    idx = df.index
    n = len(df)
    z_tiers = cfg['z_tiers']           # [2.0, 2.5, 3.0]
    lot_tiers = cfg['lot_tiers']       # [0.2, 0.3, 0.5]
    deepest = len(z_tiers) - 1         # Tier3 のインデックス
    exit_mode = cfg.get('exit_mode', 'A')
    partial_z = cfg.get('partial_z', 1.5)
    z_tp, z_stop, max_hold = cfg['z_tp'], cfg['z_stop'], cfg['max_hold']
    z_in = z_tiers[0]
    # 高ボラ・ロットスロットル: エントリー時 ATRパーセンタイル>=th なら全段 lot を mult 倍
    vt_th = cfg.get('vol_throttle_th', 1.01)
    vt_mult = cfg.get('vol_throttle_mult', 1.0)

    trades = []
    i = z_warmup(df)
    while i < n - 1:
        zi = z[i]
        if np.isnan(zi) or np.isnan(sd[i]) or sd[i] <= 0:
            i += 1
            continue
        side = 'short' if zi >= z_in else ('long' if zi <= -z_in else None)
        if side is None:
            i += 1
            continue
        if not htf_allows(cfg, i, htf_adx, htf_slope):
            i += 1
            continue

        # 高ボラ時はエントリー全体の lot を圧縮 (エントリーは止めない=エッジ維持)
        tmul = vt_mult if (not np.isnan(atr_pct[i]) and atr_pct[i] >= vt_th) else 1.0
        e1 = i + 1
        legs = [{'price': o[e1], 'lot': lot_tiers[0] * tmul, 'tier': 0, 'open': True}]
        next_tier = 1
        realized = 0.0                 # 部分利確の実現損益(pip*lot, コスト後)
        mae_basket = 0.0               # バスケット最大逆行(価格*lot, 計測のみ)
        exit_price = exit_idx = reason = None
        j = e1
        while j < n:
            # バスケットの逆行(含み損)を計測: 未決済レッグの worst 価格での合算損
            worst = h[j] if side == 'short' else l[j]
            bl = 0.0
            for leg in legs:
                if leg['open']:
                    adv = ((worst - leg['price']) if side == 'short'
                           else (leg['price'] - worst))
                    if adv > 0:
                        bl += adv * leg['lot']
            if bl > mae_basket:
                mae_basket = bl
            ref = j - 1
            mref, sref = ma[ref], sd[ref]
            valid = not (np.isnan(mref) or np.isnan(sref) or sref <= 0)
            if valid:
                # 深い段を確定足 j-1 の乖離で追加 (1バーで複数段に達する場合も順次)
                if j > e1:
                    zp = z[j - 1]
                    while next_tier < len(z_tiers):
                        thr = z_tiers[next_tier]
                        deeper = ((side == 'short' and zp >= thr) or
                                  (side == 'long' and zp <= -thr))
                        if not deeper:
                            break
                        legs.append({'price': o[j], 'lot': lot_tiers[next_tier] * tmul,
                                     'tier': next_tier, 'open': True})
                        next_tier += 1
                # 決済B: Tier3 を Z=partial_z で部分利確
                if exit_mode == 'B':
                    if side == 'short':
                        ptp_px = mref + partial_z * sref
                        hit_ptp = l[j] <= ptp_px
                    else:
                        ptp_px = mref - partial_z * sref
                        hit_ptp = h[j] >= ptp_px
                    if hit_ptp:
                        for leg in legs:
                            if leg['open'] and leg['tier'] == deepest:
                                g = ((leg['price'] - ptp_px) if side == 'short'
                                     else (ptp_px - leg['price']))
                                realized += (g / pip - cost_pips) * leg['lot']
                                leg['open'] = False
                # 全決済: ハードストップ / MA回帰
                if side == 'short':
                    sl_px, tp_px = mref + z_stop * sref, mref + z_tp * sref
                    hit_sl, hit_tp = h[j] >= sl_px, l[j] <= tp_px
                else:
                    sl_px, tp_px = mref - z_stop * sref, mref - z_tp * sref
                    hit_sl, hit_tp = l[j] <= sl_px, h[j] >= tp_px
                if hit_sl:
                    exit_price, exit_idx, reason = sl_px, j, 'zstop'
                    break
                if hit_tp:
                    exit_price, exit_idx, reason = tp_px, j, 'tp'
                    break
            if j - e1 >= max_hold:
                k = min(j + 1, n - 1)
                exit_price, exit_idx, reason = o[k], k, 'time'
                break
            j += 1
        if exit_price is None:
            exit_price, exit_idx, reason = c[-1], n - 1, 'eod'

        net = realized
        total_lot = sum(leg['lot'] for leg in legs)
        for leg in legs:                  # 残存(未部分利確)レッグを一括決済
            if leg['open']:
                g = ((leg['price'] - exit_price) if side == 'short'
                     else (exit_price - leg['price']))
                net += (g / pip - cost_pips) * leg['lot']
        trades.append({
            'side': side, 'entry_t': idx[e1], 'exit_t': idx[exit_idx],
            'entry': legs[0]['price'], 'n_legs': len(legs), 'lot': total_lot,
            'z_in_actual': zi, 'hold_bars': int(exit_idx - e1),
            'net_pips': net, 'reason': reason,
            'mae_lotpips': mae_basket / pip,        # lot加重 バスケットMAE(pips)
        })
        i = exit_idx + 1
    return _metrics(trades), trades


def z_warmup(df):
    """指標が出揃う最初のインデックス。"""
    valid = df[['ma', 'sd', 'atr_pct']].notna().all(axis=1)
    if valid.any():
        return int(np.argmax(valid.to_numpy()))
    return len(df)


def _metrics(trades):
    if not trades:
        return {'n': 0, 'pf': float('nan'), 'net_pips': 0.0, 'wr': float('nan'),
                'max_dd_pips': 0.0, 'avg_lot': float('nan'), 'expectancy': 0.0,
                'tp_rate': float('nan')}
    nets = np.array([t['net_pips'] for t in trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    gw, gl = wins.sum(), -losses.sum()
    pf = gw / gl if gl > 0 else float('inf')
    equity = np.cumsum(nets)
    peak = np.maximum.accumulate(equity)
    max_dd = (peak - equity).max() if len(equity) else 0.0
    tp_rate = np.mean([t['reason'] == 'tp' for t in trades])
    return {
        'n': len(trades), 'pf': pf, 'net_pips': float(nets.sum()),
        'wr': float((nets > 0).mean()), 'max_dd_pips': float(max_dd),
        'avg_lot': float(np.mean([t['lot'] for t in trades])),
        'expectancy': float(nets.mean()), 'tp_rate': float(tp_rate),
    }


# ----------------------------------------------------------------------------
# 評価 (IS/OOS/WFO + 固定 vs 動的 比較)
# ----------------------------------------------------------------------------
def base_cfg(args):
    return {
        'n': args.n, 'z_in': args.z_in, 'z_tp': args.z_tp, 'z_stop': args.z_stop,
        'max_hold': args.max_hold, 'zk': args.zk, 'max_lot': args.max_lot,
        'squeeze_lo': args.squeeze_lo, 'squeeze_mult': args.squeeze_mult,
        'vol_hi': args.vol_hi, 'vol_hi_mult': args.vol_hi_mult,
        'sizing_mode': 'fixed',
        'confirm_mode': 'none', 'confirm_window': args.confirm_window,
        'rsi_os': args.rsi_os, 'rsi_ob': args.rsi_ob,
        'htf_filter': 'none', 'adx_max': args.adx_max, 'slope_max': args.slope_max,
        'z_in2': args.z_in2, 'tier_lot': args.tier_lot,
        'z_tiers': list(args.tier3_zs), 'lot_tiers': list(args.tier3_lots),
        'partial_z': args.partial_z, 'exit_mode': 'A',
    }


def eval_runner(df, pip, cost_pips, cfg, runner):
    """任意のエンジン runner(seg,pip,cost,cfg)->(metrics,trades) の IS/OOS/年次WFO を計算。"""
    is_df = df[df.index < IS_END]
    oos_df = df[df.index >= IS_END]
    mi = runner(is_df, pip, cost_pips, cfg)[0]
    mo = runner(oos_df, pip, cost_pips, cfg)[0]
    fold_pfs = []
    for y in WFO_YEARS:
        seg = df[(df.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                 (df.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) > 200:
            mf = runner(seg, pip, cost_pips, cfg)[0]
            if mf['n'] >= 10 and not np.isnan(mf['pf']):
                fold_pfs.append(mf['pf'])
    return {'is': mi, 'oos': mo, 'wfo_min': min(fold_pfs) if fold_pfs else float('nan')}


def report_htf(pair, df, pip, cost_pips, cfg):
    """Phase5: HTFレジームフィルタ none/adx/slope を固定ロットで比較。"""
    print('=' * 112)
    print(f'[{pair}] HTFレジームフィルタ比較 (Phase5, 固定ロット, 確認待ち無し)  '
          f"N={cfg['n']} z_in={cfg['z_in']} max_hold={cfg['max_hold']}  "
          f"adx_max={cfg['adx_max']} slope_max={cfg['slope_max']}")
    print('=' * 112)
    variants = [('none', 'none'), ('adx<%.0f' % cfg['adx_max'], 'adx'),
                ('|slope|<%.2f' % cfg['slope_max'], 'slope')]
    rows = []
    base = None
    hdr = (f"{'htf_filter':14s}|{'IS_PF':>7s}{'IS_n':>7s}{'IS_WR':>7s}"
           f"  |{'OOS_PF':>7s}{'OOS_n':>7s}{'OOS_WR':>7s}{'OOS_DD':>8s}{'wfoMin':>8s}"
           f"  |{'dPF':>6s}{'n_ret':>7s}")
    print(hdr)
    print('-' * len(hdr))
    for label, mode in variants:
        c = dict(cfg)
        c['htf_filter'] = mode
        c['sizing_mode'] = 'fixed'
        c['confirm_mode'] = 'none'
        r = eval_runner(df, pip, cost_pips, c, run_bt)
        if base is None:
            base = r['oos']
        mi, mo = r['is'], r['oos']
        dpf = mo['pf'] - base['pf']
        n_ret = mo['n'] / base['n'] if base['n'] else float('nan')
        print(f"{label:14s}|{mi['pf']:>7.2f}{mi['n']:>7d}{mi['wr']*100:>6.0f}%"
              f"  |{mo['pf']:>7.2f}{mo['n']:>7d}{mo['wr']*100:>6.0f}%"
              f"{mo['max_dd_pips']:>8.0f}{r['wfo_min']:>8.2f}  |{dpf:>+6.2f}{n_ret*100:>6.0f}%")
        rows.append({
            'pair': pair, 'phase': 'htf', 'variant': mode, 'n': cfg['n'],
            'z_in': cfg['z_in'], 'max_hold': cfg['max_hold'],
            'adx_max': cfg['adx_max'], 'slope_max': cfg['slope_max'],
            'is_pf': mi['pf'], 'is_n': mi['n'], 'is_wr': mi['wr'],
            'oos_pf': mo['pf'], 'oos_n': mo['n'], 'oos_wr': mo['wr'],
            'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
            'wfo_min': r['wfo_min'], 'd_oos_pf': dpf, 'n_retained': n_ret,
        })
    print('\n  目的: 確認待ち(価格劣化)なしで、HTFトレンド時の質の悪い負け(死因B)だけ弾けるか。')
    print('        n減少に対し OOS PF が向上すれば成功。dPF<=0 ならゲートはエッジを生まない。')
    return rows


def report_tier(pair, df, pip, cost_pips, cfg):
    """Phase6: ベースライン(Z=2で1ロット一括) vs 層化エントリー(0.5+0.5)。"""
    print('=' * 112)
    print(f'[{pair}] 層化(Tiered)エントリー比較 (Phase6)  '
          f"N={cfg['n']} z_in={cfg['z_in']} z_tp={cfg['z_tp']} z_stop={cfg['z_stop']} "
          f"max_hold={cfg['max_hold']} tier_lot={cfg['tier_lot']}")
    print('=' * 112)
    rows = []
    base = None
    hdr = (f"{'variant':18s}|{'IS_PF':>7s}{'IS_n':>6s}"
           f"  |{'OOS_PF':>7s}{'OOS_WR':>7s}{'OOS_net':>9s}{'OOS_DD':>8s}{'avgLot':>7s}"
           f"  |{'net/lot':>9s}{'DD/lot':>8s}{'wfoMin':>8s}")
    print(hdr)
    print('-' * len(hdr))
    # baseline: 1.0ロット単発 (同一の MA回帰TP / z_stop / max_hold)
    cb = dict(cfg)
    cb['sizing_mode'] = 'fixed'
    cb['confirm_mode'] = 'none'
    cb['htf_filter'] = 'none'
    rb = eval_runner(df, pip, cost_pips, cb, run_bt)
    base = rb['oos']
    variants = [('baseline z2 x1.0', None, rb)]
    # tiered: z_in2 = 2.5 / 3.0
    for zin2 in cfg.get('tier_z_grid', [2.5, 3.0]):
        ct = dict(cfg)
        ct['z_in2'] = zin2
        ct['htf_filter'] = 'none'
        rt = eval_runner(df, pip, cost_pips, ct, run_bt_tiered)
        variants.append((f'tiered z2+z{zin2}', zin2, rt))

    def line(label, r):
        mi, mo = r['is'], r['oos']
        al = mo['avg_lot'] if mo['avg_lot'] and not np.isnan(mo['avg_lot']) else 1.0
        print(f"{label:18s}|{mi['pf']:>7.2f}{mi['n']:>6d}"
              f"  |{mo['pf']:>7.2f}{mo['wr']*100:>6.0f}%"
              f"{mo['net_pips']:>9.0f}{mo['max_dd_pips']:>8.0f}{al:>7.2f}"
              f"  |{mo['net_pips']/al:>9.0f}{mo['max_dd_pips']/al:>8.0f}{r['wfo_min']:>8.2f}")

    for label, zin2, r in variants:
        line(label, r)
        mo = r['oos']
        al = mo['avg_lot'] if mo['avg_lot'] and not np.isnan(mo['avg_lot']) else 1.0
        rows.append({
            'pair': pair, 'phase': 'tier', 'variant': label, 'z_in2': zin2,
            'n': cfg['n'], 'z_in': cfg['z_in'], 'tier_lot': cfg['tier_lot'],
            'is_pf': r['is']['pf'], 'is_n': r['is']['n'],
            'oos_pf': mo['pf'], 'oos_n': mo['n'], 'oos_wr': mo['wr'],
            'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
            'oos_avglot': mo['avg_lot'], 'oos_net_per_lot': mo['net_pips'] / al,
            'oos_dd_per_lot': mo['max_dd_pips'] / al, 'wfo_min': r['wfo_min'],
        })
    print('\n  注: tiered は第2段が出ない時 exposure=0.5ロット (avgLot 参照) でbaseline(1.0)より')
    print('      平均 exposure が小さい。pip*lot は線形なので exposure 正規化した net/lot・DD/lot が')
    print('      「同じ資本(平均建玉)に揃えた」公平比較。これが baseline比で改善すれば資本効率の純増。')
    return rows


def report_tier3(pair, df, pip, cost_pips, cfg):
    """Phase7: ベースライン vs 3段不等分割(決済A 一括 / 決済B 部分利確) を比較。"""
    z_tiers, lot_tiers = cfg['z_tiers'], cfg['lot_tiers']
    print('=' * 118)
    print(f'[{pair}] 3段・不等分割エントリー (Phase7)  '
          f"z_tiers={z_tiers} lot_tiers={lot_tiers} (max合計={sum(lot_tiers):.1f}) "
          f"partial_z={cfg['partial_z']} z_stop={cfg['z_stop']} max_hold={cfg['max_hold']}")
    print('=' * 118)
    # baseline: 1.0ロット単発、同一 z_stop / max_hold
    cb = dict(cfg)
    cb['sizing_mode'] = 'fixed'
    cb['confirm_mode'] = 'none'
    cb['htf_filter'] = 'none'
    cb['z_in'] = z_tiers[0]
    rb = eval_runner(df, pip, cost_pips, cb, run_bt)
    # 構成2/3: 不等分割 決済A / 決済B
    ca = dict(cfg); ca['exit_mode'] = 'A'; ca['htf_filter'] = 'none'
    cbb = dict(cfg); cbb['exit_mode'] = 'B'; cbb['htf_filter'] = 'none'
    ra = eval_runner(df, pip, cost_pips, ca, run_bt_tiered3)
    rbb = eval_runner(df, pip, cost_pips, cbb, run_bt_tiered3)

    base = rb['oos']
    # 最大exposure を揃えた raw 比較 (spec: baseline 1.0 = tiered max合計 1.0)
    hdr = (f"{'構成':26s}|{'OOS_PF':>7s}{'net':>9s}{'maxDD':>8s}{'avgLot':>7s}"
           f"  |{'PF%':>7s}{'net%':>8s}{'DD%':>8s}  |{'net/DD':>7s}{'wfoMin':>7s}")
    print(hdr)
    print('-' * len(hdr))
    rows = []
    for label, r in [('1 baseline z2 x1.0', rb),
                     ('2 不等分割+決済A(一括)', ra),
                     ('3 不等分割+決済B(部分利確)', rbb)]:
        mo = r['oos']
        al = mo['avg_lot'] if mo['avg_lot'] and not np.isnan(mo['avg_lot']) else 1.0
        # raw 変化率 (最大exposure一致なので raw net/DD をそのまま比較)
        pf_pct = (mo['pf'] / base['pf'] - 1) * 100 if base['pf'] else float('nan')
        net_pct = (mo['net_pips'] / base['net_pips'] - 1) * 100 if base['net_pips'] else float('nan')
        dd_pct = (mo['max_dd_pips'] / base['max_dd_pips'] - 1) * 100 if base['max_dd_pips'] else float('nan')
        net_dd = mo['net_pips'] / mo['max_dd_pips'] if mo['max_dd_pips'] else float('nan')
        print(f"{label:26s}|{mo['pf']:>7.2f}{mo['net_pips']:>9.0f}{mo['max_dd_pips']:>8.0f}"
              f"{al:>7.2f}  |{pf_pct:>+6.1f}%{net_pct:>+7.1f}%{dd_pct:>+7.1f}%"
              f"  |{net_dd:>7.2f}{r['wfo_min']:>7.2f}")
        rows.append({
            'pair': pair, 'phase': 'tier3', 'config': label,
            'oos_pf': mo['pf'], 'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
            'oos_avglot': al, 'net_dd_ratio': net_dd,
            'is_pf': r['is']['pf'], 'wfo_min': r['wfo_min'],
            'pf_chg_pct': pf_pct, 'net_chg_pct': net_pct, 'dd_chg_pct': dd_pct,
        })
    # 考察: A vs B の Net/DD
    nd_a = ra['oos']['net_pips'] / ra['oos']['max_dd_pips'] if ra['oos']['max_dd_pips'] else float('nan')
    nd_b = rbb['oos']['net_pips'] / rbb['oos']['max_dd_pips'] if rbb['oos']['max_dd_pips'] else float('nan')
    better = 'B(部分利確)' if nd_b > nd_a else 'A(一括)'
    print(f"\n  考察: Net/DD比 = A {nd_a:.2f} vs B {nd_b:.2f} → リスク調整後は {better} が優位。")
    print('  (最大exposure=1.0で揃えた raw 比較。net/DD・PFは scale不変。avgLot<1.0 は')
    print('   深い段が常には埋まらず平均建玉が小さい=同じ最大リスク枠で資本を温存している意味。)')
    return rows


def eval_confirm(df, pip, cost_pips, cfg, confirm_mode):
    """1構成(confirm_mode 指定, sizing=fixed)の IS/OOS/年次WFO を計算。"""
    c = dict(cfg)
    c['confirm_mode'] = confirm_mode
    c['sizing_mode'] = 'fixed'      # フィルタの純効果を見るため固定ロット
    is_df = df[df.index < IS_END]
    oos_df = df[df.index >= IS_END]
    mi, _ = run_bt(is_df, pip, cost_pips, c)
    mo, _ = run_bt(oos_df, pip, cost_pips, c)
    fold_pfs = []
    for y in WFO_YEARS:
        seg = df[(df.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                 (df.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) > 200:
            mf, _ = run_bt(seg, pip, cost_pips, c)
            if mf['n'] >= 10 and not np.isnan(mf['pf']):
                fold_pfs.append(mf['pf'])
    wfo_min = min(fold_pfs) if fold_pfs else float('nan')
    return {'is': mi, 'oos': mo, 'wfo_min': wfo_min}


def report_filters(pair, df, pip, cost_pips, cfg):
    """反転確認フィルタ none/ema/candle/rsi を固定ロットで比較 (Phase 3)。"""
    print('=' * 110)
    print(f'[{pair}] 反転確認フィルタ比較 (固定ロット)  bars={len(df)}  '
          f"N={cfg['n']} z_in={cfg['z_in']} z_tp={cfg['z_tp']} z_stop={cfg['z_stop']} "
          f"max_hold={cfg['max_hold']} confirm_window={cfg['confirm_window']}")
    print('=' * 110)
    modes = ['none', 'ema', 'candle', 'rsi']
    res = {m: eval_confirm(df, pip, cost_pips, cfg, m) for m in modes}
    base = res['none']
    hdr = (f"{'filter':8s}|{'IS_PF':>7s}{'IS_n':>7s}{'IS_WR':>7s}"
           f"  |{'OOS_PF':>7s}{'OOS_n':>7s}{'OOS_WR':>7s}{'OOS_DD':>8s}{'wfoMin':>8s}"
           f"  |{'dPF':>6s}{'n_ret':>7s}")
    print(hdr)
    print('-' * len(hdr))
    rows = []
    for m in modes:
        r = res[m]
        mi, mo = r['is'], r['oos']
        dpf = mo['pf'] - base['oos']['pf']
        n_ret = mo['n'] / base['oos']['n'] if base['oos']['n'] else float('nan')
        print(f"{m:8s}|{mi['pf']:>7.2f}{mi['n']:>7d}{mi['wr']*100:>6.0f}%"
              f"  |{mo['pf']:>7.2f}{mo['n']:>7d}{mo['wr']*100:>6.0f}%"
              f"{mo['max_dd_pips']:>8.0f}{r['wfo_min']:>8.2f}"
              f"  |{dpf:>+6.2f}{n_ret*100:>6.0f}%")
        rows.append({
            'pair': pair, 'confirm': m, 'n': cfg['n'], 'z_in': cfg['z_in'],
            'z_tp': cfg['z_tp'], 'max_hold': cfg['max_hold'],
            'confirm_window': cfg['confirm_window'],
            'is_pf': mi['pf'], 'is_n': mi['n'], 'is_wr': mi['wr'],
            'oos_pf': mo['pf'], 'oos_n': mo['n'], 'oos_wr': mo['wr'],
            'oos_net': mo['net_pips'], 'oos_dd': mo['max_dd_pips'],
            'oos_tp_rate': mo['tp_rate'], 'wfo_min': r['wfo_min'],
            'd_oos_pf': dpf, 'n_retained': n_ret,
        })
    print('\n  考察: フィルタで n が減り(質選別) PF が向上すれば「落ちるナイフ回避」が機能。')
    print('       n減少に PF向上が釣り合わない/IS-PF<1 なら、フィルタはコスト負けかエッジ不在。')
    return rows


def eval_mode(df, pip, cost_pips, cfg, mode):
    """1構成(sizing_mode 指定)の IS/OOS/年次WFO を計算。"""
    c = dict(cfg)
    c['sizing_mode'] = mode
    is_df = df[df.index < IS_END]
    oos_df = df[df.index >= IS_END]
    mi, _ = run_bt(is_df, pip, cost_pips, c)
    mo, _ = run_bt(oos_df, pip, cost_pips, c)
    fold_pfs = []
    folds = {}
    for y in WFO_YEARS:
        seg = df[(df.index >= pd.Timestamp(f'{y}-01-01', tz='UTC')) &
                 (df.index < pd.Timestamp(f'{y+1}-01-01', tz='UTC'))]
        if len(seg) > 200:
            mf, _ = run_bt(seg, pip, cost_pips, c)
            folds[y] = mf
            if mf['n'] >= 10 and not np.isnan(mf['pf']):
                fold_pfs.append(mf['pf'])
    wfo_min = min(fold_pfs) if fold_pfs else float('nan')
    return {'is': mi, 'oos': mo, 'folds': folds, 'wfo_min': wfo_min}


def fmt(m):
    return (f"PF={m['pf']:.2f} net={m['net_pips']:>9.0f}pip n={m['n']:>4d} "
            f"WR={m['wr']*100:4.1f}% DD={m['max_dd_pips']:>7.0f} "
            f"avgLot={m['avg_lot']:.2f} TP%={m['tp_rate']*100:3.0f}")


def report_pair(pair, df, pip, cost_pips, cfg):
    print('=' * 104)
    print(f'[{pair}]  bars={len(df)}  {df.index[0].date()}~{df.index[-1].date()}  '
          f"N={cfg['n']} z_in={cfg['z_in']} z_tp={cfg['z_tp']} z_stop={cfg['z_stop']} "
          f"max_hold={cfg['max_hold']}")
    print('=' * 104)
    modes = ['fixed', 'zscale', 'voladj', 'combo']
    results = {}
    for mode in modes:
        results[mode] = eval_mode(df, pip, cost_pips, cfg, mode)
    # IS / OOS テーブル
    for span in ('is', 'oos'):
        label = 'IS (2015-2021)' if span == 'is' else 'OOS(2022-2026)'
        print(f'\n  -- {label} --')
        for mode in modes:
            r = results[mode][span]
            print(f"    {mode:8s} | {fmt(r)}")
    # 固定 vs 動的の OOS 改善差分
    base = results['fixed']['oos']
    print('\n  -- 動的ロット vs 固定ロット (OOS) --')
    for mode in ['zscale', 'voladj', 'combo']:
        r = results[mode]['oos']
        d_pf = r['pf'] - base['pf']
        dd_chg = (r['max_dd_pips'] / base['max_dd_pips'] - 1) * 100 if base['max_dd_pips'] else float('nan')
        net_chg = (r['net_pips'] / base['net_pips'] - 1) * 100 if base['net_pips'] else float('nan')
        print(f"    {mode:8s} | dPF={d_pf:+.2f}  net{net_chg:+6.1f}%  maxDD{dd_chg:+6.1f}%  "
              f"wfoMin={results[mode]['wfo_min']:.2f} (fixed wfoMin={results['fixed']['wfo_min']:.2f})")
    return results


def collect_rows(pair, results, cfg):
    rows = []
    for mode, r in results.items():
        rows.append({
            'pair': pair, 'sizing_mode': mode, 'n': cfg['n'], 'z_in': cfg['z_in'],
            'z_tp': cfg['z_tp'], 'z_stop': cfg['z_stop'], 'max_hold': cfg['max_hold'],
            'is_pf': r['is']['pf'], 'is_n': r['is']['n'], 'is_net': r['is']['net_pips'],
            'is_dd': r['is']['max_dd_pips'],
            'oos_pf': r['oos']['pf'], 'oos_n': r['oos']['n'], 'oos_net': r['oos']['net_pips'],
            'oos_dd': r['oos']['max_dd_pips'], 'oos_wr': r['oos']['wr'],
            'oos_avglot': r['oos']['avg_lot'], 'wfo_min': r['wfo_min'],
        })
    return rows


# ----------------------------------------------------------------------------
# パラメータスイープ
# ----------------------------------------------------------------------------
def sweep_run(dfs, args):
    n_grid = [20, 40, 80]
    zin_grid = [2.0, 2.5]
    ztp_grid = [0.0, 1.0]            # MA回帰 / 反対1σ手前
    zstop_grid = [4.0]
    hold_grid = [24, 48]
    rows = []
    for pair, (df, pip, cost) in dfs.items():
        for nn in n_grid:
            ind = add_indicators(df, nn, args.atr_n, args.atr_lookback,
                                 args.ema_span, args.rsi_n)
            for z_in in zin_grid:
                for z_tp in ztp_grid:
                    for z_stop in zstop_grid:
                        for hold in hold_grid:
                            cfg = base_cfg(args)
                            cfg.update(n=nn, z_in=z_in, z_tp=z_tp, z_stop=z_stop, max_hold=hold)
                            results = {m: eval_mode(ind, pip, cost, cfg, m)
                                       for m in ['fixed', 'zscale', 'voladj', 'combo']}
                            rows.extend(collect_rows(pair, results, cfg))
    res = pd.DataFrame(rows)
    out = os.path.join(HERE, 'dynamic_lot_mr_bt_sweep_result.csv')
    res.to_csv(out, index=False)
    print(f'[csv] {out} ({len(res)} 行)')
    # 固定 vs combo の OOS PF/DD 改善が最大の構成
    print('\n' + '=' * 104)
    print('スイープ要約: 各(pair,N,z_in,z_tp,hold)で combo が固定比で OOS PF/DD をどう変えたか')
    print('=' * 104)
    piv = res.pivot_table(index=['pair', 'n', 'z_in', 'z_tp', 'max_hold'],
                          columns='sizing_mode', values=['oos_pf', 'oos_dd'])
    # combo - fixed の dPF を計算して降順
    delta = []
    for key, g in res.groupby(['pair', 'n', 'z_in', 'z_tp', 'max_hold']):
        gm = g.set_index('sizing_mode')
        if 'fixed' in gm.index and 'combo' in gm.index:
            f, cmb = gm.loc['fixed'], gm.loc['combo']
            delta.append({**dict(zip(['pair', 'n', 'z_in', 'z_tp', 'max_hold'], key)),
                          'fixed_oos_pf': f['oos_pf'], 'combo_oos_pf': cmb['oos_pf'],
                          'dPF': cmb['oos_pf'] - f['oos_pf'],
                          'fixed_oos_dd': f['oos_dd'], 'combo_oos_dd': cmb['oos_dd'],
                          'dd_chg%': (cmb['oos_dd'] / f['oos_dd'] - 1) * 100 if f['oos_dd'] else np.nan,
                          'combo_wfo_min': cmb['wfo_min'], 'oos_n': cmb['oos_n']})
    dd = pd.DataFrame(delta).sort_values('dPF', ascending=False)
    print(dd.to_string(index=False, float_format=lambda x: f'{x:.2f}'))
    return res


def filter_sweep_run(dfs, args):
    """反転確認フィルタ(none/ema/candle/rsi) x (N, z_in, max_hold) を固定ロットで探索。"""
    n_grid = [20, 40, 80]
    zin_grid = [2.0, 2.5]
    hold_grid = [12, 24, 48]
    rows = []
    for pair, (df, pip, cost) in dfs.items():
        for nn in n_grid:
            ind = add_indicators(df, nn, args.atr_n, args.atr_lookback,
                                 args.ema_span, args.rsi_n)
            for z_in in zin_grid:
                for hold in hold_grid:
                    cfg = base_cfg(args)
                    cfg.update(n=nn, z_in=z_in, max_hold=hold)
                    rows.extend(report_filters(pair, ind, pip, cost, cfg))
                    print()
    res = pd.DataFrame(rows)
    out = os.path.join(HERE, 'dynamic_lot_mr_filter_sweep_result.csv')
    res.to_csv(out, index=False)
    print(f'[csv] {out} ({len(res)} 行)')
    # フィルタ別: 「IS-selectable(IS_PF>1) ∧ OOS_PF が none 比で改善」した構成数
    print('\n' + '=' * 100)
    print('フィルタ探索要約: 各フィルタが baseline(none) の OOS PF を上回った構成の割合')
    print('=' * 100)
    for m in ['ema', 'candle', 'rsi']:
        sub = res[res['confirm'] == m]
        base = res[res['confirm'] == 'none'].set_index(['pair', 'n', 'z_in', 'max_hold'])
        better = sel = 0
        dpf_sum = 0.0
        cnt = 0
        for _, r in sub.iterrows():
            key = (r['pair'], r['n'], r['z_in'], r['max_hold'])
            if key in base.index:
                b = base.loc[key]
                cnt += 1
                dpf_sum += r['oos_pf'] - b['oos_pf']
                if r['oos_pf'] > b['oos_pf']:
                    better += 1
                if r['is_pf'] > 1.0 and r['oos_pf'] > b['oos_pf'] and r['oos_pf'] > 1.2:
                    sel += 1
        print(f"  {m:7s}: OOS PF改善 {better}/{cnt} 構成  平均dPF={dpf_sum/max(cnt,1):+.3f}  "
              f"IS-selectable∧改善∧OOS>1.2 = {sel}")
    return res


def htf_sweep_run(dfs, args):
    """Phase5: HTFフィルタ(adx/slope) x (N, z_in) x 閾値 を固定ロットで探索。"""
    n_grid = [40, 80]
    zin_grid = [2.0, 2.5]
    adx_grid = [20.0, 25.0, 30.0]
    slope_grid = [0.5, 1.0, 2.0]
    rows = []
    for pair, (df, pip, cost) in dfs.items():
        for nn in n_grid:
            ind = add_indicators(df, nn, args.atr_n, args.atr_lookback,
                                 args.ema_span, args.rsi_n, args.htf_tf,
                                 args.htf_adx_n, args.htf_slope_ma, args.htf_slope_lb)
            for z_in in zin_grid:
                base_cfg_ = base_cfg(args)
                base_cfg_.update(n=nn, z_in=z_in, htf_filter='none')
                rbase = eval_runner(ind, pip, cost, base_cfg_, run_bt)['oos']
                for am in adx_grid:
                    c = base_cfg(args)
                    c.update(n=nn, z_in=z_in, htf_filter='adx', adx_max=am)
                    r = eval_runner(ind, pip, cost, c, run_bt)
                    rows.append(_htf_row(pair, 'adx', am, nn, z_in, r, rbase))
                for sm in slope_grid:
                    c = base_cfg(args)
                    c.update(n=nn, z_in=z_in, htf_filter='slope', slope_max=sm)
                    r = eval_runner(ind, pip, cost, c, run_bt)
                    rows.append(_htf_row(pair, 'slope', sm, nn, z_in, r, rbase))
    res = pd.DataFrame(rows)
    out = os.path.join(HERE, 'dynamic_lot_mr_htf_sweep_result.csv')
    res.to_csv(out, index=False)
    print(f'[csv] {out} ({len(res)} 行)')
    print('\n' + '=' * 100)
    print('HTFフィルタ探索要約: baseline(none) の OOS PF を上回った構成')
    print('=' * 100)
    for mode in ['adx', 'slope']:
        sub = res[res['htf_mode'] == mode]
        better = (sub['d_oos_pf'] > 0).sum()
        sel = ((sub['is_pf'] > 1.0) & (sub['d_oos_pf'] > 0) & (sub['oos_pf'] > 1.2)).sum()
        print(f"  {mode:6s}: OOS PF改善 {better}/{len(sub)} 構成  "
              f"平均dPF={sub['d_oos_pf'].mean():+.3f}  "
              f"IS-selectable∧改善∧OOS>1.2 = {sel}")
    top = res[np.isfinite(res['oos_pf'])].sort_values('d_oos_pf', ascending=False).head(12)
    print('\n  dPF 上位:')
    print(top[['pair', 'htf_mode', 'threshold', 'n', 'z_in', 'is_pf', 'oos_pf',
               'd_oos_pf', 'n_retained', 'oos_dd', 'wfo_min']].to_string(
        index=False, float_format=lambda x: f'{x:.2f}'))
    return res


def _htf_row(pair, mode, thr, nn, z_in, r, rbase):
    mi, mo = r['is'], r['oos']
    return {
        'pair': pair, 'htf_mode': mode, 'threshold': thr, 'n': nn, 'z_in': z_in,
        'is_pf': mi['pf'], 'is_n': mi['n'], 'oos_pf': mo['pf'], 'oos_n': mo['n'],
        'oos_wr': mo['wr'], 'oos_dd': mo['max_dd_pips'], 'wfo_min': r['wfo_min'],
        'd_oos_pf': mo['pf'] - rbase['pf'],
        'n_retained': mo['n'] / rbase['n'] if rbase['n'] else float('nan'),
    }


# ----------------------------------------------------------------------------
def load_tf(pair, tf):
    """data/<pair>_<tf> を読み込む。無い tf (例 4h) は 1h から resample で生成。

    4h バーは確定済み 1h バーのみで構成 (open=first/high=max/low=min/close=last) するため
    lookahead は発生しない。
    """
    raw = LS.load_data(pair, tf)
    if raw is not None:
        return raw
    if tf in ('4h', '2h', '8h', '1D', '1d'):
        base = LS.load_data(pair, '1h')
        if base is None:
            return None
        rule = '1D' if tf in ('1d', '1D') else tf
        agg = base.resample(rule).agg({'open': 'first', 'high': 'max',
                                       'low': 'min', 'close': 'last'}).dropna()
        return agg
    return None


def load_pairs(pairs, tf, args):
    dfs = {}
    for pair in pairs:
        raw = load_tf(pair, tf)
        if raw is None or len(raw) < 1000:
            print(f'[warn] {pair}: データ未取得/不足のためスキップ (tf={tf})')
            continue
        meta = LS.PAIR_META.get(pair, {'pip': LS.DEFAULT_PIP, 'cost_pips': LS.DEFAULT_COST_PIPS})
        df = raw[['open', 'high', 'low', 'close']].copy()
        dfs[pair] = (df, meta['pip'], meta['cost_pips'])
    return dfs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='1h', help='時間足 (1h/15m)')
    ap.add_argument('--pairs', nargs='+', default=['AUDCAD', 'EURGBP'])
    # 平均回帰パラメータ
    ap.add_argument('--n', type=int, default=40, help='MA/SD 期間')
    ap.add_argument('--z-in', dest='z_in', type=float, default=2.0)
    ap.add_argument('--z-tp', dest='z_tp', type=float, default=0.0, help='利確Z (0=MA回帰)')
    ap.add_argument('--z-stop', dest='z_stop', type=float, default=4.0, help='ハードストップZ')
    ap.add_argument('--max-hold', dest='max_hold', type=int, default=48, help='タイムストップ(本)')
    # 動的ロット
    ap.add_argument('--zk', type=float, default=1.0, help='Zスケール係数 (z_in+0.5で+0.5lot)')
    ap.add_argument('--max-lot', dest='max_lot', type=float, default=3.0)
    ap.add_argument('--squeeze-lo', dest='squeeze_lo', type=float, default=0.20)
    ap.add_argument('--squeeze-mult', dest='squeeze_mult', type=float, default=0.5)
    ap.add_argument('--vol-hi', dest='vol_hi', type=float, default=0.90)
    ap.add_argument('--vol-hi-mult', dest='vol_hi_mult', type=float, default=0.5)
    # ATR
    ap.add_argument('--atr-n', dest='atr_n', type=int, default=14)
    ap.add_argument('--atr-lookback', dest='atr_lookback', type=int, default=500)
    # 反転確認フィルタ (Phase 3)
    ap.add_argument('--ema-span', dest='ema_span', type=int, default=5,
                    help='パターンA: 短期EMA span')
    ap.add_argument('--rsi-n', dest='rsi_n', type=int, default=7, help='パターンC: 短期RSI 期間')
    ap.add_argument('--rsi-os', dest='rsi_os', type=float, default=30.0)
    ap.add_argument('--rsi-ob', dest='rsi_ob', type=float, default=70.0)
    ap.add_argument('--confirm-window', dest='confirm_window', type=int, default=6,
                    help='乖離成立後、反転トリガを待つ最大本数')
    ap.add_argument('--confirm', default=None, choices=['none', 'ema', 'candle', 'rsi'],
                    help='--dump-trades 出力に使う確認モード (既定=none)')
    # HTFレジームフィルタ (Phase5)
    ap.add_argument('--htf-tf', dest='htf_tf', default='4h')
    ap.add_argument('--htf-adx-n', dest='htf_adx_n', type=int, default=14)
    ap.add_argument('--adx-max', dest='adx_max', type=float, default=25.0,
                    help='HTF ADX がこの値未満(レンジ)ならエントリー許可')
    ap.add_argument('--htf-slope-ma', dest='htf_slope_ma', type=int, default=50)
    ap.add_argument('--htf-slope-lb', dest='htf_slope_lb', type=int, default=10)
    ap.add_argument('--slope-max', dest='slope_max', type=float, default=1.0,
                    help='HTF SMA傾き(ATR単位)の絶対値がこの値以下(フラット)なら許可')
    # 層化エントリー (Phase6)
    ap.add_argument('--z-in2', dest='z_in2', type=float, default=2.5,
                    help='第2段エントリーの乖離閾値')
    ap.add_argument('--tier-lot', dest='tier_lot', type=float, default=0.5,
                    help='各段のロット(均等分割, 非マーチン)')
    # 3段・不等分割 (Phase7)
    ap.add_argument('--tier3-zs', dest='tier3_zs', type=float, nargs=3,
                    default=[2.0, 2.5, 3.0], help='3段の乖離閾値 Z')
    ap.add_argument('--tier3-lots', dest='tier3_lots', type=float, nargs=3,
                    default=[0.2, 0.3, 0.5], help='3段の不等ロット(合計=最大exposure)')
    ap.add_argument('--partial-z', dest='partial_z', type=float, default=1.5,
                    help='決済B: Tier3 を部分利確する Z 水準')
    ap.add_argument('--tier3-z-stop', dest='tier3_z_stop', type=float, default=4.5,
                    help='Phase7 のハードストップ Z (既定 4.5)')
    # モード
    ap.add_argument('--sizing-report', action='store_true',
                    help='Phase2 の動的ロット比較を出力 (既定は Phase3 フィルタ比較)')
    ap.add_argument('--htf-report', dest='htf_report', action='store_true',
                    help='Phase5 HTFレジームフィルタ比較を出力')
    ap.add_argument('--tier-report', dest='tier_report', action='store_true',
                    help='Phase6 層化エントリー比較を出力')
    ap.add_argument('--tier3-report', dest='tier3_report', action='store_true',
                    help='Phase7 3段不等分割(決済A/B)比較を出力')
    ap.add_argument('--sweep', action='store_true', help='動的ロットのパラメータ探索 (Phase2)')
    ap.add_argument('--filter-sweep', dest='filter_sweep', action='store_true',
                    help='反転確認フィルタ x (N,z_in,hold) を探索 (Phase3)')
    ap.add_argument('--htf-sweep', dest='htf_sweep', action='store_true',
                    help='HTFフィルタ x (N,z_in,閾値) を探索 (Phase5)')
    ap.add_argument('--dump-trades', action='store_true',
                    help='トレードログを CSV 出力 (Phase4 分布解析/MFE-MAE へ流用)')
    args = ap.parse_args()

    dfs = load_pairs(args.pairs, args.tf, args)
    if not dfs:
        print('[error] 使用可能なデータがありません。')
        return

    if args.sweep:
        sweep_run(dfs, args)
        return
    if args.filter_sweep:
        filter_sweep_run(dfs, args)
        return
    if args.htf_sweep:
        htf_sweep_run(dfs, args)
        return

    def build(df):
        return add_indicators(df, args.n, args.atr_n, args.atr_lookback,
                              args.ema_span, args.rsi_n, args.htf_tf,
                              args.htf_adx_n, args.htf_slope_ma, args.htf_slope_lb)

    all_rows = []
    out_name = 'dynamic_lot_mr_filter_result.csv'
    for pair, (df, pip, cost) in dfs.items():
        ind = build(df)
        cfg = base_cfg(args)
        if args.sizing_report:
            results = report_pair(pair, ind, pip, cost, cfg)
            all_rows.extend(collect_rows(pair, results, cfg))
            out_name = 'dynamic_lot_mr_bt_result.csv'
        elif args.htf_report:
            all_rows.extend(report_htf(pair, ind, pip, cost, cfg))
            out_name = 'dynamic_lot_mr_htf_result.csv'
            print()
        elif args.tier_report:
            all_rows.extend(report_tier(pair, ind, pip, cost, cfg))
            out_name = 'dynamic_lot_mr_tier_result.csv'
            print()
        elif args.tier3_report:
            cfg['z_stop'] = args.tier3_z_stop      # Phase7 は |Z|>=4.5 ハードストップ
            all_rows.extend(report_tier3(pair, ind, pip, cost, cfg))
            out_name = 'dynamic_lot_mr_tier3_result.csv'
            print()
        else:
            all_rows.extend(report_filters(pair, ind, pip, cost, cfg))
            print()
        if args.dump_trades:
            c = dict(cfg)
            c['confirm_mode'] = args.confirm or 'none'
            c['sizing_mode'] = 'fixed'
            _, trades = run_bt(ind, pip, cost, c)
            for t in trades:
                t['pair'] = pair
            tag = c['confirm_mode']
            tout = os.path.join(HERE, f'dynamic_lot_mr_trades_{pair}_{args.tf}_{tag}.csv')
            pd.DataFrame(trades).to_csv(tout, index=False)
            print(f'  [trades] {tout} ({len(trades)} 行, confirm={tag})')

    res = pd.DataFrame(all_rows)
    out = os.path.join(HERE, out_name)
    res.to_csv(out, index=False)
    print(f'\n[csv] {out} ({len(res)} 行)')


if __name__ == '__main__':
    main()
