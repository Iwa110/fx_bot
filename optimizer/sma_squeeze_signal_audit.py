"""
sma_squeeze_signal_audit.py - USDJPY/EURUSD シグナル頻度調査
SMA Squeeze v4.3 パラメータでエントリー条件ごとの脱落数を計測し、
squeeze_th スイープで感度分析を行う。
"""

import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = Path(_VPS_DATA_DIR) if os.path.isdir(_VPS_DATA_DIR) else Path(__file__).parent.parent / 'data'

# v4.3 実稼働パラメータ（有効ペアのみ）
PAIRS_CFG = {
    'USDJPY': {
        'sma_short': 25, 'sma_long': 150, 'squeeze_th': 2.0,
        'slope_period': 5, 'rr': 2.5, 'sl_atr_mult': 1.5,
        'timeframe': '4h',
        'daily_sma': 20, 'daily_slope_period': 3,
    },
    'EURUSD': {
        'sma_short': 25, 'sma_long': 200, 'squeeze_th': 2.0,
        'slope_period': 10, 'rr': 2.5, 'sl_atr_mult': 1.0,
        'timeframe': '4h',
        'daily_sma': 50, 'daily_slope_period': 3,
    },
}

RECENT_BARS_3M = 540   # 4h * 24 / 4 * 90 = 540


def load_1h(pair):
    candidates = [
        DATA_DIR / f'{pair}_1h.csv',
        DATA_DIR / f'{pair.lower()}_1h.csv',
        DATA_DIR / f'{pair}_H1.csv',
    ]
    for path in candidates:
        if not path.exists():
            continue
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index)
        try:
            df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass
        df.columns = [c.lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        keep = [c for c in ['open', 'high', 'low', 'close'] if c in df.columns]
        df = df[keep]
        return df.dropna(subset=['close']).sort_index()
    return None


def resample_4h(df_1h):
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    return df_1h.resample('4h').agg(agg).dropna(subset=['close'])


def calc_atr14(df):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def calc_adx14(df):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s    = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()


def make_daily_slope(df_1h, daily_sma, daily_slope_period):
    """日足SMA単調スロープを1h indexにアライン（前方充填）"""
    daily = df_1h['close'].resample('D').last().dropna()
    sma_d = daily.rolling(daily_sma).mean()
    slope_ok = {}
    for dt in sma_d.index[daily_sma + daily_slope_period:]:
        slp = sma_d.loc[:dt].iloc[-(daily_slope_period + 1):]
        if len(slp) < daily_slope_period + 1:
            continue
        d = np.diff(slp.values)
        slope_ok[dt.normalize()] = bool(np.all(d > 0) or np.all(d < 0))
    s = pd.Series(slope_ok)
    s.index = pd.to_datetime(s.index)
    return s


def run_audit(pair, cfg, df_4h, df_1h, squeeze_th_override=None):
    """
    エントリー条件ごとの脱落数をカウントしてBT実行。
    戻り値: (counters_dict, trades_list)
    """
    sma_short    = cfg['sma_short']
    sma_long     = cfg['sma_long']
    squeeze_th   = squeeze_th_override if squeeze_th_override is not None else cfg['squeeze_th']
    slope_period = cfg['slope_period']
    rr           = cfg['rr']
    sl_atr_mult  = cfg['sl_atr_mult']
    daily_sma    = cfg.get('daily_sma', 20)
    daily_slope_period = cfg.get('daily_slope_period', 3)

    df = df_4h.reset_index(drop=False)
    if 'index' in df.columns:
        df = df.rename(columns={'index': 'datetime'})
    elif df.columns[0] != 'datetime':
        df.insert(0, 'datetime', df_4h.index)

    close_s = df['close']
    sma_s_v  = close_s.rolling(sma_short).mean().values
    sma_l_v  = close_s.rolling(sma_long).mean().values
    atr14    = calc_atr14(df).values
    adx14    = calc_adx14(df).values
    close_a  = close_s.values
    open_a   = df['open'].values
    n        = len(df)

    daily_slope = make_daily_slope(df_1h, daily_sma, daily_slope_period)

    warmup = max(sma_long, slope_period, 28) + 2

    cnt = {
        'total_bars':     0,
        'in_trade':       0,
        'nan_skip':       0,
        'adx_fail':       0,
        'squeeze_fail':   0,
        'slope_fail':     0,
        'daily_slope_fail': 0,
        'no_signal':      0,
        'entry':          0,
    }
    trades   = []
    equity   = [0.0]
    in_trade = False
    t_dir = t_entry = t_sl = t_tp = 0.0

    for i in range(warmup, n):
        cnt['total_bars'] += 1
        c     = close_a[i]
        o     = open_a[i]
        sl_v  = sma_l_v[i]
        ss_v  = sma_s_v[i]
        atr_v = atr14[i]
        adx_v = adx14[i]

        if in_trade:
            force = (t_dir == 'long' and c < sl_v) or (t_dir == 'short' and c > sl_v)
            if force:
                pnl = (c - t_entry) if t_dir == 'long' else (t_entry - c)
                trades.append(('force', pnl))
                equity.append(equity[-1] + pnl)
                in_trade = False
            elif t_dir == 'long':
                if c <= t_entry - t_sl:
                    trades.append(('sl', -t_sl))
                    equity.append(equity[-1] - t_sl)
                    in_trade = False
                elif c >= t_entry + t_tp:
                    trades.append(('tp', t_tp))
                    equity.append(equity[-1] + t_tp)
                    in_trade = False
            else:
                if c >= t_entry + t_sl:
                    trades.append(('sl', -t_sl))
                    equity.append(equity[-1] - t_sl)
                    in_trade = False
                elif c <= t_entry - t_tp:
                    trades.append(('tp', t_tp))
                    equity.append(equity[-1] + t_tp)
                    in_trade = False

        if in_trade:
            cnt['in_trade'] += 1
            continue

        if np.isnan(sl_v) or np.isnan(ss_v) or np.isnan(atr_v) or np.isnan(adx_v):
            cnt['nan_skip'] += 1
            continue
        if sl_v == 0.0:
            cnt['nan_skip'] += 1
            continue

        if adx_v <= 20.0:
            cnt['adx_fail'] += 1
            continue

        div_rate = abs(ss_v - sl_v) / sl_v * 100.0
        if div_rate > squeeze_th:
            cnt['squeeze_fail'] += 1
            continue

        slp_start = i - slope_period + 1
        if slp_start < 0:
            cnt['slope_fail'] += 1
            continue
        slp   = sma_l_v[slp_start: i + 1]
        if np.any(np.isnan(slp)):
            cnt['slope_fail'] += 1
            continue
        diffs   = np.diff(slp)
        rising  = bool(np.all(diffs > 0))
        falling = bool(np.all(diffs < 0))
        if not (rising or falling):
            cnt['slope_fail'] += 1
            continue

        # 日足フィルター
        if len(daily_slope) > 0 and 'datetime' in df.columns:
            bar_date = pd.Timestamp(df['datetime'].iloc[i]).normalize()
            ds_val = daily_slope.get(bar_date, None)
            if ds_val is not None:
                if rising and ds_val is False:
                    cnt['daily_slope_fail'] += 1
                    continue
                if falling and ds_val is True:
                    cnt['daily_slope_fail'] += 1
                    continue

        prev_c = close_a[i - 1]
        prev_s = sma_s_v[i - 1]
        if np.isnan(prev_s) or np.isnan(prev_c):
            cnt['nan_skip'] += 1
            continue

        sl_dist = atr_v * sl_atr_mult
        tp_dist = sl_dist * rr
        if sl_dist == 0.0:
            cnt['nan_skip'] += 1
            continue

        if rising and c > sl_v and prev_c < prev_s and c > ss_v and c > o:
            t_dir = 'long'; t_entry = c; t_sl = sl_dist; t_tp = tp_dist
            in_trade = True
            cnt['entry'] += 1
        elif falling and c < sl_v and prev_c > prev_s and c < ss_v and c < o:
            t_dir = 'short'; t_entry = c; t_sl = sl_dist; t_tp = tp_dist
            in_trade = True
            cnt['entry'] += 1
        else:
            cnt['no_signal'] += 1

    if in_trade:
        pnl = (close_a[-1] - t_entry) if t_dir == 'long' else (t_entry - close_a[-1])
        trades.append(('open', pnl))

    return cnt, trades


def calc_metrics(trades):
    if not trades:
        return {'pf': 0.0, 'win_rate': 0.0, 'n': 0}
    pnl_arr = np.array([p for _, p in trades])
    wins = pnl_arr[pnl_arr > 0]
    loss = pnl_arr[pnl_arr <= 0]
    gp = float(wins.sum()) if len(wins) > 0 else 0.0
    gl = float(abs(loss.sum())) if len(loss) > 0 else 0.0
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    return {
        'pf':       round(pf, 3),
        'win_rate': round(len(wins) / len(pnl_arr) * 100, 1),
        'n':        len(pnl_arr),
    }


def months_in_df(df):
    dates = df.index if hasattr(df.index, 'year') else pd.to_datetime(df.index)
    if len(dates) == 0:
        return 1
    span = (dates[-1] - dates[0]).days / 30.0
    return max(span, 1.0)


# ────────────────────────────────────────
# Main
# ────────────────────────────────────────
print('=' * 80)
print('SMA Squeeze v4.3 シグナル頻度調査 (USDJPY / EURUSD)')
print('=' * 80)

for pair, cfg in PAIRS_CFG.items():
    print(f'\n{"="*70}')
    print(f'  {pair}  (tf={cfg["timeframe"]}, squeeze_th={cfg["squeeze_th"]}, '
          f'sma_short={cfg["sma_short"]}, sma_long={cfg["sma_long"]})')
    print(f'{"="*70}')

    df_1h = load_1h(pair)
    if df_1h is None:
        print(f'  [ERROR] 1hデータが見つかりません: {pair}')
        continue

    df_4h_full = resample_4h(df_1h)
    df_4h_rec  = df_4h_full.tail(RECENT_BARS_3M)

    total_months  = months_in_df(df_4h_full)
    recent_months = months_in_df(df_4h_rec)

    # ── 全期間 ──
    cnt_all, trades_all = run_audit(pair, cfg, df_4h_full, df_1h)
    m_all = calc_metrics(trades_all)
    avg_mo_all = m_all['n'] / total_months

    print(f'\n  [全期間: {len(df_4h_full)}本 / {total_months:.1f}ヶ月]')
    print(f'    評価対象bars    : {cnt_all["total_bars"]}')
    print(f'    保有中スキップ  : {cnt_all["in_trade"]}')
    print(f'    NaN/ゼロスキップ: {cnt_all["nan_skip"]}')
    print(f'    ADX<=20 脱落    : {cnt_all["adx_fail"]}')
    print(f'    squeeze_th脱落  : {cnt_all["squeeze_fail"]}')
    print(f'    SMAスロープ脱落 : {cnt_all["slope_fail"]}')
    print(f'    日足フィルター脱落: {cnt_all["daily_slope_fail"]}')
    print(f'    シグナルなし    : {cnt_all["no_signal"]}')
    print(f'    エントリー      : {cnt_all["entry"]}  (月平均 {avg_mo_all:.2f}件)')
    print(f'    PF={m_all["pf"]}  WR={m_all["win_rate"]}%  n={m_all["n"]}')

    # ── 直近3ヶ月 ──
    cnt_rec, trades_rec = run_audit(pair, cfg, df_4h_rec, df_1h)
    m_rec = calc_metrics(trades_rec)
    avg_mo_rec = m_rec['n'] / recent_months

    print(f'\n  [直近3ヶ月: {len(df_4h_rec)}本 / {recent_months:.1f}ヶ月]')
    print(f'    ADX<=20 脱落    : {cnt_rec["adx_fail"]}')
    print(f'    squeeze_th脱落  : {cnt_rec["squeeze_fail"]}')
    print(f'    SMAスロープ脱落 : {cnt_rec["slope_fail"]}')
    print(f'    日足フィルター脱落: {cnt_rec["daily_slope_fail"]}')
    print(f'    エントリー      : {cnt_rec["entry"]}  (月平均 {avg_mo_rec:.2f}件)')
    print(f'    PF={m_rec["pf"]}  WR={m_rec["win_rate"]}%  n={m_rec["n"]}')

    if avg_mo_rec < 1.0:
        print(f'\n  ⚠ シグナル稀 (直近3M月平均={avg_mo_rec:.2f}件): squeeze_th緩和を検討')

    # ── squeeze_th 感度分析 ──
    TH_SWEEP = [0.5, 1.0, 1.5, 2.0, 3.0]
    print(f'\n  [squeeze_th 感度分析 (全期間)]')
    print(f'  {"th":>5}  {"n":>5}  {"月平均":>7}  {"PF":>6}  {"WR":>6}')
    for th in TH_SWEEP:
        _, t = run_audit(pair, cfg, df_4h_full, df_1h, squeeze_th_override=th)
        m    = calc_metrics(t)
        avg  = m['n'] / total_months
        marker = ' ← 現行' if th == cfg['squeeze_th'] else ''
        print(f'  {th:>5.1f}  {m["n"]:>5}  {avg:>7.2f}  {m["pf"]:>6.3f}  {m["win_rate"]:>6.1f}%{marker}')

# ── 推奨アクション ──
print('\n' + '=' * 80)
print('【判断基準】')
print('  月平均 < 1件/月 → パラメータ過剰フィルター → squeeze_th 1.5に緩和を検討')
print('  月平均 ≥ 2件/月 → ブローカー側の問題（MT5履歴同期漏れ）を疑う')
print('=' * 80)
