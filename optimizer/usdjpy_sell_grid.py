"""
usdjpy_sell_grid.py - USDJPY SELL閾値再グリッド検証
RSI sell_th [45..50] を走査し SELL N不足を解消しながら PF改善を維持する最適閾値を探索。

固定: rsi_buy_th=55, BBwidth(ratio=0.8, lb=30), sl=3.0, tp_rr=1.5
グリッド: rsi_sell_th = [45, 46, 47, 48, 49, 50]

採用基準:
# ADOPT:       PF>1.3 かつ N>=80 かつ RSI_OK
# CONDITIONAL: PF>1.1 かつ N>=80 かつ RSI_OK
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

# ===== グリッド設定 =====
RSI_BUY_TH      = 55                          # 固定
RSI_SELL_TH_GRID = [45, 46, 47, 48, 49, 50]  # 探索対象
BW_RATIO        = 0.8
BW_LOOKBACK     = 30

# ===== 採用基準 =====
ADOPT_PF_TH = 1.3
COND_PF_TH  = 1.1
MIN_N       = 80
RSI_OK_RATIO = 0.6   # SELL N >= BUY N x 0.6 で RSI_OK

# ===== ベースライン =====
BASELINE = {'label': 'baseline(sell>45,no-bw)', 'PF': 1.242, 'WR': 45.6, 'N': 103,
            'sell_N': 99, 'verdict': 'ADOPT'}
PLANB    = {'label': 'plan_b(sell>50,bw)',       'PF': 1.849, 'WR': 57.3, 'N': 103,
            'sell_N': 20, 'verdict': 'RSI_CAUTION→実質REJECT'}


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
    rsi_buy_th, rsi_sell_th,
    bw_ratio=None, bw_lookback=None,
):
    """BUY/SELL別内訳を含む全集計を返す"""
    close = df_5m['close']
    bb_u, bb_l, _bb_ma, _bb_std = calc_bb(close, BB_PERIOD, BB_SIGMA)
    rsi_5m = calc_rsi(close, RSI_PERIOD)
    atr    = calc_atr(df_5m, ATR_PERIOD)

    bb_width      = bb_u - bb_l
    bb_width_mean = (bb_width.rolling(bw_lookback).mean()
                     if bw_ratio is not None and bw_lookback is not None else None)

    close_arr = close.values
    n         = len(df_5m)

    buckets = {
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

        # 4h RSI（常時ON: 閾値はパラメータ）
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
        b = buckets[direction]
        if pnl > 0:
            b['wins'] += 1
            b['gp']   += pnl
        else:
            b['losses'] += 1
            b['gl']     += abs(pnl)
        b['pnl'].append(pnl)
        last_bar = i

    def _agg(b):
        trades = b['wins'] + b['losses']
        if trades == 0:
            return {'pf': None, 'wr': None, 'n': 0}
        return {
            'pf': round(b['gp'] / b['gl'], 3) if b['gl'] > 0 else 99.0,
            'wr': round(b['wins'] / trades * 100, 1),
            'n':  trades,
        }

    buy_r  = _agg(buckets['buy'])
    sell_r = _agg(buckets['sell'])
    total  = buy_r['n'] + sell_r['n']
    if total == 0:
        return None

    total_gp   = buckets['buy']['gp']  + buckets['sell']['gp']
    total_gl   = buckets['buy']['gl']  + buckets['sell']['gl']
    total_wins = buckets['buy']['wins'] + buckets['sell']['wins']

    return {
        'trades':   total,
        'win_rate': round(total_wins / total * 100, 1),
        'pf':       round(total_gp / total_gl, 3) if total_gl > 0 else 99.0,
        'max_dd':   compute_max_dd_pips(all_pnl),
        'buy':      buy_r,
        'sell':     sell_r,
    }


# ===== 判定 =====
def make_verdict(pf, total_n, buy_n, sell_n):
    sell_ratio = sell_n / buy_n if buy_n > 0 else 0.0
    rsi_ok = sell_ratio >= RSI_OK_RATIO
    if pf is None:
        return 'REJECT', 'N/A', sell_ratio
    if pf > ADOPT_PF_TH and total_n >= MIN_N and rsi_ok:
        return 'ADOPT', 'RSI_OK', sell_ratio
    if pf > COND_PF_TH and total_n >= MIN_N and rsi_ok:
        return 'CONDITIONAL', 'RSI_OK', sell_ratio
    rsi_label = 'RSI_OK' if rsi_ok else 'RSI_CAUTION'
    return 'REJECT', rsi_label, sell_ratio


# ===== メイン =====
def main():
    log_print('=== USDJPY SELL閾値再グリッド検証 ===')
    log_print(f'固定: rsi_buy_th={RSI_BUY_TH}, BBwidth(ratio={BW_RATIO},lb={BW_LOOKBACK})')
    log_print(f'グリッド: rsi_sell_th={RSI_SELL_TH_GRID}')
    log_print(f'採用基準: ADOPT=PF>{ADOPT_PF_TH} N>={MIN_N} RSI_OK'
              f' / CONDITIONAL=PF>{COND_PF_TH} N>={MIN_N} RSI_OK')

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

    log_print(f'\n{"="*60}')
    log_print(f'グリッドサーチ ({len(RSI_SELL_TH_GRID)} runs)')
    log_print(f'{"="*60}')

    rows   = []
    adopt  = []
    total  = len(RSI_SELL_TH_GRID)

    for idx, sell_th in enumerate(RSI_SELL_TH_GRID, 1):
        res = simulate(
            df_5m, htf4h_lkp, htf_lkp, rsi_4h_lkp,
            rsi_buy_th=RSI_BUY_TH,
            rsi_sell_th=sell_th,
            bw_ratio=BW_RATIO,
            bw_lookback=BW_LOOKBACK,
        )

        if res is None:
            log_print(f'  [{idx}/{total}] sell_th={sell_th}: N=0')
            rows.append({
                'rsi_sell_th': sell_th,
                'PF': None, 'WR': None, 'N': 0, 'MaxDD': None,
                'buy_PF': None, 'buy_WR': None, 'buy_N': 0,
                'sell_PF': None, 'sell_WR': None, 'sell_N': 0,
                'sell_ratio': 0.0, 'verdict': 'REJECT',
            })
            continue

        buy_n  = res['buy']['n']
        sell_n = res['sell']['n']
        v, rsi_label, sell_ratio = make_verdict(res['pf'], res['trades'], buy_n, sell_n)
        tag = f'[{v}][{rsi_label}]'

        log_print(f'  [{idx}/{total}] sell_th={sell_th}: '
                  f'PF={res["pf"]} WR={res["win_rate"]}% N={res["trades"]} '
                  f'MaxDD={res["max_dd"]}pips  {tag}')
        log_print(f'           BUY: PF={res["buy"]["pf"]} WR={res["buy"]["wr"]}% N={buy_n} '
                  f'| SELL: PF={res["sell"]["pf"]} WR={res["sell"]["wr"]}% N={sell_n} '
                  f'(ratio={sell_ratio:.2f})')

        row = {
            'rsi_sell_th': sell_th,
            'PF':    res['pf'],
            'WR':    res['win_rate'],
            'N':     res['trades'],
            'MaxDD': res['max_dd'],
            'buy_PF':  res['buy']['pf'],
            'buy_WR':  res['buy']['wr'],
            'buy_N':   buy_n,
            'sell_PF': res['sell']['pf'],
            'sell_WR': res['sell']['wr'],
            'sell_N':  sell_n,
            'sell_ratio': round(sell_ratio, 2),
            'verdict': v,
        }
        rows.append(row)
        if v == 'ADOPT':
            adopt.append(row)

    # ===== ADOPT抜粋 =====
    log_print(f'\n{"="*60}')
    log_print('ADOPT設定 抜粋')
    log_print(f'{"="*60}')
    if adopt:
        hdr = (f'  {"sell_th":>8} | {"PF":>6} | {"WR%":>5} | {"N":>4} | {"MaxDD":>8} | '
               f'{"buy_N":>5} | {"sell_N":>6} | {"ratio":>5}')
        log_print(hdr)
        log_print('  ' + '-' * 70)
        for r in adopt:
            log_print(f'  {r["rsi_sell_th"]:>8} | {r["PF"]:>6.3f} | {r["WR"]:>4.1f}% | '
                      f'{r["N"]:>4} | {r["MaxDD"]:>8.1f} | '
                      f'{r["buy_N"]:>5} | {r["sell_N"]:>6} | {r["sell_ratio"]:>5.2f}')
    else:
        log_print('  ADOPTなし')

    # ===== トレードオフ表 =====
    log_print(f'\n{"="*60}')
    log_print('トレードオフ表: rsi_sell_th | 全体PF | SELL N | verdict')
    log_print(f'{"="*60}')
    hdr2 = f'  {"rsi_sell_th":>12} | {"全体PF":>7} | {"SELL N":>7} | {"verdict"}'
    log_print(hdr2)
    log_print('  ' + '-' * 48)

    # ベースライン行
    log_print(f'  {"baseline(45)":>12} |  {BASELINE["PF"]:.3f} | {BASELINE["sell_N"]:>6} | '
              f'{BASELINE["verdict"]}  ← 参考')
    for r in rows:
        pf_str = f'{r["PF"]:.3f}' if r['PF'] is not None else '  N/A'
        log_print(f'  {r["rsi_sell_th"]:>12} | {pf_str:>7} | {r["sell_N"]:>6} | {r["verdict"]}')
    # plan_b行
    log_print(f'  {"plan_b(50)":>12} |  {PLANB["PF"]:.3f} | {PLANB["sell_N"]:>6} | '
              f'{PLANB["verdict"]}  ← 参考')

    # ===== CSV出力 =====
    out = os.path.join(OPT_DIR, 'usdjpy_sell_grid.csv')
    cols = ['rsi_sell_th', 'PF', 'WR', 'N', 'MaxDD',
            'buy_PF', 'buy_WR', 'buy_N',
            'sell_PF', 'sell_WR', 'sell_N',
            'sell_ratio', 'verdict']
    df_out = pd.DataFrame(rows)
    df_out[cols].to_csv(out, index=False, encoding='utf-8')
    log_print(f'\n[出力] {out} ({len(rows)}件)')

    log_print('\n=== 完了 ===')


if __name__ == '__main__':
    main()
