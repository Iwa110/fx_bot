"""
grid_dynamic_bt.py - 状態適応型(動的)Gridエンジン。

grid_floatstop_bt.run_backtest を1:1で踏襲しつつ、パラメータ
(atr_mult / ci_threshold / max_levels / float_stop / lot)を
**バー毎に可変**にする。全パラメータは t-1 までの特徴量から算出した
凍結マッピングで決まる(=ルックアヘッド厳禁)。

検証タスク: 「10年同一パラメータ」を状態適応に置き換えて静的最良(AUDCAD atr1.5)を
risk-adjusted で上回れるか。本ファイルはエンジンのみ。評価は grid_dynamic_eval.py。

設計上の忠実性:
  - float-stop = intrabar adverse extreme 検知(ギャップ貫通許容), next-bar fill 相当(close約定)
  - TP価格はエントリー時の gw で固定(後からatr_multが変わっても既存TPは不変=実機同様)
  - lot はポジション毎に保持(エントリー時のlotで決済)。float-stop集計も per-position lot。
  - param配列は df 行と整列した numpy 配列。すべて t-1 特徴量由来で呼び出し側が凍結。

constant param で呼べば G.run_backtest と完全一致する(grid_dynamic_eval.py に整合テストあり)。
"""

import numpy as np
import pandas as pd

import grid_floatstop_bt as G

CONTRACT = G.CONTRACT


def run_backtest_dynamic(pair, df, atr_series, ci_series,
                         atr_mult_arr, ci_th_arr, maxlv_arr, fs_arr, lot_arr, quote_jpy,
                         b48_hours=48):
    """動的パラメータ Grid BT。
    *_arr は len(df) と同じ長さの numpy 配列(各バーで適用する値, t-1 由来で凍結)。
    返り値は G.run_backtest と同一スキーマ。"""
    qj = quote_jpy

    def pjpy(price_diff, lot):
        return price_diff * lot * CONTRACT * qj

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None

    tp_pnls, b48_pnls, b48_pos_pnls = [], [], []
    fs_pnls, fs_pos_pnls = [], []
    skip_count = 0

    realized_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    worst_event = 0.0
    monthly = {}

    def add_month(ts, v):
        k = ts.strftime('%Y-%m')
        monthly[k] = monthly.get(k, 0.0) + v

    idx = df.index
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()
    atr_vals = atr_series.reindex(idx).to_numpy()
    ci_vals = ci_series.reindex(idx).to_numpy()

    for i in range(len(df)):
        atr = atr_vals[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]
        atr_mult = atr_mult_arr[i]
        ci_threshold = ci_th_arr[i]
        max_levels = int(maxlv_arr[i])
        float_stop = fs_arr[i]
        cur_lot = lot_arr[i]
        ci = ci_vals[i]

        gw = atr * atr_mult
        bar_h, bar_l, bar_cl = highs[i], lows[i], closes[i]

        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # ── TP check ──
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            pnl = pjpy(p['tp'] - p['entry'], p['lot'])
            tp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
            long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            pnl = pjpy(p['entry'] - p['tp'], p['lot'])
            tp_pnls.append(pnl); realized_pnl += pnl; add_month(ts, pnl)
            short_pos.remove(p)

        # ── FLOAT STOP (intrabar adverse extreme), per-position lot ──
        if long_pos:
            unreal = sum(pjpy(bar_l - p['entry'], p['lot']) for p in long_pos)
            if unreal <= float_stop:
                pos_pnls = [pjpy(bar_l - p['entry'], p['lot']) for p in long_pos]
                ev = sum(pos_pnls)
                fs_pos_pnls.extend(pos_pnls); fs_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                long_pos = []; b48_long_start = None
        if short_pos:
            unreal = sum(pjpy(p['entry'] - bar_h, p['lot']) for p in short_pos)
            if unreal <= float_stop:
                pos_pnls = [pjpy(p['entry'] - bar_h, p['lot']) for p in short_pos]
                ev = sum(pos_pnls)
                fs_pos_pnls.extend(pos_pnls); fs_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                short_pos = []; b48_short_start = None

        # ── B48 timer reset ──
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # ── B48 expiry ──
        if b48_long_start is not None:
            if (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
                pos_pnls = [pjpy(bar_cl - p['entry'], p['lot']) for p in long_pos]
                ev = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls); b48_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                long_pos = []; b48_long_start = None
        if b48_short_start is not None:
            if (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
                pos_pnls = [pjpy(p['entry'] - bar_cl, p['lot']) for p in short_pos]
                ev = sum(pos_pnls)
                b48_pos_pnls.extend(pos_pnls); b48_pnls.append(ev)
                realized_pnl += ev; add_month(ts, ev)
                worst_event = min(worst_event, ev)
                short_pos = []; b48_short_start = None

        # ── DD tracking ──
        peak_pnl = max(peak_pnl, realized_pnl)
        max_dd = max(max_dd, peak_pnl - realized_pnl)

        # ── New entries ──
        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        if len(long_pos) == 0:
            if ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw, 'lot': cur_lot})
                if len(long_pos) == max_levels: b48_long_start = ts
        elif len(long_pos) < max_levels:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw, 'lot': cur_lot})
                if len(long_pos) == max_levels: b48_long_start = ts
        else:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                skip_count += 1

        if len(short_pos) == 0:
            if ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw, 'lot': cur_lot})
                if len(short_pos) == max_levels: b48_short_start = ts
        elif len(short_pos) < max_levels:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw, 'lot': cur_lot})
                if len(short_pos) == max_levels: b48_short_start = ts
        else:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                skip_count += 1

    all_pnls = tp_pnls + b48_pos_pnls + fs_pos_pnls
    wins = [p for p in all_pnls if p >= 0]
    losses = [p for p in all_pnls if p < 0]
    gp = sum(wins); gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else float('inf')

    return {
        'pf': round(pf, 4),
        'total_pnl': round(realized_pnl, 0),
        'n_tp': len(tp_pnls),
        'n_b48': len(b48_pnls),
        'b48_total': round(sum(b48_pnls), 0),
        'n_fstop': len(fs_pnls),
        'fstop_total': round(sum(fs_pnls), 0),
        'worst_event': round(worst_event, 0),
        'max_dd': round(max_dd, 0),
        'skip_count': skip_count,
        'monthly': monthly,
        'fs_events': fs_pnls,
        'b48_events': b48_pnls,
    }


# ───────── t-1 安全な状態量(レジーム特徴) ─────────

def atr_regime_prev(df, atr_series, period_price=False):
    """t-1 ATR(strictly previous bar)。realized vol プロキシ。
    price 正規化が必要なら atr/price。返り値: numpy 配列(len=df), 先頭はNaN。"""
    atr_prev = atr_series.reindex(df.index).shift(1)
    if period_price:
        atr_prev = atr_prev / df['close'].shift(1)
    return atr_prev.to_numpy()


def ci_prev(df, ci_series):
    """t-1 CI(D1は既に+1d shift済だが更に1barずらして厳格にt-1化)。"""
    return ci_series.reindex(df.index).shift(1).to_numpy()


def freeze_quantile_thresholds(feature_arr, is_mask, qs):
    """IS区間(is_mask=True)の feature 分布から分位点しきい値を凍結。
    qs: list[float] 例 [0.33,0.66]。返り値: しきい値配列。"""
    vals = feature_arr[is_mask]
    vals = vals[~np.isnan(vals)]
    return np.quantile(vals, qs)


def bucketize(feature_arr, thresholds):
    """feature を thresholds(昇順)で 0..len(thresholds) のバケットに割当。NaN→-1。"""
    out = np.full(len(feature_arr), -1, dtype=int)
    valid = ~np.isnan(feature_arr)
    out[valid] = np.searchsorted(thresholds, feature_arr[valid], side='right')
    return out
