"""
backtest.py - Phase3: BB戦略パラメータ候補のバックテスト
入力: candidates.json (Phase2出力・複合パラメータ) または suggestions.json (単一パラメータ)
出力: backtest_results.json

bb_monitor v13準拠のロジックをローカルCSVデータで再現。
MT5依存部分（F2乖離フィルター, F3BBスタック）はCSVのみでは計算不可のためスキップ。
"""

import json
import os
import argparse
import numpy as np
import pandas as pd
from pathlib import Path

# ===== パス設定 =====
_VPS_DATA_DIR    = r'C:\Users\Administrator\fx_bot\data'
DATA_DIR         = _VPS_DATA_DIR if os.path.isdir(_VPS_DATA_DIR) else str(Path(__file__).parent.parent / 'data')
CANDIDATES_FILE  = 'candidates.json'
SUGGESTIONS_FILE = 'suggestions.json'
OUTPUT_FILE      = 'backtest_results.json'

# ===== USDCAD固有設定（bb_monitor v13準拠） =====
USDCAD_CFG = {
    'bb_period':       20,
    'bb_sigma':        1.5,
    'htf_period':      20,
    'htf_sigma':       1.5,
    'htf_range_sigma': 1.0,
    'htf_bars':        50,
    'rsi_period':      14,
    'rsi_buy_max':     45,
    'rsi_sell_min':    55,
    'sl_atr_mult':     1.5,
    'tp_sl_ratio':     1.0,
    'atr_period':      14,
    'cooldown_bars':   3,
    'spread_pips':     2,
    'filter_type':     None,
    'f1_param':        3,
    'pip_unit':        0.0001,
    'is_jpy':          False,
}
# ===== BB全ペア設定 =====
BB_PAIRS_CFG = {
    'GBPJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 1.5, 'sl_atr_mult': 3.0, 'tp_sl_ratio': 1.5, 'use_htf4h': True},
    'EURJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 1.5, 'sl_atr_mult': 3.0, 'tp_sl_ratio': 1.5},
    'AUDJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 1.5, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5},
    'USDJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 2.0, 'sl_atr_mult': 3.0, 'tp_sl_ratio': 1.5, 'use_htf4h': True},
    'EURUSD': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'sl_atr_mult': 1.2, 'tp_sl_ratio': 1.5, 'use_htf4h': True, 'bb_width_th': 0.002},
    'GBPUSD': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'sl_atr_mult': 1.2, 'tp_sl_ratio': 1.5, 'use_htf4h': False},
}

# ===== フィルターBT対象ペア =====
FILTER_BT_PAIRS = ['GBPJPY', 'USDJPY']

# ===== ペア別時間帯フィルター候補（UTC） =====
HOUR_FILTER_CANDIDATES = {
    'USDJPY': [21, 22, 5],   # v17現行に差し替え
    'GBPJPY': [7, 8, 9, 13, 14, 15, 16],
}

# ===== Stage2候補 =====
STAGE2_CANDIDATES = [
    {'label': 'current',      'activate': 0.70, 'distance': 1.00},
    {'label': 'D(0.75/0.45)', 'activate': 0.75, 'distance': 0.45},
    {'label': 'B(0.80/0.40)', 'activate': 0.80, 'distance': 0.40},
    {'label': 'C(0.85/0.30)', 'activate': 0.85, 'distance': 0.30},
]
GRID_SL_CANDIDATES       = [1.5, 2.0, 2.5, 3.0]
GRID_STAGE2_DISTANCES    = [0.1, 0.2, 0.3]
GRID_STAGE2_ACTIVATE     = 0.7

# ===== ベースパラメータ取得 =====
def get_base_params():
    return USDCAD_CFG.copy()

# ===== CSVデータ読み込み =====
def load_csv(symbol, tf='5m'):
    candidates = [
        os.path.join(DATA_DIR, f'{symbol}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol.lower()}_{tf}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_{tf.upper()}.csv'),
        os.path.join(DATA_DIR, f'{symbol}_H1.csv'),
        os.path.join(DATA_DIR, f'{symbol}_M5.csv'),
    ]
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

    print(f'[WARN] CSVなし: {symbol} {tf}')
    return None

# ===== インジケーター =====
def calc_bb(close, period, sigma):
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
    """ADX(period)を1h足DataFrameから計算。戻り値: pd.Series"""
    high  = df['high']
    low   = df['low']
    close = df['close']

    plus_dm  = high.diff()
    minus_dm = low.diff().mul(-1)
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low  - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)

    atr_s     = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di   = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di  = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx        = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx       = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx

# ===== HTFシグナル構築（BB sigma position） =====
def build_htf_lookup(df_1h, htf_period, htf_sigma):
    close = df_1h['close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    ma        = close.rolling(htf_period).mean()
    std       = close.rolling(htf_period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']

# ===== 4h EMA20 HTFフィルター用ルックアップ構築 =====
def build_htf4h_ema_lookup(df_1h, ema_period=20):
    """
    1h CSVを4hにリサンプルしてEMA20を計算。
    戻り値: pd.Series (index=datetime, value= +1:Buy許可 / -1:Sell許可 / 0:両方許可)
    ※ closeがEMA20より上 → Buy許可(+1)、下 → Sell許可(-1)
    """
    df = df_1h.copy()
    df = df.set_index('datetime')
    df4h = df['close'].resample('4h').last().dropna().to_frame()
    df4h['ema20'] = df4h['close'].ewm(span=ema_period, adjust=False).mean()
    df4h['signal'] = np.where(df4h['close'] > df4h['ema20'], 1, -1)
    return df4h['signal']

# ===== ADXルックアップ構築（1h足） =====
def build_adx_lookup(df_1h, period=14, threshold=20):
    """
    1h足でADX(period)を計算。
    戻り値: pd.Series (index=datetime, value=ADX値)
    """
    df = df_1h.set_index('datetime') if 'datetime' in df_1h.columns else df_1h.copy()
    adx = calc_adx(df, period)
    return adx

# ===== F1モメンタムフィルター =====
def f1_ok(close_arr, i, direction, param):
    if i < param:
        return True
    diff = close_arr[i] - close_arr[i - param]
    if direction == 'buy':
        return diff < 0
    else:
        return diff > 0

# ===== simulate_with_stage2 =====
def simulate_with_stage2(symbol, pair_cfg, stage2_activate, stage2_distance,
                          sl_atr_mult=None, n_bars=5000):
    """
    Stage2トレーリングSLシミュレーター。
    pair_cfg追加対応キー: use_htf4h, filter_type('F1'/'F2andF1'/None), f1_param, bb_width_th
    戻り値: {'avg_exit_pct', 'win_rate', 'pf', 'trades', 'rr_actual',
              'tp_count', 'trail_count', 'sl_count'}
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)
    if sl_atr_mult is not None:
        cfg['sl_atr_mult'] = sl_atr_mult

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    htf4h_lkp   = build_htf4h_ema_lookup(df_1h) if cfg.get('use_htf4h') else None
    bb_width_th = cfg.get('bb_width_th')
    filter_type = cfg.get('filter_type')
    f1_param    = cfg.get('f1_param', 3)

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = tp_count = trail_count = sl_count = 0
    gross_profit = gross_loss = 0.0
    exit_pcts = []
    last_bar  = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c   = close_arr[i]
        sl  = atr.iloc[i] * cfg['sl_atr_mult']
        tp  = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        if bb_width_th is not None:
            bw = (bb_std.iloc[i] * 2) / bb_ma.iloc[i] if bb_ma.iloc[i] != 0 else 0
            if bw < bb_width_th:
                continue

        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        direction = None
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        if filter_type in ('F1', 'F2andF1'):
            if not f1_ok(close_arr, i, direction, f1_param):
                continue

        if htf4h_lkp is not None:
            htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if htf4h_idx < 0:
                continue
            htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
            if direction == 'buy'  and htf4h_sig != 1:
                continue
            if direction == 'sell' and htf4h_sig != -1:
                continue

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = abs(tp_price - entry)

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
                if progress >= stage2_activate:
                    activated = True
                if activated:
                    new_trail = mid - tp_dist * stage2_distance
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
                if progress >= stage2_activate:
                    activated = True
                if activated:
                    new_trail = mid + tp_dist * stage2_distance
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

        if direction == 'buy':
            exit_pct = (exit_price - entry) / tp_dist * 100
        else:
            exit_pct = (entry - exit_price) / tp_dist * 100

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price

        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)

        if hit == 'tp':
            tp_count += 1
        elif hit == 'trail_sl':
            trail_count += 1
        elif hit == 'sl':
            sl_count += 1
        exit_pcts.append(exit_pct)
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':       trades,
        'win_rate':     round(wins / trades * 100, 1),
        'pf':           round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual':    round(gross_profit / wins / (gross_loss / losses), 3) if wins > 0 and losses > 0 else 0.0,
        'avg_exit_pct': round(float(np.mean(exit_pcts)), 1),
        'tp_count':     tp_count,
        'trail_count':  trail_count,
        'sl_count':     sl_count,
    }

# ===== コアBTロジック =====
def run_backtest(symbol, override_params):
    cfg = get_base_params()
    cfg.update(override_params)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    spread    = cfg['spread_pips'] * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = tp_reach = 0
    gross_profit = gross_loss = 0.0
    last_trade_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_trade_bar < cfg['cooldown_bars']:
            continue

        c   = close_arr[i]
        sl  = atr.iloc[i] * cfg['sl_atr_mult']
        tp  = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl):
            continue

        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sigma_pos = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sigma_pos):
            continue

        direction = None
        if c < bb_l.iloc[i] and not np.isnan(rsi.iloc[i]):
            if rsi.iloc[i] < cfg['rsi_buy_max']:
                if abs(htf_sigma_pos) < cfg['htf_range_sigma']:
                    if cfg['filter_type'] is None or f1_ok(close_arr, i, 'buy', cfg['f1_param']):
                        direction = 'buy'
        elif c > bb_u.iloc[i] and not np.isnan(rsi.iloc[i]):
            if rsi.iloc[i] > cfg['rsi_sell_min']:
                if abs(htf_sigma_pos) < cfg['htf_range_sigma']:
                    if cfg['filter_type'] is None or f1_ok(close_arr, i, 'sell', cfg['f1_param']):
                        direction = 'sell'

        if direction is None:
            continue

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 200, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'; break
                if h >= tp_price:
                    hit = 'tp'; break
            else:
                if h >= sl_price:
                    hit = 'sl'; break
                if l <= tp_price:
                    hit = 'tp'; break

        if hit == 'tp':
            wins += 1
            tp_reach += 1
            gross_profit += tp
        elif hit == 'sl':
            losses += 1
            gross_loss += sl
        else:
            continue

        last_trade_bar = i

    trades = wins + losses
    if trades == 0:
        return {'pf': 0.0, 'win_rate': 0.0, 'rr_actual': 0.0, 'trades': 0, 'tp_reach_rate': 0.0}

    return {
        'pf':            round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'win_rate':      round(wins / trades * 100, 1),
        'rr_actual':     round(gross_profit / wins / (gross_loss / losses), 3) if wins > 0 and losses > 0 else 0.0,
        'trades':        trades,
        'tp_reach_rate': round(tp_reach / trades * 100, 1),
    }

# ===== candidates.json対応 =====
def run_backtest_from_candidates(symbol, output_file):
    if not Path(CANDIDATES_FILE).exists():
        print(f'[ERROR] {CANDIDATES_FILE} が見つかりません')
        return []

    data       = json.loads(Path(CANDIDATES_FILE).read_text(encoding='utf-8'))
    candidates = data.get('candidates', [])
    print(f'[Phase3] candidates.json読み込み: {len(candidates)}件')

    baseline = run_backtest(symbol, {})
    results  = []

    if baseline:
        print(f'[ベースライン] PF={baseline["pf"]}  勝率={baseline["win_rate"]}%  '
              f'RR={baseline["rr_actual"]}  取引数={baseline["trades"]}')
        results.append({
            'id': 'baseline', 'params': {}, 'description': 'baseline',
            'priority': 0, 'symbol': symbol, 'result': 'BASELINE', **baseline,
        })

    for cand in sorted(candidates, key=lambda x: x.get('priority', 99)):
        res = run_backtest(symbol, cand['params'])
        if res is None:
            print(f'  [{cand["id"]}] BT失敗')
            continue

        verdict = 'APPROVED' if res['pf'] >= 1.0 and res['trades'] >= 20 else 'REJECTED'
        row = {
            'id':          cand['id'],
            'params':      cand['params'],
            'description': cand.get('description', ''),
            'priority':    cand.get('priority', 99),
            'symbol':      symbol,
            'result':      verdict,
            **res,
        }
        results.append(row)

        dpf = res['pf'] - (baseline['pf'] if baseline else 0)
        dwr = res['win_rate'] - (baseline['win_rate'] if baseline else 0)
        print(f'  [{cand["id"]}] {verdict}  '
              f'PF={res["pf"]}({dpf:+.3f})  '
              f'勝率={res["win_rate"]}%({dwr:+.1f}%)  '
              f'RR={res["rr_actual"]}  N={res["trades"]}')

    existing = []
    if Path(output_file).exists():
        existing = json.loads(Path(output_file).read_text(encoding='utf-8'))
        if not isinstance(existing, list):
            existing = existing.get('results', [])

    all_results = existing + results
    Path(output_file).write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f'\n[Phase3] 出力完了: {output_file} (今回{len(results)}件 / 累計{len(all_results)}件)')
    return results

# ===== suggestions.json対応（後方互換） =====
def run_backtest_from_suggestions(symbol, output_file):
    suggestions = load_suggestions()
    results     = []

    baseline = run_backtest(symbol, {})
    if baseline is None:
        print('[ERROR] CSVデータなし → 終了')
        return []

    print(f'[ベースライン] PF={baseline["pf"]}  勝率={baseline["win_rate"]}%  '
          f'RR={baseline["rr_actual"]}  取引数={baseline["trades"]}')
    results.append({'param': 'baseline', 'candidate': 'current', 'symbol': symbol, **baseline})

    for sug in sorted(suggestions, key=lambda x: x.get('priority', 99)):
        param = sug['param']
        cands = sug.get('candidates', sug.get('values', []))
        print(f'\n[{param}] current={sug.get("current")}  reason: {sug.get("reason", "")}')

        for cand_val in cands:
            res = run_backtest(symbol, {param: cand_val})
            if res is None:
                continue
            row = {'param': param, 'candidate': cand_val, 'symbol': symbol, **res}
            results.append(row)
            dpf = res['pf'] - baseline['pf']
            dwr = res['win_rate'] - baseline['win_rate']
            print(f'  {param}={cand_val}: '
                  f'PF={res["pf"]}({dpf:+.3f})  '
                  f'勝率={res["win_rate"]}%({dwr:+.1f}%)  '
                  f'RR={res["rr_actual"]}  N={res["trades"]}')

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n=== 出力完了: {output_file} ({len(results)}件) ===')
    return results

# ===== suggestions.json読み込み =====
def load_suggestions():
    if not os.path.exists(SUGGESTIONS_FILE):
        print('[WARN] suggestions.json不在 → USDCADデフォルト候補で実行')
        return [
            {'param': 'tp_sl_ratio',     'current': 1.0,  'candidates': [1.5, 2.0, 2.5, 3.0], 'priority': 1, 'reason': 'RR改善'},
            {'param': 'rsi_sell_min',    'current': 55,   'candidates': [60, 65, 70],           'priority': 2, 'reason': 'フィルター強化'},
            {'param': 'rsi_buy_max',     'current': 45,   'candidates': [35, 30, 25],           'priority': 2, 'reason': 'フィルター強化'},
            {'param': 'htf_range_sigma', 'current': 1.0,  'candidates': [0.5, 0.7, 1.5],       'priority': 3, 'reason': 'HTFフィルター'},
            {'param': 'sl_atr_mult',     'current': 1.5,  'candidates': [1.0, 1.2, 2.0],       'priority': 3, 'reason': 'SL幅調整'},
        ]
    with open(SUGGESTIONS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# ===== Stage2 BT =====
def run_stage2_backtest():
    print('=== Stage2パラメータ比較BT ===')
    rows = []
    for symbol, pair_cfg in BB_PAIRS_CFG.items():
        print(f'\n--- {symbol} ---')
        for cand in STAGE2_CANDIDATES:
            res = simulate_with_stage2(
                symbol, pair_cfg,
                stage2_activate=cand['activate'],
                stage2_distance=cand['distance'],
            )
            if res is None:
                print(f'  {cand["label"]}: データなし')
                continue
            row = {'symbol': symbol, **cand, **res}
            rows.append(row)
            print(f'  {cand["label"]:20s} | '
                  f'avg_exit={res["avg_exit_pct"]:+5.1f}%TP | '
                  f'PF={res["pf"]:.3f} | 勝率={res["win_rate"]}% | '
                  f'TP={res["tp_count"]} Trail={res["trail_count"]} SL={res["sl_count"]}')

    out = r'C:\Users\Administrator\fx_bot\optimizer\stage2_bt_results.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\n出力: {out}')

# ===== SL幅シミュレーター =====
def simulate_with_sl(symbol, pair_cfg, sl_atr_mult, n_bars=5000):
    cfg = get_base_params()
    cfg.update(pair_cfg)
    cfg['sl_atr_mult'] = sl_atr_mult

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = tp_count = sl_count = 0
    gross_profit = gross_loss = 0.0
    last_bar = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c  = close_arr[i]
        sl = atr.iloc[i] * cfg['sl_atr_mult']
        tp = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        direction = None
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 300, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'; break
                if h >= tp_price:
                    hit = 'tp'; break
            else:
                if h >= sl_price:
                    hit = 'sl'; break
                if l <= tp_price:
                    hit = 'tp'; break

        if hit == 'tp':
            wins += 1; tp_count += 1; gross_profit += tp
        elif hit == 'sl':
            losses += 1; sl_count += 1; gross_loss += sl
        else:
            continue

        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':    trades,
        'win_rate':  round(wins / trades * 100, 1),
        'pf':        round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual': round(gross_profit / wins / (gross_loss / losses), 3)
                     if wins > 0 and losses > 0 else 0.0,
        'tp_rate':   round(tp_count / trades * 100, 1),
    }

SL_CANDIDATES = [1.5, 2.0, 2.5, 3.0]

def run_sl_backtest():
    print('=== SL幅比較BT ===')
    STAGE2_ACTIVATE_BASE = 0.70
    STAGE2_DISTANCE_BASE = 1.00
    rows = []

    for symbol, pair_cfg in BB_PAIRS_CFG.items():
        print(f'\n--- {symbol} (tp_sl_ratio={pair_cfg["tp_sl_ratio"]}) ---')
        print(f'  {"sl_mult":>8} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"TP率":>6} | {"Trail率":>7} | {"SL率":>6} | {"N":>5}')
        print(f'  {"-"*70}')

        for sl_mult in SL_CANDIDATES:
            res = simulate_with_stage2(
                symbol, pair_cfg,
                stage2_activate=STAGE2_ACTIVATE_BASE,
                stage2_distance=STAGE2_DISTANCE_BASE,
                sl_atr_mult=sl_mult,
            )
            if res is None:
                print(f'  {sl_mult:>8} | データなし')
                continue
            row = {'symbol': symbol, 'sl_atr_mult': sl_mult,
                   'tp_sl_ratio': pair_cfg['tp_sl_ratio'], **res}
            rows.append(row)
            trades     = res['trades']
            tp_rate    = round(res['tp_count']    / trades * 100, 1)
            trail_rate = round(res['trail_count'] / trades * 100, 1)
            sl_rate    = round(res['sl_count']    / trades * 100, 1)
            print(f'  {sl_mult:>8.1f} | '
                  f'{res["pf"]:>6.3f} | '
                  f'{res["win_rate"]:>5.1f}% | '
                  f'{res["rr_actual"]:>6.3f} | '
                  f'{tp_rate:>5.1f}% | '
                  f'{trail_rate:>5.1f}% | '
                  f'{sl_rate:>5.1f}% | '
                  f'{trades:>5}')

    out = r'C:\Users\Administrator\fx_bot\optimizer\sl_bt_results.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\n出力: {out}')

    print('\n=== ペア別 最良sl_atr_mult ===')
    for symbol in BB_PAIRS_CFG:
        pair_rows = [r for r in rows if r['symbol'] == symbol]
        if not pair_rows:
            continue
        best = max(pair_rows, key=lambda x: x['pf'])
        print(f'  {symbol}: sl_mult={best["sl_atr_mult"]} '
              f'PF={best["pf"]} 勝率={best["win_rate"]}% '
              f'TP率={round(best["tp_count"]/best["trades"]*100,1)}% '
              f'Trail率={round(best["trail_count"]/best["trades"]*100,1)}% N={best["trades"]}')

def run_stage2_grid_backtest():
    print('=== Stage2グリッドサーチBT ===')
    rows = []
    for symbol, pair_cfg in BB_PAIRS_CFG.items():
        print(f'\n--- {symbol} ---')
        print(f'  {"sl_mult":>7} | {"s2_dist":>7} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"avg_exit":>8} | TP/Trail/SL | {"N":>5}')
        print('  ' + '-' * 75)

        for sl_mult in GRID_SL_CANDIDATES:
            for s2_dist in GRID_STAGE2_DISTANCES:
                res = simulate_with_stage2(
                    symbol, pair_cfg,
                    stage2_activate=GRID_STAGE2_ACTIVATE,
                    stage2_distance=s2_dist,
                    sl_atr_mult=sl_mult,
                )
                if res is None:
                    print(f'  {sl_mult:>7.1f} | {s2_dist:>7.2f} | データなし')
                    continue
                row = {
                    **res,
                    'symbol':          symbol,
                    'sl_atr_mult':     sl_mult,
                    'stage2_activate': GRID_STAGE2_ACTIVATE,
                    'stage2_distance': s2_dist,
                }
                rows.append(row)
                print(f'  {sl_mult:>7.1f} | {s2_dist:>7.2f} | '
                      f'{res["pf"]:>6.3f} | '
                      f'{res["win_rate"]:>5.1f}% | '
                      f'{res["rr_actual"]:>6.3f} | '
                      f'{res["avg_exit_pct"]:>+7.1f}%TP | '
                      f'{res["tp_count"]:>3}/{res["trail_count"]:>3}/{res["sl_count"]:>3} | '
                      f'{res["trades"]:>5}')

    print('\n=== ペア別 最良組み合わせ（PF基準）===')
    for symbol in BB_PAIRS_CFG:
        pair_rows = [r for r in rows if r['symbol'] == symbol and r['trades'] >= 20]
        if not pair_rows:
            continue
        best = max(pair_rows, key=lambda x: x['pf'])
        print(f'  {symbol}: sl_mult={best["sl_atr_mult"]} s2_dist={best["stage2_distance"]} '
              f'PF={best["pf"]} 勝率={best["win_rate"]}% '
              f'avg_exit={best["avg_exit_pct"]:+.1f}%TP N={best["trades"]}')

    out = r'C:\Users\Administrator\fx_bot\optimizer\stage2_grid_results.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\n出力: {out}')

DISTANCE_SWEEP_PAIRS    = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']
DISTANCE_SWEEP_VALUES   = [0.05, 0.1, 0.2, 0.3]
DISTANCE_SWEEP_ACTIVATE = 0.7

def run_distance_sweep():
    print('=== Stage2 distance sweep BT ===')
    rows = []
    for symbol in DISTANCE_SWEEP_PAIRS:
        pair_cfg = BB_PAIRS_CFG.get(symbol)
        if pair_cfg is None:
            print(f'[WARN] {symbol} not in BB_PAIRS_CFG, skip')
            continue
        print(f'\n--- {symbol} (sl_atr_mult={pair_cfg["sl_atr_mult"]}) ---')
        print(f'  {"distance":>8} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"avg_exit":>9} | TP/Trail/SL | {"N":>5}')
        print(f'  {"-"*65}')

        for dist in DISTANCE_SWEEP_VALUES:
            res = simulate_with_stage2(
                symbol, pair_cfg,
                stage2_activate=DISTANCE_SWEEP_ACTIVATE,
                stage2_distance=dist,
                sl_atr_mult=pair_cfg['sl_atr_mult'],
            )
            if res is None:
                print(f'  {dist:>8.2f} | データなし')
                continue
            row = {
                'symbol':          symbol,
                'stage2_distance': dist,
                'stage2_activate': DISTANCE_SWEEP_ACTIVATE,
                'sl_atr_mult':     pair_cfg['sl_atr_mult'],
                'tp_sl_ratio':     pair_cfg['tp_sl_ratio'],
                **res,
            }
            rows.append(row)
            print(f'  {dist:>8.2f} | '
                  f'{res["pf"]:>6.3f} | '
                  f'{res["win_rate"]:>5.1f}% | '
                  f'{res["rr_actual"]:>6.3f} | '
                  f'{res["avg_exit_pct"]:>+8.1f}%TP | '
                  f'{res["tp_count"]:>3}/{res["trail_count"]:>3}/{res["sl_count"]:>3} | '
                  f'{res["trades"]:>5}')

    if not rows:
        print('[ERROR] 結果なし')
        return

    out_csv = str(Path(__file__).parent.parent / 'data' / 'stage2_distance_bt.csv')
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    print('\n=== ペア別 PF比較表 ===')
    pivot_pf = df_out.pivot(index='symbol', columns='stage2_distance', values='pf')
    pivot_wr = df_out.pivot(index='symbol', columns='stage2_distance', values='win_rate')
    pivot_rr = df_out.pivot(index='symbol', columns='stage2_distance', values='rr_actual')
    pivot_n  = df_out.pivot(index='symbol', columns='stage2_distance', values='trades')
    header   = f'  {"pair":>7} | ' + ' | '.join(f'd={d:.2f}' for d in DISTANCE_SWEEP_VALUES)

    for label, pivot in [('[PF]', pivot_pf), ('[勝率%]', pivot_wr), ('[実RR]', pivot_rr), ('[取引数N]', pivot_n)]:
        print(f'\n{label}')
        print(header)
        for sym in DISTANCE_SWEEP_PAIRS:
            if sym not in pivot.index:
                continue
            if label == '[取引数N]':
                vals = ' | '.join(f'{int(pivot.loc[sym, d]):6d}' for d in DISTANCE_SWEEP_VALUES)
            elif label == '[勝率%]':
                vals = ' | '.join(f'{pivot.loc[sym, d]:5.1f}%' for d in DISTANCE_SWEEP_VALUES)
            else:
                vals = ' | '.join(f'{pivot.loc[sym, d]:6.3f}' for d in DISTANCE_SWEEP_VALUES)
            print(f'  {sym:>7} | {vals}')


# ========================================================
# ===== エントリーフィルターBT（新規追加）=====
# ========================================================

# フィルター条件定義
# 各フィルターはON/OFFで切り替え可能
# filter_cfg例:
#   {'hour': True,  'htf4h': False, 'adx': False}  → 時間帯のみON
#   {'hour': True,  'htf4h': True,  'adx': False}  → 時間帯+HTF4h
#   {'hour': True,  'htf4h': True,  'adx': True}   → 全フィルター

# フィルター組み合わせ一覧（単体→複合の順）
FILTER_COMBOS = [
    {'label': 'no_filter',       'hour': False, 'htf4h': False, 'adx': False},
    {'label': 'hour_only',       'hour': True,  'htf4h': False, 'adx': False},
    {'label': 'htf4h_only',      'hour': False, 'htf4h': True,  'adx': False},
    {'label': 'adx_only',        'hour': False, 'htf4h': False, 'adx': True},
    {'label': 'hour+htf4h',      'hour': True,  'htf4h': True,  'adx': False},
    {'label': 'hour+adx',        'hour': True,  'htf4h': False, 'adx': True},
    {'label': 'htf4h+adx',       'hour': False, 'htf4h': True,  'adx': True},
    {'label': 'all_filters',     'hour': True,  'htf4h': True,  'adx': True},
]


def simulate_with_filters(symbol, pair_cfg, filter_cfg, n_bars=5000):
    """
    エントリーフィルター付きBT（simulate_with_stage2ロジックベース）。
    filter_cfg: {'hour': bool, 'htf4h': bool, 'adx': bool}
    Stage2パラメータはBB_PAIRS_CFG準拠の現状設定を使用。
    戻り値: {'pf', 'win_rate', 'rr_actual', 'trades', 'avg_exit_pct',
              'tp_count', 'trail_count', 'sl_count'} or None
    """
    # 現在の稼働パラメータ（trail_monitor v10準拠）
    STAGE2_ACTIVATE = 0.70
    STAGE2_DISTANCE_MAP = {
        'GBPJPY': 0.3,
        'USDJPY': 0.3,
    }
    stage2_distance = STAGE2_DISTANCE_MAP.get(symbol, 0.3)

    cfg = get_base_params()
    cfg.update(pair_cfg)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    # フィルター用ルックアップ事前構築
    htf4h_lkp = build_htf4h_ema_lookup(df_1h) if filter_cfg.get('htf4h') else None
    adx_lkp   = build_adx_lookup(df_1h)       if filter_cfg.get('adx')   else None
    hour_list  = HOUR_FILTER_CANDIDATES.get(symbol, []) if filter_cfg.get('hour') else None

    spread    = 2 * cfg['pip_unit']
    close_arr = close.values
    n         = len(df_5m)

    wins = losses = tp_count = trail_count = sl_count = 0
    gross_profit = gross_loss = 0.0
    exit_pcts = []
    last_bar  = -cfg['cooldown_bars'] - 1

    for i in range(cfg['bb_period'] + 1, n):
        if i - last_bar < cfg['cooldown_bars']:
            continue

        c   = close_arr[i]
        sl  = atr.iloc[i] * cfg['sl_atr_mult']
        tp  = sl * cfg['tp_sl_ratio']
        if sl == 0 or np.isnan(sl) or np.isnan(c):
            continue

        dt = df_5m['datetime'].iloc[i]

        # --- 時間帯フィルター ---
        if hour_list is not None:
            if dt.hour not in hour_list:
                continue

        # --- HTF sigma（既存） ---
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # --- エントリー方向判定（BBタッチ+RSI） ---
        direction = None
        rsi_v = rsi.iloc[i]
        if np.isnan(rsi_v):
            continue
        if c <= bb_l.iloc[i] and rsi_v < cfg['rsi_buy_max']:
            direction = 'buy'
        elif c >= bb_u.iloc[i] and rsi_v > cfg['rsi_sell_min']:
            direction = 'sell'
        if direction is None:
            continue

        # --- HTF 4h EMA20フィルター ---
        if htf4h_lkp is not None:
            htf4h_idx = htf4h_lkp.index.searchsorted(dt, side='right') - 1
            if htf4h_idx < 0:
                continue
            htf4h_sig = htf4h_lkp.iloc[htf4h_idx]
            # +1=Buy許可 / -1=Sell許可
            if direction == 'buy'  and htf4h_sig != 1:
                continue
            if direction == 'sell' and htf4h_sig != -1:
                continue

        # --- ADXフィルター ---
        if adx_lkp is not None:
            adx_idx = adx_lkp.index.searchsorted(dt, side='right') - 1
            if adx_idx < 0:
                continue
            adx_val = adx_lkp.iloc[adx_idx]
            if np.isnan(adx_val) or adx_val <= 20:
                continue

        # --- 決済シミュレーション（Stage2トレーリングSL） ---
        entry    = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp  if direction == 'buy' else entry - tp
        sl_price = entry - sl  if direction == 'buy' else entry + sl
        tp_dist  = abs(tp_price - entry)

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
                    new_trail = mid - tp_dist * stage2_distance
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
                    new_trail = mid + tp_dist * stage2_distance
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

        if direction == 'buy':
            exit_pct = (exit_price - entry) / tp_dist * 100
        else:
            exit_pct = (entry - exit_price) / tp_dist * 100

        pnl = exit_price - entry if direction == 'buy' else entry - exit_price

        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)

        if hit == 'tp':
            tp_count += 1
        elif hit == 'trail_sl':
            trail_count += 1
        elif hit == 'sl':
            sl_count += 1
        exit_pcts.append(exit_pct)
        last_bar = i

    trades = wins + losses
    if trades == 0:
        return None

    return {
        'trades':       trades,
        'win_rate':     round(wins / trades * 100, 1),
        'pf':           round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual':    round(gross_profit / wins / (gross_loss / losses), 3) if wins > 0 and losses > 0 else 0.0,
        'avg_exit_pct': round(float(np.mean(exit_pcts)), 1),
        'tp_count':     tp_count,
        'trail_count':  trail_count,
        'sl_count':     sl_count,
    }


def run_filter_backtest():
    """
    エントリーフィルターBT。
    対象: GBPJPY, USDJPY
    フィルター: 単体(hour/htf4h/adx) + 組み合わせ
    出力: optimizer/filter_bt_results.csv + JSON
    """
    print('=== エントリーフィルターBT ===')
    print(f'対象ペア: {FILTER_BT_PAIRS}')
    print(f'フィルター組み合わせ: {len(FILTER_COMBOS)}パターン')

    rows = []

    for symbol in FILTER_BT_PAIRS:
        pair_cfg = BB_PAIRS_CFG.get(symbol)
        if pair_cfg is None:
            print(f'[WARN] {symbol} not in BB_PAIRS_CFG, skip')
            continue

        print(f'\n--- {symbol} ---')
        print(f'  {"label":>16} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"avg_exit":>9} | TP/Trail/SL | {"N":>5}')
        print(f'  {"-"*75}')

        for combo in FILTER_COMBOS:
            filter_cfg = {k: v for k, v in combo.items() if k != 'label'}
            res = simulate_with_filters(symbol, pair_cfg, filter_cfg)

            if res is None:
                print(f'  {combo["label"]:>16} | データなし / N不足')
                continue

            row = {
                'symbol':      symbol,
                'filter':      combo['label'],
                'hour':        combo['hour'],
                'htf4h':       combo['htf4h'],
                'adx':         combo['adx'],
                **res,
            }
            rows.append(row)

            trades = res['trades']
            tp_rate    = round(res['tp_count']    / trades * 100, 1)
            trail_rate = round(res['trail_count'] / trades * 100, 1)
            sl_rate    = round(res['sl_count']    / trades * 100, 1)
            print(f'  {combo["label"]:>16} | '
                  f'{res["pf"]:>6.3f} | '
                  f'{res["win_rate"]:>5.1f}% | '
                  f'{res["rr_actual"]:>6.3f} | '
                  f'{res["avg_exit_pct"]:>+8.1f}%TP | '
                  f'{tp_rate:>4.1f}%/{trail_rate:>4.1f}%/{sl_rate:>4.1f}% | '
                  f'{trades:>5}')

    if not rows:
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    # CSV出力
    out_dir = Path(r'C:\Users\Administrator\fx_bot\optimizer')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv  = str(out_dir / 'filter_bt_results.csv')
    out_json = str(out_dir / 'filter_bt_results.json')

    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\n出力: {out_csv}')
    print(f'出力: {out_json}')

    # サマリー：ペア別PF上位3（N>=20）
    print('\n=== ペア別 PF上位3（N>=20）===')
    for symbol in FILTER_BT_PAIRS:
        pair_rows = [r for r in rows if r['symbol'] == symbol and r['trades'] >= 20]
        if not pair_rows:
            print(f'  {symbol}: 有効結果なし')
            continue
        top3 = sorted(pair_rows, key=lambda x: x['pf'], reverse=True)[:3]
        print(f'  {symbol}:')
        for r in top3:
            print(f'    [{r["filter"]}] '
                  f'PF={r["pf"]} 勝率={r["win_rate"]}% '
                  f'実RR={r["rr_actual"]} N={r["trades"]}')

    # ベースライン（no_filter）との差分表示
    print('\n=== ベースライン比（PF差分）===')
    for symbol in FILTER_BT_PAIRS:
        base = next((r for r in rows if r['symbol'] == symbol and r['filter'] == 'no_filter'), None)
        if base is None:
            continue
        print(f'  {symbol} (base PF={base["pf"]}, N={base["trades"]}):')
        for r in rows:
            if r['symbol'] != symbol or r['filter'] == 'no_filter':
                continue
            dpf = r['pf'] - base['pf']
            dn  = r['trades'] - base['trades']
            print(f'    [{r["filter"]:>16}] '
                  f'PF={r["pf"]}({dpf:+.3f}) '
                  f'勝率={r["win_rate"]}% '
                  f'N={r["trades"]}({dn:+d})')


# ===== Stage2 distance グリッドサーチ最適化（全ペア対応） =====
STAGE2_OPT_PAIRS     = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD', 'EURJPY', 'AUDJPY']
STAGE2_OPT_DISTANCES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]
STAGE2_OPT_SL_MULTS  = [1.5, 2.0, 2.5, 3.0]
STAGE2_OPT_ACTIVATE  = 0.7
STAGE2_OPT_MIN_N     = 30


def run_stage2_opt():
    """
    BB戦略 stage2_distance x sl_atr_mult グリッドサーチ。
    対象: GBPJPY/USDJPY/EURUSD/GBPUSD/EURJPY/AUDJPY
    評価優先: PF > 勝率 > N（N<30は除外）
    出力: stage2_opt_results.csv + TRAIL_CONFIG更新案
    """
    print('=== BB stage2_distance 最適化グリッドサーチ ===')
    print(f'pairs    : {STAGE2_OPT_PAIRS}')
    print(f'distances: {STAGE2_OPT_DISTANCES}')
    print(f'sl_mults : {STAGE2_OPT_SL_MULTS}')
    print(f'activate : {STAGE2_OPT_ACTIVATE} (固定)')
    print(f'min_N    : {STAGE2_OPT_MIN_N}')

    all_rows = []

    for symbol in STAGE2_OPT_PAIRS:
        pair_cfg = BB_PAIRS_CFG.get(symbol)
        if pair_cfg is None:
            print(f'\n[WARN] {symbol} not in BB_PAIRS_CFG, skip')
            continue

        print(f'\n{"="*70}')
        print(f'  {symbol}  (bb_sigma={pair_cfg["bb_sigma"]} tp_sl_ratio={pair_cfg["tp_sl_ratio"]})')
        print(f'{"="*70}')
        hdr = (f'  {"sl_mult":>7} | {"s2_dist":>7} | {"PF":>6} | '
               f'{"勝率":>6} | {"実RR":>6} | {"avg_exit":>9} | '
               f'{"TP":>4}/{"Trail":>5}/{"SL":>4} | {"N":>5}')
        print(hdr)
        print('  ' + '-' * 77)

        pair_rows = []
        for sl_mult in STAGE2_OPT_SL_MULTS:
            for s2_dist in STAGE2_OPT_DISTANCES:
                res = simulate_with_stage2(
                    symbol, pair_cfg,
                    stage2_activate=STAGE2_OPT_ACTIVATE,
                    stage2_distance=s2_dist,
                    sl_atr_mult=sl_mult,
                )
                if res is None:
                    print(f'  {sl_mult:>7.1f} | {s2_dist:>7.2f} | データなし')
                    continue

                n = res['trades']
                n_mark = '' if n >= STAGE2_OPT_MIN_N else ' *'
                row = {
                    'symbol':          symbol,
                    'sl_atr_mult':     sl_mult,
                    'stage2_distance': s2_dist,
                    'stage2_activate': STAGE2_OPT_ACTIVATE,
                    **res,
                }
                all_rows.append(row)
                pair_rows.append(row)

                print(f'  {sl_mult:>7.1f} | {s2_dist:>7.2f} | '
                      f'{res["pf"]:>6.3f} | '
                      f'{res["win_rate"]:>5.1f}% | '
                      f'{res["rr_actual"]:>6.3f} | '
                      f'{res["avg_exit_pct"]:>+8.1f}%TP | '
                      f'{res["tp_count"]:>4}/{res["trail_count"]:>5}/{res["sl_count"]:>4} | '
                      f'{n:>5}{n_mark}')

        # ペア別推奨（N>=30）
        valid = [r for r in pair_rows if r['trades'] >= STAGE2_OPT_MIN_N]
        if valid:
            best = max(valid, key=lambda x: (x['pf'], x['win_rate']))
            print(f'\n  [{symbol}] 推奨: sl_mult={best["sl_atr_mult"]} '
                  f's2_dist={best["stage2_distance"]} '
                  f'PF={best["pf"]} 勝率={best["win_rate"]}% '
                  f'実RR={best["rr_actual"]} N={best["trades"]}')
        else:
            print(f'\n  [{symbol}] N<{STAGE2_OPT_MIN_N}のため推奨なし')

    if not all_rows:
        print('[ERROR] 結果なし。データを確認してください。')
        return

    # CSV出力
    out_dir = Path(__file__).parent
    out_csv = str(out_dir / 'stage2_opt_results.csv')
    df_out = pd.DataFrame(all_rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    # ===== TRAIL_CONFIG更新案 =====
    print('\n' + '=' * 70)
    print('  TRAIL_CONFIG 更新案（trail_monitor.py v10用）')
    print('  ※ コピペして使用してください')
    print('=' * 70)

    current_s2dist = {
        'BB_GBPJPY': 0.3,
        'BB_USDJPY': 0.3,
        'BB_EURJPY': 0.3,
        'BB_AUDJPY': 0.3,
        'BB_EURUSD': 0.1,
        'BB_GBPUSD': 0.3,
    }

    recommendations = {}
    for symbol in STAGE2_OPT_PAIRS:
        valid = [r for r in all_rows
                 if r['symbol'] == symbol and r['trades'] >= STAGE2_OPT_MIN_N]
        if not valid:
            recommendations[symbol] = None
            continue
        best = max(valid, key=lambda x: (x['pf'], x['win_rate']))
        recommendations[symbol] = best

    print('\nTRAIL_CONFIG = {')
    trail_key_map = {
        'GBPJPY': 'BB_GBPJPY',
        'USDJPY': 'BB_USDJPY',
        'EURJPY': 'BB_EURJPY',
        'AUDJPY': 'BB_AUDJPY',
        'EURUSD': 'BB_EURUSD',
        'GBPUSD': 'BB_GBPUSD',
    }
    for symbol in STAGE2_OPT_PAIRS:
        key  = trail_key_map[symbol]
        best = recommendations[symbol]
        if best is None:
            dist = current_s2dist.get(key, 0.3)
            comment = f'# N不足のため現状維持'
        else:
            dist    = best['stage2_distance']
            old_dist = current_s2dist.get(key, 0.3)
            change  = f'+{dist-old_dist:.2f}' if dist != old_dist else '変更なし'
            comment = (f'# PF={best["pf"]} 勝率={best["win_rate"]}% '
                       f'N={best["trades"]} (旧:{old_dist} -> 新:{dist}, {change})')
        print(f'    \'{key}\': '
              f'{{"stage2": True, "stage3_activate": 1.2, "stage3_distance": 0.8, '
              f'"stage2_distance": {dist}}},  {comment}')
    print('    ...')
    print('}')

    # ペア別サマリー表（PF上位5 per pair）
    print('\n' + '=' * 70)
    print('  ペア別 PF上位5（N>=30）')
    print('=' * 70)
    for symbol in STAGE2_OPT_PAIRS:
        valid = [r for r in all_rows
                 if r['symbol'] == symbol and r['trades'] >= STAGE2_OPT_MIN_N]
        if not valid:
            print(f'\n  {symbol}: 有効結果なし（N<{STAGE2_OPT_MIN_N}）')
            continue
        top5 = sorted(valid, key=lambda x: x['pf'], reverse=True)[:5]
        print(f'\n  {symbol}:')
        print(f'  {"sl_mult":>7} | {"s2_dist":>7} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"N":>5}')
        for r in top5:
            print(f'  {r["sl_atr_mult"]:>7.1f} | '
                  f'{r["stage2_distance"]:>7.2f} | '
                  f'{r["pf"]:>6.3f} | '
                  f'{r["win_rate"]:>5.1f}% | '
                  f'{r["rr_actual"]:>6.3f} | '
                  f'{r["trades"]:>5}')


# ===== ペア別グリッドサーチBT =====
PAIR_GRID_STAGE2_DISTANCES = [0.1, 0.2, 0.3]
PAIR_GRID_STAGE2_ACTIVATE  = 0.7
PAIR_GRID_MIN_N            = 30

_PAIR_GRID_DEFS = {
    'GBPUSD': {
        'base': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'tp_sl_ratio': 1.5},
        'filter_configs': [
            {'label': 'no_filter',  'use_htf4h': False, 'filter_type': None},
            {'label': 'f1_p3',      'use_htf4h': False, 'filter_type': 'F1', 'f1_param': 3},
            {'label': 'f1_p5',      'use_htf4h': False, 'filter_type': 'F1', 'f1_param': 5},
            {'label': 'htf4h_only', 'use_htf4h': True,  'filter_type': None},
        ],
        'sl_candidates': [1.0, 1.2, 1.5, 2.0],
    },
    'EURUSD': {
        'base': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'tp_sl_ratio': 1.5},
        'filter_configs': [
            {'label': 'htf4h_bw002',  'use_htf4h': True,  'bb_width_th': 0.0020},
            {'label': 'htf4h_bw0015', 'use_htf4h': True,  'bb_width_th': 0.0015},
            {'label': 'htf4h_bw0025', 'use_htf4h': True,  'bb_width_th': 0.0025},
            {'label': 'htf4h_nobw',   'use_htf4h': True,  'bb_width_th': None},
            {'label': 'no_filter',    'use_htf4h': False, 'bb_width_th': None},
        ],
        'sl_candidates': [1.2, 1.5, 2.0, 2.5],
    },
    'GBPJPY': {
        'base': {'is_jpy': True, 'pip_unit': 0.01, 'bb_sigma': 1.5, 'tp_sl_ratio': 1.5},
        'filter_configs': [
            {'label': 'F2andF1_p3', 'use_htf4h': True, 'filter_type': 'F2andF1', 'f1_param': 3, 'f2_param': 10.0},
            {'label': 'F2andF1_p5', 'use_htf4h': True, 'filter_type': 'F2andF1', 'f1_param': 5, 'f2_param': 10.0},
            {'label': 'F1only_p3',  'use_htf4h': True, 'filter_type': 'F1',      'f1_param': 3},
            {'label': 'htf4h_only', 'use_htf4h': True, 'filter_type': None},
        ],
        'sl_candidates': [2.5, 3.0, 3.5],
    },
    'USDJPY': {
        'base': {'is_jpy': True, 'pip_unit': 0.01, 'bb_sigma': 2.0, 'tp_sl_ratio': 1.5},
        'filter_configs': [
            {'label': 'f1_p3', 'use_htf4h': True, 'filter_type': 'F1', 'f1_param': 3},
            {'label': 'f1_p5', 'use_htf4h': True, 'filter_type': 'F1', 'f1_param': 5},
            {'label': 'f1_p7', 'use_htf4h': True, 'filter_type': 'F1', 'f1_param': 7},
        ],
        'sl_candidates': [2.5, 3.0, 3.5],
    },
}


def run_pair_grid_bt():
    """
    ペア別グリッドサーチBT。
    filter_config x sl_atr_mult x stage2_distance の全組み合わせを実行。
    出力: optimizer/pair_grid_results.csv
    """
    print('=== ペア別グリッドサーチBT ===')
    print(f'stage2_distances : {PAIR_GRID_STAGE2_DISTANCES}')
    print(f'stage2_activate  : {PAIR_GRID_STAGE2_ACTIVATE} (固定)')
    print(f'min_N for summary: {PAIR_GRID_MIN_N}')

    all_rows = []
    total_runs = sum(
        len(d['filter_configs']) * len(d['sl_candidates']) * len(PAIR_GRID_STAGE2_DISTANCES)
        for d in _PAIR_GRID_DEFS.values()
    )
    print(f'総実行数: {total_runs} runs\n')

    for symbol, grid_def in _PAIR_GRID_DEFS.items():
        n_runs = (len(grid_def['filter_configs'])
                  * len(grid_def['sl_candidates'])
                  * len(PAIR_GRID_STAGE2_DISTANCES))
        print(f'{"="*70}')
        print(f'  {symbol}  ({n_runs} runs)')
        print(f'{"="*70}')
        hdr = (f'  {"filter":>14} | {"sl":>4} | {"s2d":>4} | '
               f'{"PF":>6} | {"WR":>5} | {"RR":>5} | {"exit":>7} | '
               f'{"TP":>3}/{"Tr":>3}/{"SL":>3} | {"N":>5}')
        print(hdr)
        print('  ' + '-' * 75)

        for fc in grid_def['filter_configs']:
            label = fc['label']
            fc_params = {k: v for k, v in fc.items() if k != 'label'}
            for sl_mult in grid_def['sl_candidates']:
                for s2d in PAIR_GRID_STAGE2_DISTANCES:
                    pair_cfg = {**grid_def['base'], **fc_params}
                    res = simulate_with_stage2(
                        symbol, pair_cfg,
                        stage2_activate=PAIR_GRID_STAGE2_ACTIVATE,
                        stage2_distance=s2d,
                        sl_atr_mult=sl_mult,
                    )
                    if res is None:
                        continue
                    row = {
                        'symbol':          symbol,
                        'filter_label':    label,
                        'sl_atr_mult':     sl_mult,
                        'stage2_distance': s2d,
                        'pf':              res['pf'],
                        'win_rate':        res['win_rate'],
                        'rr_actual':       res['rr_actual'],
                        'avg_exit_pct':    res['avg_exit_pct'],
                        'tp_count':        res['tp_count'],
                        'trail_count':     res['trail_count'],
                        'sl_count':        res['sl_count'],
                        'trades':          res['trades'],
                    }
                    all_rows.append(row)
                    mark = ' *' if res['trades'] < PAIR_GRID_MIN_N else ''
                    print(f'  {label:>14} | {sl_mult:>4.1f} | {s2d:>4.2f} | '
                          f'{res["pf"]:>6.3f} | '
                          f'{res["win_rate"]:>4.1f}% | '
                          f'{res["rr_actual"]:>5.3f} | '
                          f'{res["avg_exit_pct"]:>+6.1f}%TP | '
                          f'{res["tp_count"]:>3}/{res["trail_count"]:>3}/{res["sl_count"]:>3} | '
                          f'{res["trades"]:>5}{mark}')

    if not all_rows:
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    out_csv = str(Path(__file__).parent / 'pair_grid_results.csv')
    df_out = pd.DataFrame(all_rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    print('\n' + '=' * 70)
    print(f'  PF>=1.2 かつ N>={PAIR_GRID_MIN_N} の組み合わせ')
    print('=' * 70)
    for symbol in _PAIR_GRID_DEFS:
        hits = [r for r in all_rows
                if r['symbol'] == symbol
                and r['pf'] >= 1.2
                and r['trades'] >= PAIR_GRID_MIN_N]
        if not hits:
            print(f'\n  {symbol}: 該当なし')
            continue
        hits_sorted = sorted(hits, key=lambda x: x['pf'], reverse=True)
        print(f'\n  {symbol}:')
        for r in hits_sorted:
            print(f'    [{r["filter_label"]}] '
                  f'sl={r["sl_atr_mult"]} s2d={r["stage2_distance"]} '
                  f'PF={r["pf"]} WR={r["win_rate"]}% '
                  f'RR={r["rr_actual"]} N={r["trades"]}')


# ===== メイン =====
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',    default='USDCAD')
    parser.add_argument('--output',    default=OUTPUT_FILE)
    parser.add_argument('--mode',      default='auto',
                        choices=['auto', 'candidates', 'suggestions'])
    parser.add_argument('--stage2',         action='store_true', help='Stage2パラメータ比較BT')
    parser.add_argument('--sl',             action='store_true', help='SL幅比較BT')
    parser.add_argument('--grid',           action='store_true', help='sl_atr_mult x stage2_distanceグリッドサーチ')
    parser.add_argument('--distance-sweep', action='store_true', help='Stage2 distance 0.05/0.1/0.2/0.3 ペア別比較')
    parser.add_argument('--filter-bt',      action='store_true', help='エントリーフィルターBT（GBPJPY/USDJPY）')
    parser.add_argument('--stage2-opt',     action='store_true', help='stage2_distance x sl_atr_mult グリッドサーチ（6ペア）')
    parser.add_argument('--pair-grid',      action='store_true', help='ペア別グリッドサーチBT')
    args = parser.parse_args()

    if args.stage2:
        run_stage2_backtest()
        return
    if args.sl:
        run_sl_backtest()
        return
    if args.grid:
        run_stage2_grid_backtest()
        return
    if args.distance_sweep:
        run_distance_sweep()
        return
    if args.filter_bt:
        run_filter_backtest()
        return
    if args.stage2_opt:
        run_stage2_opt()
        return
    if args.pair_grid:
        run_pair_grid_bt()
        return

    print('=== backtest.py Phase3 開始 ===')
    print(f'symbol={args.symbol}  mode={args.mode}  output={args.output}')

    use_candidates = (
        args.mode == 'candidates' or
        (args.mode == 'auto' and Path(CANDIDATES_FILE).exists())
    )

    if use_candidates:
        print('[モード] candidates.json（複合パラメータ）')
        results = run_backtest_from_candidates(args.symbol, args.output)
    else:
        print('[モード] suggestions.json（単一パラメータ・後方互換）')
        results = run_backtest_from_suggestions(args.symbol, args.output)

    valid = [r for r in results if r.get('result') not in ('BASELINE', 'baseline') and r.get('trades', 0) >= 20]
    if valid:
        print('\n--- PF上位5 ---')
        for r in sorted(valid, key=lambda x: x['pf'], reverse=True)[:5]:
            label = r.get('id') or f'{r.get("param")}={r.get("candidate")}'
            print(f'  {label}  PF={r["pf"]}  勝率={r["win_rate"]}%  N={r["trades"]}')


if __name__ == '__main__':
    main()