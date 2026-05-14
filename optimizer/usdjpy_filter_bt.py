"""
usdjpy_filter_bt.py - USDJPY フィルター改善グリッドBT
プランB: RSI閾値再調整 + BBバンド幅
プランC: BBバンド幅 + 時間帯フィルター

ベースライン: htf4h_rsi (4h EMA20 + RSI buy<55, sell>45), sl=3.0, tp_rr=1.5
             PF=1.242, WR=45.6%, N=103, MaxDD=219.8pips
採用基準:
# ADOPT:       PF>1.3 かつ N>=80
# CONDITIONAL: PF>1.1 かつ N>=80（要追加検証）
# REJECT:      それ以外
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
_VPS_OPT_DIR = r'C:\Users\Administrator\fx_bot\optimizer'
OPT_DIR = _VPS_OPT_DIR if os.path.isdir(_VPS_OPT_DIR) else str(Path(__file__).parent)

# ===== USDJPY固定設定 =====
SYMBOL          = 'USDJPY'
PIP_UNIT        = 0.01
BB_PERIOD       = 20
BB_SIGMA        = 1.5
RSI_PERIOD      = 14
RSI_BUY_MAX     = 45    # 5m RSI: buy許可上限
RSI_SELL_MIN    = 55    # 5m RSI: sell許可下限
ATR_PERIOD      = 14
HTF_PERIOD      = 20
HTF_SIGMA       = 1.5
HTF_RANGE_SIGMA = 1.0
COOLDOWN_BARS   = 3
SPREAD          = 2 * PIP_UNIT
SL_ATR_MULT     = 3.0
TP_RR           = 1.5
STAGE2_ACTIVATE = 0.70
STAGE2_DISTANCE = 0.30

# ベースライン htf4h_rsi の 4h RSI 閾値
BASE_RSI_BUY_TH  = 55   # 4h RSI: buy許可閾値（これ未満でbuy許可）
BASE_RSI_SELL_TH = 45   # 4h RSI: sell許可閾値（これ超でsell許可）

BASELINE_PF = 1.242
BASELINE_WR = 45.6
BASELINE_N  = 103
BASELINE_DD = 219.8

ADOPT_PF_TH = 1.3
COND_PF_TH  = 1.1
MIN_N       = 80

# ===== フィルターグリッド定義 =====
# B-F1: 4h RSI閾値グリッド (4x4=16通り)
RSI_BUY_TH_GRID  = [45, 50, 55, 60]   # buy許可: 4h RSI < この値
RSI_SELL_TH_GRID = [40, 45, 50, 55]   # sell許可: 4h RSI > この値

# B-F2 / C-F1: BBバンド幅 (3x2=6通り)
BW_RATIO_GRID    = [0.8, 1.0, 1.2]
BW_LOOKBACK_GRID = [20, 30]

# C-F2: 時間帯（UTC） (3通り)
HOUR_SETS = {
    'SET_A': [6, 7, 13, 20, 21, 22],
    'SET_B': [6, 7, 8, 13, 14, 20, 21, 22],
    'SET_C': [6, 7, 8, 9, 13, 14, 15, 20, 21, 22],
}


def log_print(msg):
    print(msg, flush=True)


# ===== データ読み込み =====
def load_csv(symbol, tf='5m'):
    candidates = [
        os.path.join(DATA_DIR, f'{symbol}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol.lower()}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_{tf.upper()}.csv'),
    ]
    if tf == '1h':
        candidates.append(os.path.join(DATA_DIR, f'{symbol}_H1.csv'))
    if tf == '5m':
        candidates.append(os.path.join(DATA_DIR, f'{symbol}_M5.csv'))

    for path in candidates:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df.index.name = 'datetime'
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]]
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.dropna(subset=['close'])
        df = df.sort_index()
        df = df.reset_index()
        return df
    log_print(f'[WARN] CSVなし: {symbol} {tf}')
    return None


# ===== インジケーター =====
def calc_bb(close, period=20, sigma=1.5):
    ma  = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma + sigma * std, ma - sigma * std, ma, std


def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df, period=14):
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ===== ルックアップ構築 =====
def build_htf_lookup(df_1h, period=20, sigma=1.5):
    """1h sigma position ルックアップ"""
    close     = df_1h['close']
    ma        = close.rolling(period).mean()
    std       = close.rolling(period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']


def build_htf4h_ema_lookup(df_1h, ema_period=20):
    """4h EMA20 方向フィルター: +1=Buy許可 / -1=Sell許可"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20']  = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_rsi_4h_lookup(df_1h, period=14):
    """4h RSI ルックアップ"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], period)
    return df4h['rsi']


# ===== MaxDD計算 =====
def compute_max_dd_pips(pnl_list):
    if not pnl_list:
        return 0.0
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for pnl in pnl_list:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return round(max_dd / PIP_UNIT, 1)


# ===== コアシミュレーション =====
def simulate_usdjpy(
    df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
    rsi_buy_th=BASE_RSI_BUY_TH,   # 4h RSI buy閾値（常時ON・B-F1で変化）
    rsi_sell_th=BASE_RSI_SELL_TH, # 4h RSI sell閾値（常時ON・B-F1で変化）
    bw_ratio=None, bw_lookback=None,  # BBwidth追加フィルター（B-F2/C-F1）
    hour_list=None,                   # 時間帯追加フィルター（C-F2）
):
    """
    USDJPYフィルター付きBT（全データ期間）。
    ベースフィルター（常時ON）: 1h HTF sigma + 4h EMA20 + 4h RSI(閾値可変)
    追加フィルター: BBwidth / hour_list
    """
    close = df_5m['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi_5m = calc_rsi(close, RSI_PERIOD)
    atr    = calc_atr(df_5m, ATR_PERIOD)

    bb_width      = bb_u - bb_l
    bb_width_mean = (bb_width.rolling(bw_lookback).mean()
                     if bw_ratio is not None and bw_lookback is not None else None)

    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    gross_profit = gross_loss = 0.0
    pnl_list  = []
    win_pnls  = []
    loss_pnls = []
    last_bar  = -COOLDOWN_BARS - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        c       = close_arr[i]
        sl_dist = atr.iloc[i] * SL_ATR_MULT
        tp_dist = sl_dist * TP_RR
        if sl_dist == 0 or np.isnan(sl_dist) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # C-F2: 時間帯フィルター
        if hour_list is not None and dt.hour not in hour_list:
            continue

        # 1h HTF sigma（常時ON）
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE_SIGMA:
            continue

        # 5m BB + RSI エントリー判定
        rsi_v = rsi_5m.iloc[i]
        if np.isnan(rsi_v):
            continue
        direction = None
        if c <= bb_l.iloc[i] and rsi_v < RSI_BUY_MAX:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > RSI_SELL_MIN:
            direction = 'sell'
        if direction is None:
            continue

        # 4h EMA20（常時ON）
        htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        # 4h RSI（常時ON: ベースライン閾値 or B-F1グリッド閾値）
        rsi4h_idx = rsi_4h_lkp.index.searchsorted(dt, side='right') - 1
        if rsi4h_idx < 0:
            continue
        rsi4h_val = rsi_4h_lkp.iloc[rsi4h_idx]
        if np.isnan(rsi4h_val):
            continue
        if direction == 'buy'  and rsi4h_val >= rsi_buy_th:
            continue
        if direction == 'sell' and rsi4h_val <= rsi_sell_th:
            continue

        # B-F2/C-F1: BBwidthフィルター
        if bw_ratio is not None and bb_width_mean is not None:
            mean_bw = bb_width_mean.iloc[i]
            cur_bw  = bb_u.iloc[i] - bb_l.iloc[i]
            if np.isnan(mean_bw) or cur_bw < mean_bw * bw_ratio:
                continue

        # 決済シミュレーション（Stage2 トレーリングSL）
        entry    = c + SPREAD if direction == 'buy' else c - SPREAD
        tp_price = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl_price = entry - sl_dist if direction == 'buy' else entry + sl_dist

        trail_sl  = sl_price
        activated = False
        hit        = None
        exit_price = None

        for j in range(i + 1, min(i + 300, n)):
            h   = df_5m['high'].iloc[j]
            l   = df_5m['low'].iloc[j]
            mid = (h + l) / 2.0

            if direction == 'buy':
                progress = (mid - entry) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid - tp_dist * STAGE2_DISTANCE
                    if new_trail > trail_sl:
                        trail_sl = new_trail
                if l <= trail_sl:
                    hit = 'trail_sl' if activated else 'sl'
                    exit_price = trail_sl
                    break
                if h >= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break
            else:
                progress = (entry - mid) / tp_dist if tp_dist > 0 else 0
                if progress >= STAGE2_ACTIVATE:
                    activated = True
                if activated:
                    new_trail = mid + tp_dist * STAGE2_DISTANCE
                    if new_trail < trail_sl:
                        trail_sl = new_trail
                if h >= trail_sl:
                    hit = 'trail_sl' if activated else 'sl'
                    exit_price = trail_sl
                    break
                if l <= tp_price:
                    hit = 'tp'
                    exit_price = tp_price
                    break

        if hit is None or exit_price is None:
            continue

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price
        pnl_list.append(pnl)

        if pnl > 0:
            wins += 1
            gross_profit += pnl
            win_pnls.append(pnl)
        else:
            losses += 1
            gross_loss += abs(pnl)
            loss_pnls.append(abs(pnl))

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':   trades,
        'win_rate': round(wins / trades * 100, 1),
        'pf':       round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'max_dd':   compute_max_dd_pips(pnl_list),
        'avg_win':  round(float(np.mean(win_pnls))  / PIP_UNIT, 1) if win_pnls  else 0.0,
        'avg_loss': round(float(np.mean(loss_pnls)) / PIP_UNIT, 1) if loss_pnls else 0.0,
    }


def verdict(pf, n):
    if pf > ADOPT_PF_TH and n >= MIN_N:
        return 'ADOPT'
    if pf > COND_PF_TH and n >= MIN_N:
        return 'CONDITIONAL'
    return 'REJECT'


def _make_row(plan, phase, filter_type, params_str, res, extra=None):
    dpf    = res['pf'] - BASELINE_PF
    v      = verdict(res['pf'], res['trades'])
    n_flag = 'N_insufficient' if res['trades'] < MIN_N else ''
    row = {
        'plan':        plan,
        'phase':       phase,
        'filter_type': filter_type,
        'params':      params_str,
        'PF':          res['pf'],
        'WR':          res['win_rate'],
        'N':           res['trades'],
        'MaxDD':       res['max_dd'],
        'avg_win':     res['avg_win'],
        'avg_loss':    res['avg_loss'],
        'verdict':     v,
        'N_flag':      n_flag,
        'delta_pf':    round(dpf, 3),
    }
    if extra:
        row.update(extra)
    return row, v, n_flag, round(dpf, 3)


# ===== Plan B Phase1 =====
def run_planb_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp):
    rows   = []
    total1 = len(RSI_BUY_TH_GRID) * len(RSI_SELL_TH_GRID)
    total2 = len(BW_RATIO_GRID) * len(BW_LOOKBACK_GRID)

    # B-F1: RSI閾値グリッド (16 runs)
    log_print(f'\n[PlanB-Phase1-F1] RSI 4h閾値グリッド ({total1} runs)')
    cnt = 0
    for rsi_buy_th in RSI_BUY_TH_GRID:
        for rsi_sell_th in RSI_SELL_TH_GRID:
            cnt += 1
            params_str = f'rsi_buy<{rsi_buy_th},rsi_sell>{rsi_sell_th}'
            res = simulate_usdjpy(
                df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
                rsi_buy_th=rsi_buy_th, rsi_sell_th=rsi_sell_th,
            )
            if res is None:
                log_print(f'  [{cnt}/{total1}] {params_str}: N=0')
                continue
            row, v, nf, dpf = _make_row(
                'B', 1, 'B-F1_RSI', params_str, res,
                {'_rsi_buy_th': rsi_buy_th, '_rsi_sell_th': rsi_sell_th},
            )
            rows.append(row)
            log_print(f'  [{cnt}/{total1}] {params_str}: '
                      f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                      f'N={res["trades"]} [{v}]{"  *" if nf else ""}')

    # B-F2: BBwidth (6 runs)
    log_print(f'\n[PlanB-Phase1-F2] BBwidth ({total2} runs)')
    cnt = 0
    for bw_ratio in BW_RATIO_GRID:
        for bw_lookback in BW_LOOKBACK_GRID:
            cnt += 1
            params_str = f'bw_ratio={bw_ratio},bw_lookback={bw_lookback}'
            res = simulate_usdjpy(
                df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
                bw_ratio=bw_ratio, bw_lookback=bw_lookback,
            )
            if res is None:
                log_print(f'  [{cnt}/{total2}] {params_str}: N=0')
                continue
            row, v, nf, dpf = _make_row(
                'B', 1, 'B-F2_BBwidth', params_str, res,
                {'_bw_ratio': bw_ratio, '_bw_lookback': bw_lookback},
            )
            rows.append(row)
            log_print(f'  [{cnt}/{total2}] {params_str}: '
                      f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                      f'N={res["trades"]} [{v}]{"  *" if nf else ""}')

    return rows


# ===== Plan C Phase1 =====
def run_planc_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, bw_cache=None):
    rows = []

    # C-F1: BBwidth (B-F2と同一なのでキャッシュ再利用)
    log_print(f'\n[PlanC-Phase1-F1] BBwidth ({len(BW_RATIO_GRID)*len(BW_LOOKBACK_GRID)} runs) [B-F2結果を再利用]')
    if bw_cache is not None:
        for r in bw_cache:
            if r['filter_type'] != 'B-F2_BBwidth':
                continue
            cr = dict(r)
            cr['plan']        = 'C'
            cr['filter_type'] = 'C-F1_BBwidth'
            rows.append(cr)
            log_print(f'  [cached] {r["params"]}: '
                      f'PF={r["PF"]}({r["delta_pf"]:+.3f}) [{r["verdict"]}]')
    else:
        cnt   = 0
        total = len(BW_RATIO_GRID) * len(BW_LOOKBACK_GRID)
        for bw_ratio in BW_RATIO_GRID:
            for bw_lookback in BW_LOOKBACK_GRID:
                cnt += 1
                params_str = f'bw_ratio={bw_ratio},bw_lookback={bw_lookback}'
                res = simulate_usdjpy(
                    df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
                    bw_ratio=bw_ratio, bw_lookback=bw_lookback,
                )
                if res is None:
                    log_print(f'  [{cnt}/{total}] {params_str}: N=0')
                    continue
                row, v, nf, dpf = _make_row(
                    'C', 1, 'C-F1_BBwidth', params_str, res,
                    {'_bw_ratio': bw_ratio, '_bw_lookback': bw_lookback},
                )
                rows.append(row)
                log_print(f'  [{cnt}/{total}] {params_str}: '
                          f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                          f'N={res["trades"]} [{v}]{"  *" if nf else ""}')

    # C-F2: 時間帯フィルター (3 runs)
    log_print(f'\n[PlanC-Phase1-F2] Hour ({len(HOUR_SETS)} runs)')
    for idx, (set_name, hours) in enumerate(HOUR_SETS.items(), 1):
        params_str = f'{set_name}={hours}'
        res = simulate_usdjpy(
            df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
            hour_list=hours,
        )
        if res is None:
            log_print(f'  [{idx}] {set_name}: N=0')
            continue
        row, v, nf, dpf = _make_row(
            'C', 1, 'C-F2_Hour', params_str, res,
            {'_set_name': set_name, '_hours': hours},
        )
        rows.append(row)
        log_print(f'  [{idx}] {set_name}: '
                  f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                  f'N={res["trades"]} [{v}]{"  *" if nf else ""}')

    return rows


# ===== Plan B Phase2 =====
def run_planb_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, planb_p1):
    f1_rows = [r for r in planb_p1 if r['filter_type'] == 'B-F1_RSI']
    f2_rows = [r for r in planb_p1 if r['filter_type'] == 'B-F2_BBwidth']

    adopt_f1 = [r for r in f1_rows if r['verdict'] == 'ADOPT']
    cond_f1  = [r for r in f1_rows if r['verdict'] == 'CONDITIONAL']

    if adopt_f1:
        best_f1 = sorted(adopt_f1, key=lambda x: x['PF'], reverse=True)[0]
        log_print(f'\n[PlanB-Phase2] B-F1最良(ADOPT): {best_f1["params"]} PF={best_f1["PF"]}')
    elif cond_f1:
        best_f1 = sorted(cond_f1, key=lambda x: x['PF'], reverse=True)[0]
        log_print(f'\n[PlanB-Phase2] B-F1最良(CONDITIONAL): {best_f1["params"]} PF={best_f1["PF"]}')
    elif f1_rows:
        best_f1 = sorted(f1_rows, key=lambda x: x['PF'], reverse=True)[0]
        log_print(f'\n[PlanB-Phase2] B-F1最良(REJECT/全REJECT): {best_f1["params"]} PF={best_f1["PF"]}')
        log_print('  ※ ADOPT/CONDITIONALなし。最高PFのREJECT設定で続行。')
    else:
        log_print('\n[PlanB-Phase2] B-F1結果なし → Phase2スキップ')
        return []

    if not f2_rows:
        log_print('[PlanB-Phase2] B-F2結果なし → Phase2スキップ')
        return []

    log_print(f'[PlanB-Phase2] B-F1({best_f1["params"]}) × B-F2({len(f2_rows)}通り) = {len(f2_rows)} runs')

    rows = []
    for idx, f2 in enumerate(f2_rows, 1):
        params_str = f'{best_f1["params"]} + {f2["params"]}'
        res = simulate_usdjpy(
            df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
            rsi_buy_th=best_f1['_rsi_buy_th'],
            rsi_sell_th=best_f1['_rsi_sell_th'],
            bw_ratio=f2['_bw_ratio'],
            bw_lookback=f2['_bw_lookback'],
        )
        if res is None:
            log_print(f'  [{idx}/{len(f2_rows)}] {params_str}: N=0')
            continue
        dpf    = res['pf'] - BASELINE_PF
        v      = verdict(res['pf'], res['trades'])
        n_flag = 'N_insufficient' if res['trades'] < MIN_N else ''
        rows.append({
            'plan': 'B', 'phase': 2, 'filter_combo': 'B-F1+B-F2',
            'params': params_str,
            'PF': res['pf'], 'WR': res['win_rate'], 'N': res['trades'],
            'MaxDD': res['max_dd'], 'avg_win': res['avg_win'], 'avg_loss': res['avg_loss'],
            'verdict': v, 'N_flag': n_flag, 'delta_pf': round(dpf, 3),
        })
        log_print(f'  [{idx}/{len(f2_rows)}] {params_str}: '
                  f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                  f'N={res["trades"]} [{v}]{"  *" if n_flag else ""}')

    return rows


# ===== Plan C Phase2 =====
def run_planc_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, planc_p1):
    f1_rows = [r for r in planc_p1 if r['filter_type'] == 'C-F1_BBwidth']
    f2_rows = [r for r in planc_p1 if r['filter_type'] == 'C-F2_Hour']

    ac_f1 = [r for r in f1_rows if r['verdict'] in ('ADOPT', 'CONDITIONAL')]

    if not ac_f1:
        log_print('\n[PlanC-Phase2] C-F1でADOPT/CONDITIONALなし → Phase2スキップ')
        return []
    if not f2_rows:
        log_print('\n[PlanC-Phase2] C-F2結果なし → Phase2スキップ')
        return []

    total_runs = len(ac_f1) * len(f2_rows)
    log_print(f'\n[PlanC-Phase2] C-F1({len(ac_f1)}通り) × C-F2({len(f2_rows)}通り) = {total_runs} runs')

    rows      = []
    run_count = 0
    for f1 in ac_f1:
        for f2 in f2_rows:
            run_count += 1
            params_str = f'{f1["params"]} + {f2["params"]}'
            hours_val  = f2.get('_hours') or HOUR_SETS.get(f2.get('_set_name'))
            res = simulate_usdjpy(
                df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
                bw_ratio=f1['_bw_ratio'],
                bw_lookback=f1['_bw_lookback'],
                hour_list=hours_val,
            )
            if res is None:
                log_print(f'  [{run_count}/{total_runs}] {params_str}: N=0')
                continue
            dpf    = res['pf'] - BASELINE_PF
            v      = verdict(res['pf'], res['trades'])
            n_flag = 'N_insufficient' if res['trades'] < MIN_N else ''
            rows.append({
                'plan': 'C', 'phase': 2, 'filter_combo': 'C-F1+C-F2',
                'params': params_str,
                'PF': res['pf'], 'WR': res['win_rate'], 'N': res['trades'],
                'MaxDD': res['max_dd'], 'avg_win': res['avg_win'], 'avg_loss': res['avg_loss'],
                'verdict': v, 'N_flag': n_flag, 'delta_pf': round(dpf, 3),
            })
            log_print(f'  [{run_count}/{total_runs}] {params_str}: '
                      f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                      f'N={res["trades"]} [{v}]{"  *" if n_flag else ""}')

    return rows


# ===== Top5表示 =====
def print_top5(rows, label):
    if not rows:
        log_print(f'\n=== {label}: 結果なし ===')
        return
    top5 = sorted(rows, key=lambda x: x['PF'], reverse=True)[:5]
    log_print(f'\n=== {label} PF降順Top5 (ΔPF vs baseline={BASELINE_PF}) ===')
    hdr = f'  {"params":<45} | {"PF":>6} | {"ΔPF":>6} | {"WR%":>5} | {"N":>4} | {"MaxDD":>8} | {"verdict":>11}'
    log_print(hdr)
    log_print('  ' + '-' * 97)
    for r in top5:
        params_key = r.get('params', r.get('filter_combo', ''))
        log_print(f'  {params_key[:45]:<45} | '
                  f'{r["PF"]:>6.3f} | {r["delta_pf"]:>+6.3f} | '
                  f'{r["WR"]:>4.1f}% | {r["N"]:>4} | '
                  f'{r["MaxDD"]:>8.1f} | {r["verdict"]:>11}')


# ===== 最終比較表 =====
def print_final_comparison(planb_all, planc_all):
    log_print('\n' + '=' * 75)
    log_print('=== Plan B vs Plan C 最良設定比較 ===')
    log_print('=' * 75)

    def best_of(rows):
        if not rows:
            return None
        adopt = [r for r in rows if r['verdict'] == 'ADOPT']
        cond  = [r for r in rows if r['verdict'] == 'CONDITIONAL']
        if adopt:
            return sorted(adopt, key=lambda x: x['PF'], reverse=True)[0]
        if cond:
            return sorted(cond, key=lambda x: x['PF'], reverse=True)[0]
        return sorted(rows, key=lambda x: x['PF'], reverse=True)[0]

    best_b = best_of(planb_all)
    best_c = best_of(planc_all)

    fields = [
        ('verdict',     'verdict'),
        ('PF',          'PF'),
        ('ΔPF',         'delta_pf'),
        ('WR(%)',        'WR'),
        ('N',           'N'),
        ('MaxDD(pips)', 'MaxDD'),
    ]

    log_print(f'\n  {"項目":<14} | {"Plan B":>28} | {"Plan C":>28}')
    log_print('  ' + '-' * 75)
    for label, key in fields:
        bv = best_b[key] if best_b else 'N/A'
        cv = best_c[key] if best_c else 'N/A'
        if isinstance(bv, float):
            bv = f'{bv:+.3f}' if key == 'delta_pf' else f'{bv:.3f}'
        if isinstance(cv, float):
            cv = f'{cv:+.3f}' if key == 'delta_pf' else f'{cv:.3f}'
        log_print(f'  {label:<14} | {str(bv):>28} | {str(cv):>28}')

    log_print('')
    if best_b:
        log_print(f'  PlanB最良: {best_b["params"]}')
    if best_c:
        log_print(f'  PlanC最良: {best_c["params"]}')
    log_print(f'\n  ベースライン: PF={BASELINE_PF} WR={BASELINE_WR}% N={BASELINE_N} MaxDD={BASELINE_DD}pips')


# ===== メイン =====
def main():
    log_print('=== USDJPY フィルター改善グリッドBT ===')
    log_print(f'ベースライン: htf4h_rsi, sl={SL_ATR_MULT}, tp_rr={TP_RR}')
    log_print(f'             PF={BASELINE_PF} WR={BASELINE_WR}% N={BASELINE_N} MaxDD={BASELINE_DD}pips')
    log_print(f'採用基準: ADOPT=PF>{ADOPT_PF_TH} N>={MIN_N} / CONDITIONAL=PF>{COND_PF_TH} N>={MIN_N}')
    log_print(f'4h RSIベースライン: buy<{BASE_RSI_BUY_TH}, sell>{BASE_RSI_SELL_TH}')

    df_5m = load_csv(SYMBOL, '5m')
    df_1h = load_csv(SYMBOL, '1h')
    if df_5m is None or df_1h is None:
        log_print('[ERROR] CSVデータなし → 終了')
        return

    log_print(f'5m bars: {len(df_5m)}  1h bars: {len(df_1h)}')
    log_print('ルックアップ構築中...')
    htf_lkp    = build_htf_lookup(df_1h, HTF_PERIOD, HTF_SIGMA)
    htf4h_lkp  = build_htf4h_ema_lookup(df_1h)
    rsi_4h_lkp = build_rsi_4h_lookup(df_1h)
    log_print('構築完了')

    # ===== Plan B Phase1 =====
    log_print('\n' + '=' * 60)
    log_print('Plan B Phase1: RSI閾値グリッド (B-F1) + BBwidth (B-F2) 単体検証')
    log_print('=' * 60)
    planb_p1 = run_planb_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp)
    print_top5(planb_p1, 'PlanB Phase1')

    # ===== Plan C Phase1 =====
    log_print('\n' + '=' * 60)
    log_print('Plan C Phase1: BBwidth (C-F1) + 時間帯 (C-F2) 単体検証')
    log_print('=' * 60)
    planc_p1 = run_planc_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, bw_cache=planb_p1)
    print_top5(planc_p1, 'PlanC Phase1')

    # Phase1 CSV出力
    all_p1 = planb_p1 + planc_p1
    if all_p1:
        out1  = os.path.join(OPT_DIR, 'usdjpy_filter_phase1.csv')
        cols1 = ['plan', 'phase', 'filter_type', 'params', 'PF', 'WR', 'N',
                 'MaxDD', 'avg_win', 'avg_loss', 'verdict', 'N_flag', 'delta_pf']
        df1 = pd.DataFrame(all_p1)
        df1[cols1].to_csv(out1, index=False, encoding='utf-8')
        log_print(f'\n[Phase1] 出力: {out1} ({len(all_p1)}件)')

    # ===== Plan B Phase2 =====
    log_print('\n' + '=' * 60)
    log_print('Plan B Phase2: B-F1最良1設定 × B-F2全通り')
    log_print('=' * 60)
    planb_p2 = run_planb_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, planb_p1)
    if planb_p2:
        print_top5(planb_p2, 'PlanB Phase2')

    # ===== Plan C Phase2 =====
    log_print('\n' + '=' * 60)
    log_print('Plan C Phase2: C-F1(ADOPT/COND) × C-F2全通り')
    log_print('=' * 60)
    planc_p2 = run_planc_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, planc_p1)
    if planc_p2:
        print_top5(planc_p2, 'PlanC Phase2')

    # Phase2 CSV出力
    all_p2 = planb_p2 + planc_p2
    if all_p2:
        out2  = os.path.join(OPT_DIR, 'usdjpy_filter_phase2.csv')
        cols2 = ['plan', 'phase', 'filter_combo', 'params', 'PF', 'WR', 'N',
                 'MaxDD', 'avg_win', 'avg_loss', 'verdict', 'N_flag', 'delta_pf']
        df2 = pd.DataFrame(all_p2)
        df2[cols2].to_csv(out2, index=False, encoding='utf-8')
        log_print(f'\n[Phase2] 出力: {out2} ({len(all_p2)}件)')
    else:
        log_print('\n[Phase2] 出力なし（全プランPhase2スキップ）')

    # ===== 最終比較 =====
    print_final_comparison(planb_p1 + planb_p2, planc_p1 + planc_p2)
    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
