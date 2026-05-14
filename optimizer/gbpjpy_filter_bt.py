"""
gbpjpy_filter_bt.py - GBPJPY フィルター改善グリッドBT
Phase1: 単体フィルター効果検証 (RSI/ADX/BBwidth/Hour) 29runs
Phase2: 有効フィルター組み合わせ検証 (ADOPTフィルターの2組み合わせ)

ベースライン: htf4h_only, sl=3.0, tp_rr=1.5
             PF=0.944, WR=40.0%, N=557, MaxDD=2443.7pips
目標: PF>1.1 かつ N>=80

採用基準:
# ADOPT: PF>1.1 かつ N>=80
# CONDITIONAL: PF>1.0 かつ N>=80（要追加検証）
# REJECT: それ以外
"""

import os
import itertools
import numpy as np
import pandas as pd
from pathlib import Path

# ===== パス設定 =====
_VPS_DATA_DIR = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
_VPS_OPT_DIR = r'C:\Users\Administrator\fx_bot\optimizer'
OPT_DIR = _VPS_OPT_DIR if os.path.isdir(_VPS_OPT_DIR) else str(Path(__file__).parent)

# ===== GBPJPY固定設定 =====
SYMBOL          = 'GBPJPY'
PIP_UNIT        = 0.01
BB_PERIOD       = 20
BB_SIGMA        = 1.5
RSI_PERIOD      = 14
RSI_BUY_MAX     = 45
RSI_SELL_MIN    = 55
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

BASELINE_PF = 0.944
BASELINE_N  = 557

# ADOPT: PF>1.1 かつ N>=80
# CONDITIONAL: PF>1.0 かつ N>=80（要追加検証）
# REJECT: それ以外
ADOPT_PF_TH = 1.1
COND_PF_TH  = 1.0
MIN_N       = 80

# ===== フィルターグリッド定義 =====
# F1: 4h RSI
RSI_BUY_TH_GRID  = [45, 50, 55, 60]   # buy許可: 4h RSI < この値
RSI_SELL_TH_GRID = [40, 45, 50, 55]   # sell許可: 4h RSI > この値

# F2: 4h ADX
ADX_TH_GRID = [20, 25, 30]

# F3: BBバンド幅
BW_RATIO_GRID    = [0.8, 1.0, 1.2]
BW_LOOKBACK_GRID = [20, 30]

# F4: 時間帯（UTC）
HOUR_SETS = {
    'SET_A': [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
    'SET_B': [13, 14, 15, 16, 17, 18, 19, 20, 21, 22],
    'SET_C': [6, 7, 8, 13, 14, 15, 16, 17, 20, 21, 22],
    'SET_D': [7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
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


def calc_adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    hl  = high - low
    hc  = (high - close.shift()).abs()
    lc  = (low  - close.shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_s    = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di  = (100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
                / atr_s.replace(0, np.nan))
    minus_di = (100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
                / atr_s.replace(0, np.nan))
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


# ===== ルックアップ構築 =====
def build_htf_lookup(df_1h, period=20, sigma=1.5):
    """1h sigma position ルックアップ（既存HTFフィルター用）"""
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
    df4h['ema20']   = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal']  = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_rsi_4h_lookup(df_1h, period=14):
    """4h RSI ルックアップ（F1用）"""
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['rsi'] = calc_rsi(df4h['close'], period)
    return df4h['rsi']


def build_adx_4h_lookup(df_1h, period=14):
    """4h ADX ルックアップ（F2用）"""
    df   = df_1h.copy().set_index('datetime')
    agg  = {c: f for c, f in [('open', 'first'), ('high', 'max'), ('low', 'min'), ('close', 'last')]
            if c in df.columns}
    df4h = df.resample('4h').agg(agg).dropna()
    return calc_adx(df4h, period)


# ===== MaxDD計算 =====
def compute_max_dd_pips(pnl_list):
    """P&Lリスト（価格単位）からMaxDD（pips）を計算"""
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
def simulate_gbpjpy(
    df_5m, htf4h_lkp, htf_lkp,
    rsi_4h_lkp=None, rsi_buy_th=None, rsi_sell_th=None,   # F1
    adx_4h_lkp=None, adx_th=None,                          # F2
    bw_ratio=None, bw_lookback=None,                        # F3
    hour_list=None,                                         # F4
):
    """
    GBPJPYフィルター付きBT（全データ期間）。
    ベースフィルター: HTF sigma + HTF 4h EMA20 は常時ON。
    追加フィルターはキーワード引数で切替。
    """
    close = df_5m['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi_5m = calc_rsi(close, RSI_PERIOD)
    atr    = calc_atr(df_5m, ATR_PERIOD)

    # F3: BBwidth rolling mean（ループ外で事前計算）
    bb_width      = bb_u - bb_l
    bb_width_mean = (bb_width.rolling(bw_lookback).mean()
                     if bw_ratio is not None and bw_lookback is not None else None)

    close_arr = close.values
    n         = len(df_5m)

    wins = losses = 0
    tp_count = trail_count = sl_count = 0
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

        # F4: 時間帯フィルター
        if hour_list is not None and dt.hour not in hour_list:
            continue

        # HTF sigma（常時ON）
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= HTF_RANGE_SIGMA:
            continue

        # エントリー方向（5m BB + RSI）
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

        # ベースフィルター: HTF 4h EMA20（常時ON）
        htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
        if htf4h_idx < 0:
            continue
        htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
        if direction == 'buy'  and htf4h_sig != 1:
            continue
        if direction == 'sell' and htf4h_sig != -1:
            continue

        # F1: 4h RSIフィルター
        if rsi_4h_lkp is not None and rsi_buy_th is not None and rsi_sell_th is not None:
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

        # F2: 4h ADXフィルター
        if adx_4h_lkp is not None and adx_th is not None:
            adx_idx = adx_4h_lkp.index.searchsorted(dt, side='right') - 1
            if adx_idx < 0:
                continue
            adx_val = adx_4h_lkp.iloc[adx_idx]
            if np.isnan(adx_val) or adx_val < adx_th:
                continue

        # F3: BBwidthフィルター
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
        hit       = None
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

        if hit == 'tp':
            tp_count += 1
        elif hit == 'trail_sl':
            trail_count += 1
        else:
            sl_count += 1
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
    # ADOPT: PF>1.1 かつ N>=80
    # CONDITIONAL: PF>1.0 かつ N>=80（要追加検証）
    # REJECT: それ以外
    if pf > ADOPT_PF_TH and n >= MIN_N:
        return 'ADOPT'
    if pf > COND_PF_TH and n >= MIN_N:
        return 'CONDITIONAL'
    return 'REJECT'


# ===== Phase1 =====
def run_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, adx_4h_lkp):
    rows      = []
    run_count = 0
    total     = len(RSI_BUY_TH_GRID) * len(RSI_SELL_TH_GRID) + len(ADX_TH_GRID) + \
                len(BW_RATIO_GRID) * len(BW_LOOKBACK_GRID) + len(HOUR_SETS)
    log_print(f'[Phase1] 総実行数: {total} runs')

    def _add_row(filter_type, params_str, res, extra=None):
        dpf = res['pf'] - BASELINE_PF
        v   = verdict(res['pf'], res['trades'])
        n_flag = 'N_insufficient' if res['trades'] < MIN_N else ''
        row = {
            'phase':       1,
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
        rows.append(row)
        log_print(f'  [{run_count}/{total}] {params_str}: '
                  f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                  f'N={res["trades"]} [{v}]{"  *" if n_flag else ""}')

    # ---- F1: 4h RSI (16 runs) ----
    log_print(f'\n[Phase1-F1] RSI 4h ({len(RSI_BUY_TH_GRID)*len(RSI_SELL_TH_GRID)} runs)')
    for rsi_buy_th in RSI_BUY_TH_GRID:
        for rsi_sell_th in RSI_SELL_TH_GRID:
            run_count += 1
            params_str = f'rsi_buy<{rsi_buy_th},rsi_sell>{rsi_sell_th}'
            res = simulate_gbpjpy(
                df_5m, htf4h_lkp, htf_lkp,
                rsi_4h_lkp=rsi_4h_lkp,
                rsi_buy_th=rsi_buy_th,
                rsi_sell_th=rsi_sell_th,
            )
            if res is None:
                log_print(f'  [{run_count}/{total}] {params_str}: N=0')
                continue
            _add_row('F1_RSI', params_str, res,
                     {'_rsi_buy_th': rsi_buy_th, '_rsi_sell_th': rsi_sell_th})

    # ---- F2: 4h ADX (3 runs) ----
    log_print(f'\n[Phase1-F2] ADX 4h ({len(ADX_TH_GRID)} runs)')
    for adx_th in ADX_TH_GRID:
        run_count += 1
        params_str = f'adx>={adx_th}'
        res = simulate_gbpjpy(
            df_5m, htf4h_lkp, htf_lkp,
            adx_4h_lkp=adx_4h_lkp,
            adx_th=adx_th,
        )
        if res is None:
            log_print(f'  [{run_count}/{total}] {params_str}: N=0')
            continue
        _add_row('F2_ADX', params_str, res, {'_adx_th': adx_th})

    # ---- F3: BBwidth (6 runs) ----
    log_print(f'\n[Phase1-F3] BBwidth ({len(BW_RATIO_GRID)*len(BW_LOOKBACK_GRID)} runs)')
    for bw_ratio in BW_RATIO_GRID:
        for bw_lookback in BW_LOOKBACK_GRID:
            run_count += 1
            params_str = f'bw_ratio={bw_ratio},bw_lookback={bw_lookback}'
            res = simulate_gbpjpy(
                df_5m, htf4h_lkp, htf_lkp,
                bw_ratio=bw_ratio,
                bw_lookback=bw_lookback,
            )
            if res is None:
                log_print(f'  [{run_count}/{total}] {params_str}: N=0')
                continue
            _add_row('F3_BBwidth', params_str, res,
                     {'_bw_ratio': bw_ratio, '_bw_lookback': bw_lookback})

    # ---- F4: Hour (4 runs) ----
    log_print(f'\n[Phase1-F4] Hour ({len(HOUR_SETS)} runs)')
    for set_name, hours in HOUR_SETS.items():
        run_count += 1
        params_str = f'{set_name}={hours}'
        res = simulate_gbpjpy(
            df_5m, htf4h_lkp, htf_lkp,
            hour_list=hours,
        )
        if res is None:
            log_print(f'  [{run_count}/{total}] {set_name}: N=0')
            continue
        _add_row('F4_Hour', params_str, res,
                 {'_set_name': set_name, '_hours': hours})

    return rows


# ===== Phase2 =====
def run_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, adx_4h_lkp, phase1_rows):
    adopt = [r for r in phase1_rows if r['verdict'] == 'ADOPT']
    if not adopt:
        log_print('\n[Phase2] ADOPT条件(PF>1.1 かつ N>=80)を満たすフィルターなし → Phase2スキップ')
        return []

    log_print(f'\n[Phase2] ADOPTフィルター: {len(adopt)}件')
    for r in adopt:
        log_print(f'  {r["filter_type"]:>12} {r["params"]}  PF={r["PF"]} N={r["N"]}')

    # 異なるフィルタータイプのペアのみ組み合わせ
    pairs = [(a, b) for a, b in itertools.combinations(adopt, 2)
             if a['filter_type'] != b['filter_type']]
    log_print(f'[Phase2] 組み合わせ数: {len(pairs)} runs (同フィルタータイプは除外)')

    rows      = []
    run_count = 0
    for a, b in pairs:
        run_count += 1
        kwargs      = {}
        combo_label = f'{a["filter_type"]}+{b["filter_type"]}'

        for r in [a, b]:
            ft = r['filter_type']
            if ft == 'F1_RSI':
                kwargs['rsi_4h_lkp']  = rsi_4h_lkp
                kwargs['rsi_buy_th']  = r['_rsi_buy_th']
                kwargs['rsi_sell_th'] = r['_rsi_sell_th']
            elif ft == 'F2_ADX':
                kwargs['adx_4h_lkp'] = adx_4h_lkp
                kwargs['adx_th']     = r['_adx_th']
            elif ft == 'F3_BBwidth':
                kwargs['bw_ratio']    = r['_bw_ratio']
                kwargs['bw_lookback'] = r['_bw_lookback']
            elif ft == 'F4_Hour':
                kwargs['hour_list'] = r['_hours']

        params_str = f'{a["params"]} + {b["params"]}'
        res = simulate_gbpjpy(df_5m, htf4h_lkp, htf_lkp, **kwargs)
        if res is None:
            log_print(f'  [{run_count}] {combo_label}: N=0')
            continue

        dpf    = res['pf'] - BASELINE_PF
        v      = verdict(res['pf'], res['trades'])
        n_flag = 'N_insufficient' if res['trades'] < MIN_N else ''
        rows.append({
            'phase':        2,
            'filter_combo': combo_label,
            'params':       params_str,
            'PF':           res['pf'],
            'WR':           res['win_rate'],
            'N':            res['trades'],
            'MaxDD':        res['max_dd'],
            'avg_win':      res['avg_win'],
            'avg_loss':     res['avg_loss'],
            'verdict':      v,
            'N_flag':       n_flag,
            'delta_pf':     round(dpf, 3),
        })
        log_print(f'  [{run_count}] {combo_label}: '
                  f'PF={res["pf"]}({dpf:+.3f}) WR={res["win_rate"]}% '
                  f'N={res["trades"]} [{v}]{"  *" if n_flag else ""}')

    return rows


# ===== コンソール Top5 表示 =====
def print_top5(rows, label, key_col):
    if not rows:
        return
    top5 = sorted(rows, key=lambda x: x['PF'], reverse=True)[:5]
    log_print(f'\n=== {label} PF降順 Top5 (ΔPF vs baseline={BASELINE_PF}) ===')
    hdr = f'  {key_col:>30} | {"PF":>6} | {"ΔPF":>6} | {"WR":>5} | {"N":>5} | {"MaxDD":>8} | {"verdict":>11}'
    log_print(hdr)
    log_print('  ' + '-' * 85)
    for r in top5:
        key_val = r.get('filter_type', '') + ' ' + r.get('params', r.get('filter_combo', ''))
        log_print(f'  {key_val[:30]:>30} | '
                  f'{r["PF"]:>6.3f} | {r["delta_pf"]:>+6.3f} | '
                  f'{r["WR"]:>4.1f}% | {r["N"]:>5} | '
                  f'{r["MaxDD"]:>8.1f} | {r["verdict"]:>11}')


# ===== メイン =====
def main():
    log_print('=== GBPJPY フィルター改善グリッドBT ===')
    log_print(f'ベースライン: htf4h_only, sl={SL_ATR_MULT}, tp_rr={TP_RR}')
    log_print(f'             PF={BASELINE_PF} WR=40.0% N={BASELINE_N} MaxDD=2443.7pips')
    log_print(f'目標: PF>{ADOPT_PF_TH} かつ N>={MIN_N}')

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
    adx_4h_lkp = build_adx_4h_lookup(df_1h)
    log_print('構築完了')

    # ===== Phase1 =====
    log_print('\n' + '=' * 60)
    log_print('Phase1: 単体フィルター効果検証')
    log_print('=' * 60)
    phase1_rows = run_phase1(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, adx_4h_lkp)

    out1 = os.path.join(OPT_DIR, 'gbpjpy_filter_phase1.csv')
    if phase1_rows:
        cols1 = ['phase', 'filter_type', 'params', 'PF', 'WR', 'N',
                 'MaxDD', 'avg_win', 'avg_loss', 'verdict', 'N_flag', 'delta_pf']
        df1 = pd.DataFrame(phase1_rows)
        df1[cols1].to_csv(out1, index=False, encoding='utf-8')
        log_print(f'\n[Phase1] 出力: {out1} ({len(phase1_rows)}件)')
        print_top5(phase1_rows, 'Phase1', 'filter_type + params')

    # ===== Phase2 =====
    log_print('\n' + '=' * 60)
    log_print('Phase2: 有効フィルター組み合わせ検証')
    log_print('=' * 60)
    phase2_rows = run_phase2(df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp, adx_4h_lkp, phase1_rows)

    if phase2_rows:
        out2  = os.path.join(OPT_DIR, 'gbpjpy_filter_phase2.csv')
        cols2 = ['phase', 'filter_combo', 'params', 'PF', 'WR', 'N',
                 'MaxDD', 'avg_win', 'avg_loss', 'verdict', 'N_flag', 'delta_pf']
        df2 = pd.DataFrame(phase2_rows)
        df2[cols2].to_csv(out2, index=False, encoding='utf-8')
        log_print(f'\n[Phase2] 出力: {out2} ({len(phase2_rows)}件)')
        print_top5(phase2_rows, 'Phase2', 'filter_combo')

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
