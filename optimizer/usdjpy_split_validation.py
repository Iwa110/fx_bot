"""
usdjpy_split_validation.py - USDJPY Plan B 期間分割検証
RSI(buy<55, sell>50) + BBwidth(ratio=0.8, lb=30) の過学習リスク確認

期間分割: 全データを時系列3分割 (A=前1/3, B=中1/3, C=後1/3)
フィルター3種:
  baseline  : htf4h_rsi (RSI buy<55 sell>45)
  rsi_only  : RSI buy<55 sell>50 (BBwidthなし)
  plan_b    : RSI buy<55 sell>50 + BBwidth(ratio=0.8, lb=30)

BUY/SELL分離分析: 全データ期間で方向別集計
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

# ===== フィルター設定 =====
BASE_RSI_BUY_TH  = 55
BASE_RSI_SELL_TH = 45
PLANB_RSI_BUY_TH  = 55
PLANB_RSI_SELL_TH = 50
PLANB_BW_RATIO    = 0.8
PLANB_BW_LOOKBACK = 30

BASELINE_PF = 1.242
BASELINE_WR = 45.6
BASELINE_N  = 103

# 判定閾値
STABLE_PF  = 1.0
RSI_OK_RATIO = 0.6   # SELL N >= BUY N x 0.6 で RSI_OK


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
    close     = df_1h['close']
    ma        = close.rolling(period).mean()
    std       = close.rolling(period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']


def build_htf4h_ema_lookup(df_1h, ema_period=20):
    df   = df_1h.copy().set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20']  = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']


def build_rsi_4h_lookup(df_1h, period=14):
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
def simulate(
    df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
    date_from=None, date_to=None,
    rsi_buy_th=BASE_RSI_BUY_TH,
    rsi_sell_th=BASE_RSI_SELL_TH,
    bw_ratio=None, bw_lookback=None,
):
    """
    ベースフィルター（常時ON）: 1h HTF sigma + 4h EMA20 + 4h RSI(閾値可変)
    追加フィルター: BBwidth
    date_from/date_to: 期間分割用（None=全期間）
    returns: dict with overall stats + buy/sell breakdown
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

    stats = {
        'buy':  {'wins': 0, 'losses': 0, 'gp': 0.0, 'gl': 0.0, 'pnl': []},
        'sell': {'wins': 0, 'losses': 0, 'gp': 0.0, 'gl': 0.0, 'pnl': []},
    }
    all_pnl  = []
    last_bar = -COOLDOWN_BARS - 1

    for i in range(BB_PERIOD + 1, n):
        if i - last_bar < COOLDOWN_BARS:
            continue

        c       = close_arr[i]
        sl_dist = atr.iloc[i] * SL_ATR_MULT
        tp_dist = sl_dist * TP_RR
        if sl_dist == 0 or np.isnan(sl_dist) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # 期間フィルター
        if date_from is not None and dt < date_from:
            continue
        if date_to is not None and dt >= date_to:
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

        # 4h RSI（常時ON: 閾値はパラメータで可変）
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

        # BBwidthフィルター（追加）
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
        all_pnl.append(pnl)
        s = stats[direction]
        if pnl > 0:
            s['wins'] += 1
            s['gp']   += pnl
        else:
            s['losses'] += 1
            s['gl']     += abs(pnl)
        s['pnl'].append(pnl)
        last_bar = i

    def _agg(s):
        trades = s['wins'] + s['losses']
        if trades == 0:
            return None
        return {
            'trades':   trades,
            'win_rate': round(s['wins'] / trades * 100, 1),
            'pf':       round(s['gp'] / s['gl'], 3) if s['gl'] > 0 else 99.0,
            'max_dd':   compute_max_dd_pips(s['pnl']),
        }

    buy_res  = _agg(stats['buy'])
    sell_res = _agg(stats['sell'])

    total_trades = (stats['buy']['wins'] + stats['buy']['losses'] +
                    stats['sell']['wins'] + stats['sell']['losses'])
    if total_trades == 0:
        return None

    total_gp = stats['buy']['gp'] + stats['sell']['gp']
    total_gl = stats['buy']['gl'] + stats['sell']['gl']
    total_wins = stats['buy']['wins'] + stats['sell']['wins']

    return {
        'trades':   total_trades,
        'win_rate': round(total_wins / total_trades * 100, 1),
        'pf':       round(total_gp / total_gl, 3) if total_gl > 0 else 99.0,
        'max_dd':   compute_max_dd_pips(all_pnl),
        'buy':      buy_res,
        'sell':     sell_res,
    }


# ===== 判定ロジック =====
def stability_verdict(pf_a, pf_b, pf_c):
    fail = sum(1 for p in [pf_a, pf_b, pf_c] if p is not None and p < STABLE_PF)
    if fail == 0:
        return 'STABLE'
    if fail == 1:
        return 'UNSTABLE'
    return 'REJECT'


def rsi_verdict(buy_n, sell_n):
    if buy_n == 0:
        return 'RSI_CAUTION'
    return 'RSI_OK' if sell_n >= buy_n * RSI_OK_RATIO else 'RSI_CAUTION'


# ===== コンソール表示 =====
def print_split_summary(split_data):
    """
    split_data: list of dicts
      {period, date_from, date_to, filter_type, pf, wr, n, max_dd}
    """
    log_print('\n' + '=' * 80)
    log_print('=== 期間分割サマリー ===')
    log_print('=' * 80)
    hdr = f'  {"期間":<10} | {"filter_type":<12} | {"PF":>6} | {"WR%":>5} | {"N":>4} | {"MaxDD":>8}'
    log_print(hdr)
    log_print('  ' + '-' * 60)
    cur_period = None
    for r in split_data:
        if r['period'] != cur_period:
            cur_period = r['period']
            log_print(f'  [{r["period"]}] {r["date_from"].date()} ~ {r["date_to"].date()}')
        pf_str = f'{r["pf"]:.3f}' if r["pf"] is not None else ' N/A '
        n_str  = str(r["n"])  if r["n"] is not None else 'N/A'
        wr_str = f'{r["wr"]:.1f}%' if r["wr"] is not None else 'N/A'
        dd_str = f'{r["max_dd"]:.1f}' if r["max_dd"] is not None else 'N/A'
        log_print(f'  {"":<10} | {r["filter_type"]:<12} | {pf_str:>6} | {wr_str:>5} | '
                  f'{n_str:>4} | {dd_str:>8}')


def print_buysell_summary(bs_data):
    """
    bs_data: list of dicts
      {period, direction, filter_type, pf, wr, n}
    """
    log_print('\n' + '=' * 70)
    log_print('=== BUY/SELL方向別サマリー（全データ） ===')
    log_print('=' * 70)
    hdr = f'  {"filter_type":<12} | {"direction":<9} | {"PF":>6} | {"WR%":>5} | {"N":>4}'
    log_print(hdr)
    log_print('  ' + '-' * 47)
    cur_ft = None
    for r in bs_data:
        if r['filter_type'] != cur_ft:
            cur_ft = r['filter_type']
            log_print(f'  {r["filter_type"]}')
        pf_str = f'{r["pf"]:.3f}' if r["pf"] is not None else '  N/A'
        n_str  = str(r["n"])  if r["n"] is not None else 'N/A'
        wr_str = f'{r["wr"]:.1f}%' if r["wr"] is not None else 'N/A'
        log_print(f'  {"":<12} | {r["direction"]:<9} | {pf_str:>6} | {wr_str:>5} | {n_str:>4}')


# ===== メイン =====
def main():
    log_print('=== USDJPY Plan B 期間分割検証 ===')
    log_print(f'検証フィルター: RSI(buy<{PLANB_RSI_BUY_TH},sell>{PLANB_RSI_SELL_TH})'
              f' + BBwidth(ratio={PLANB_BW_RATIO},lb={PLANB_BW_LOOKBACK})')
    log_print(f'ベースライン: PF={BASELINE_PF} WR={BASELINE_WR}% N={BASELINE_N}')

    df_5m = load_csv(SYMBOL, '5m')
    df_1h = load_csv(SYMBOL, '1h')
    if df_5m is None or df_1h is None:
        log_print('[ERROR] CSVデータなし → 終了')
        return

    log_print(f'5m bars: {len(df_5m)}  1h bars: {len(df_1h)}')
    log_print(f'5m 期間: {df_5m["datetime"].iloc[0]} ~ {df_5m["datetime"].iloc[-1]}')

    log_print('ルックアップ構築中...')
    htf_lkp    = build_htf_lookup(df_1h, HTF_PERIOD, HTF_SIGMA)
    htf4h_lkp  = build_htf4h_ema_lookup(df_1h)
    rsi_4h_lkp = build_rsi_4h_lookup(df_1h)
    log_print('構築完了')

    # ===== 期間分割 =====
    all_dates = df_5m['datetime']
    total_bars = len(df_5m)
    cut1 = total_bars // 3
    cut2 = cut1 * 2
    dt_a_from = all_dates.iloc[0]
    dt_a_to   = all_dates.iloc[cut1]
    dt_b_from = all_dates.iloc[cut1]
    dt_b_to   = all_dates.iloc[cut2]
    dt_c_from = all_dates.iloc[cut2]
    dt_c_to   = all_dates.iloc[-1] + pd.Timedelta(minutes=1)

    periods = [
        ('Period_A', dt_a_from, dt_a_to),
        ('Period_B', dt_b_from, dt_b_to),
        ('Period_C', dt_c_from, dt_c_to),
    ]

    log_print(f'\n期間分割:')
    for name, dfrom, dto in periods:
        log_print(f'  {name}: {dfrom.date()} ~ {dto.date()}')

    # フィルター3種の定義
    filter_configs = [
        ('baseline', BASE_RSI_BUY_TH,  BASE_RSI_SELL_TH,  None,           None),
        ('rsi_only', PLANB_RSI_BUY_TH, PLANB_RSI_SELL_TH, None,           None),
        ('plan_b',   PLANB_RSI_BUY_TH, PLANB_RSI_SELL_TH, PLANB_BW_RATIO, PLANB_BW_LOOKBACK),
    ]

    # ===== 期間分割 BT =====
    log_print('\n' + '=' * 60)
    log_print('期間分割 バックテスト (3期間 × 3フィルター = 9 runs)')
    log_print('=' * 60)

    split_rows   = []
    pf_table     = {}   # (period, filter_type) -> pf

    for period_name, dfrom, dto in periods:
        log_print(f'\n[{period_name}] {dfrom.date()} ~ {dto.date()}')
        for ft, rb, rs, bwr, bwl in filter_configs:
            res = simulate(
                df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
                date_from=dfrom, date_to=dto,
                rsi_buy_th=rb, rsi_sell_th=rs,
                bw_ratio=bwr, bw_lookback=bwl,
            )
            if res is None:
                log_print(f'  {ft:<12}: N=0')
                pf_table[(period_name, ft)] = None
                split_rows.append({
                    'period':    period_name,
                    'date_from': dfrom,
                    'date_to':   dto,
                    'filter_type': ft,
                    'PF':    None,
                    'WR':    None,
                    'N':     None,
                    'MaxDD': None,
                })
                continue
            pf_table[(period_name, ft)] = res['pf']
            log_print(f'  {ft:<12}: PF={res["pf"]} WR={res["win_rate"]}% '
                      f'N={res["trades"]} MaxDD={res["max_dd"]}pips')
            split_rows.append({
                'period':      period_name,
                'date_from':   dfrom,
                'date_to':     dto,
                'filter_type': ft,
                'PF':          res['pf'],
                'WR':          res['win_rate'],
                'N':           res['trades'],
                'MaxDD':       res['max_dd'],
            })

    # ===== 安定性判定 =====
    log_print('\n--- 安定性判定 ---')
    for ft in ['baseline', 'rsi_only', 'plan_b']:
        pfs = [pf_table.get((p, ft)) for p in ['Period_A', 'Period_B', 'Period_C']]
        sv  = stability_verdict(*pfs)
        pf_str = ' / '.join(f'{p:.3f}' if p is not None else 'N/A' for p in pfs)
        log_print(f'  {ft:<12}: [{sv}]  PF= {pf_str}  (A/B/C)')

    # ===== 全データ BUY/SELL分析 =====
    log_print('\n' + '=' * 60)
    log_print('BUY/SELL方向別分析（全データ）')
    log_print('=' * 60)

    bs_rows = []
    for ft, rb, rs, bwr, bwl in filter_configs:
        res = simulate(
            df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
            rsi_buy_th=rb, rsi_sell_th=rs,
            bw_ratio=bwr, bw_lookback=bwl,
        )
        if res is None:
            log_print(f'  {ft}: N=0')
            for direction in ['buy', 'sell']:
                bs_rows.append({'period': 'full', 'direction': direction,
                                'filter_type': ft, 'PF': None, 'WR': None, 'N': None})
            continue

        for direction in ['buy', 'sell']:
            dr = res.get(direction)
            if dr is None:
                log_print(f'  {ft} {direction}: N=0')
                bs_rows.append({'period': 'full', 'direction': direction,
                                'filter_type': ft, 'PF': None, 'WR': None, 'N': None})
            else:
                log_print(f'  {ft:<12} {direction:<5}: PF={dr["pf"]} '
                          f'WR={dr["win_rate"]}% N={dr["trades"]}')
                bs_rows.append({'period': 'full', 'direction': direction,
                                'filter_type': ft,
                                'PF':  dr['pf'],
                                'WR':  dr['win_rate'],
                                'N':   dr['trades']})

    # ===== 非対称RSI判定 =====
    log_print('\n--- 非対称RSI判定 ---')
    for ft in ['baseline', 'rsi_only', 'plan_b']:
        buy_row  = next((r for r in bs_rows if r['filter_type'] == ft and r['direction'] == 'buy'),  None)
        sell_row = next((r for r in bs_rows if r['filter_type'] == ft and r['direction'] == 'sell'), None)
        bn = buy_row['N']  if buy_row  and buy_row['N']  is not None else 0
        sn = sell_row['N'] if sell_row and sell_row['N'] is not None else 0
        rv = rsi_verdict(bn, sn)
        log_print(f'  {ft:<12}: [{rv}]  BUY N={bn} / SELL N={sn}'
                  f'  (ratio={sn/bn:.2f})' if bn > 0 else
                  f'  {ft:<12}: [{rv}]  BUY N={bn} / SELL N={sn}')

    # ===== コンソールサマリー =====
    print_split_summary([{
        'period':    r['period'],
        'date_from': r['date_from'],
        'date_to':   r['date_to'],
        'filter_type': r['filter_type'],
        'pf':    r['PF'],
        'wr':    r['WR'],
        'n':     r['N'],
        'max_dd': r['MaxDD'],
    } for r in split_rows])

    print_buysell_summary([{
        'period':      r['period'],
        'direction':   r['direction'],
        'filter_type': r['filter_type'],
        'pf':  r['PF'],
        'wr':  r['WR'],
        'n':   r['N'],
    } for r in bs_rows])

    # ===== CSV出力 =====
    out_split = os.path.join(OPT_DIR, 'usdjpy_split_validation.csv')
    cols_split = ['period', 'date_from', 'date_to', 'filter_type', 'PF', 'WR', 'N', 'MaxDD']
    df_split = pd.DataFrame(split_rows)
    df_split['date_from'] = df_split['date_from'].dt.date
    df_split['date_to']   = df_split['date_to'].dt.date
    df_split[cols_split].to_csv(out_split, index=False, encoding='utf-8')
    log_print(f'\n[出力] {out_split} ({len(split_rows)}件)')

    out_bs = os.path.join(OPT_DIR, 'usdjpy_buysell_analysis.csv')
    cols_bs = ['period', 'direction', 'filter_type', 'PF', 'WR', 'N']
    df_bs = pd.DataFrame(bs_rows)
    df_bs[cols_bs].to_csv(out_bs, index=False, encoding='utf-8')
    log_print(f'[出力] {out_bs} ({len(bs_rows)}件)')

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
