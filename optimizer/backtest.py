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
DATA_DIR         = r'C:\Users\Administrator\fx_bot\data'
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
# 変更後（v16実績値）
BB_PAIRS_CFG = {
    'GBPJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 1.5, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5},
    'EURJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 1.5, 'sl_atr_mult': 3.0, 'tp_sl_ratio': 1.5},
    'USDJPY': {'is_jpy': True,  'pip_unit': 0.01,   'bb_sigma': 2.0, 'sl_atr_mult': 3.0, 'tp_sl_ratio': 1.5},
    'EURUSD': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'sl_atr_mult': 1.5, 'tp_sl_ratio': 1.5},
    'GBPUSD': {'is_jpy': False, 'pip_unit': 0.0001, 'bb_sigma': 1.5, 'sl_atr_mult': 1.5, 'tp_sl_ratio': 1.5},
}

# ===== Stage2候補 =====
STAGE2_CANDIDATES = [
    {'label': 'current',  'activate': 0.70, 'distance': 1.00},  # 現状ベースライン
    {'label': 'D(0.75/0.45)', 'activate': 0.75, 'distance': 0.45},
    {'label': 'B(0.80/0.40)', 'activate': 0.80, 'distance': 0.40},
    {'label': 'C(0.85/0.30)', 'activate': 0.85, 'distance': 0.30},
]
GRID_SL_CANDIDATES       = [1.5, 2.0, 2.5, 3.0]
GRID_STAGE2_DISTANCES    = [0.1, 0.2, 0.3]
GRID_STAGE2_ACTIVATE     = 0.7   # 固定（activateはBT済みで変更なし）

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
        # NaN列除去・重複列除去
        df = df[[c for c in ['open','high','low','close','volume'] if c in df.columns]]
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.dropna(subset=['close'])
        df = df.sort_index()
        df = df.reset_index()  # datetimeを列に
        return df

    print(f'[WARN] CSVなし: {symbol} {tf}')
    return None# ===== インジケーター =====
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

# ===== HTFシグナル構築 =====
def build_htf_lookup(df_1h, htf_period, htf_sigma):
    close = df_1h['close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]  # 重複列対策
    ma        = close.rolling(htf_period).mean()
    std       = close.rolling(htf_period).std()
    sigma_pos = (close - ma) / std.replace(0, np.nan)
    result    = df_1h[['datetime']].copy()
    result['sigma_pos'] = sigma_pos.values
    return result.set_index('datetime')['sigma_pos']

# ===== F1モメンタムフィルター =====
def f1_ok(close_arr, i, direction, param):
    if i < param:
        return True
    diff = close_arr[i] - close_arr[i - param]
    if direction == 'buy':
        return diff < 0
    else:
        return diff > 0
"""
Stage2トレーリングSLシミュレーター。
- stage2_activate: TPまでの距離に対する到達率でStage2発動
- stage2_distance: 発動後のトレーリング幅（TP距離の割合）
戻り値: {'avg_exit_pct', 'win_rate', 'pf', 'trades', 'rr_actual'}
"""
def simulate_with_stage2(symbol, pair_cfg, stage2_activate, stage2_distance,
                          sl_atr_mult=None, n_bars=5000):
    cfg = get_base_params()
    cfg.update(pair_cfg)
    if sl_atr_mult is not None:
        cfg['sl_atr_mult'] = sl_atr_mult   # ← グリッドサーチ用上書き

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    # データ末尾n_bars件に絞る
    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close   = df_5m['close']
    bb_u, bb_l, bb_ma, bb_std = calc_bb(close, cfg['bb_period'], cfg['bb_sigma'])
    rsi     = calc_rsi(close, cfg['rsi_period'])
    atr     = calc_atr(df_5m, cfg['atr_period'])
    htf_lkp = build_htf_lookup(df_1h, cfg['htf_period'], cfg['htf_sigma'])

    spread    = 2 * cfg['pip_unit']  # 2pipsスプレッド固定
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

        # HTFフィルター
        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sp = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sp) or abs(htf_sp) >= cfg['htf_range_sigma']:
            continue

        # エントリー判定（BBタッチ+RSI）
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
        tp_dist  = abs(tp_price - entry)

        # Stage2トレーリングSL
        trail_sl   = sl_price
        activated  = False
        hit        = None
        exit_price = None

        for j in range(i + 1, min(i + 300, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]
            mid = (h + l) / 2.0

            if direction == 'buy':
                # Stage2発動チェック
                progress = (mid - entry) / tp_dist if tp_dist > 0 else 0
                if progress >= stage2_activate:
                    activated = True
                # トレーリングSL更新（発動後）
                if activated:
                    new_trail = mid - tp_dist * stage2_distance
                    if new_trail > trail_sl:
                        trail_sl = new_trail
                # SL/TP判定
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

        # 決済位置（TP距離の何%で決済したか）
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
        
        # hit種別カウント
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
        'trades':        trades,
        'win_rate':      round(wins / trades * 100, 1),
        'pf':            round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0,
        'rr_actual':     round(gross_profit / wins / (gross_loss / losses), 3) if wins > 0 and losses > 0 else 0.0,
        'avg_exit_pct':  round(float(np.mean(exit_pcts)), 1),
        'tp_count':      tp_count,
        'trail_count':   trail_count,
        'sl_count':      sl_count,
    }
# ===== コアBTロジック =====
def run_backtest(symbol, override_params):
    """
    override_paramsでベースパラメータを上書きしてBTを実行。
    単一でも複合でも対応。
    戻り値: {'pf', 'win_rate', 'rr_actual', 'trades', 'tp_reach_rate'} or None
    """
    cfg = get_base_params()
    cfg.update(override_params)

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    # インジケーター計算
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

        # HTFシグマ位置取得
        dt      = df_5m['datetime'].iloc[i]
        htf_idx = htf_lkp.index.searchsorted(dt, side='right') - 1
        if htf_idx < 0:
            continue
        htf_sigma_pos = htf_lkp.iloc[htf_idx]
        if np.isnan(htf_sigma_pos):
            continue

        # エントリー判定
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

        # 決済シミュレーション
        entry = c + spread if direction == 'buy' else c - spread
        tp_price = entry + tp if direction == 'buy' else entry - tp
        sl_price = entry - sl if direction == 'buy' else entry + sl
        hit = None

        for j in range(i + 1, min(i + 200, n)):
            h = df_5m['high'].iloc[j]
            l = df_5m['low'].iloc[j]
            if direction == 'buy':
                if l <= sl_price:
                    hit = 'sl'
                    break
                if h >= tp_price:
                    hit = 'tp'
                    break
            else:
                if h >= sl_price:
                    hit = 'sl'
                    break
                if l <= tp_price:
                    hit = 'tp'
                    break

        if hit == 'tp':
            wins += 1
            tp_reach += 1
            gross_profit += tp
        elif hit == 'sl':
            losses += 1
            gross_loss += sl
        else:
            continue  # タイムアウトはカウントしない

        last_trade_bar = i

    trades = wins + losses
    if trades == 0:
        return {'pf': 0.0, 'win_rate': 0.0, 'rr_actual': 0.0, 'trades': 0, 'tp_reach_rate': 0.0}

    pf           = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0
    win_rate     = round(wins / trades * 100, 1)
    rr_actual    = round(gross_profit / wins / (gross_loss / losses), 3) if wins > 0 and losses > 0 else 0.0
    tp_reach_rate = round(tp_reach / trades * 100, 1)

    return {
        'pf':            pf,
        'win_rate':      win_rate,
        'rr_actual':     rr_actual,
        'trades':        trades,
        'tp_reach_rate': tp_reach_rate,
    }

# ===== candidates.json対応（Phase2複合パラメータ） =====
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
        params = get_base_params()
        params.update(cand['params'])
        res = run_backtest(symbol, cand['params'])
        if res is None:
            print(f'  [{cand["id"]}] BT失敗')
            continue

        # 採用判定: PF>=1.0 かつ 取引数>=20
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
        print(f'    params={cand["params"]}')

    # 追記モード（ループ蓄積用）
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

# ===== suggestions.json対応（単一パラメータ・後方互換） =====
def run_backtest_from_suggestions(symbol, output_file):
    suggestions = load_suggestions()
    results     = []

    baseline = run_backtest(symbol, {})
    if baseline is None:
        print('[ERROR] CSVデータなし → 終了')
        return []

    print(f'[ベースライン] PF={baseline["pf"]}  勝率={baseline["win_rate"]}%  '
          f'RR={baseline["rr_actual"]}  取引数={baseline["trades"]}')
    results.append({
        'param': 'baseline', 'candidate': 'current', 'symbol': symbol, **baseline,
    })

    for sug in sorted(suggestions, key=lambda x: x.get('priority', 99)):
        param      = sug['param']
        cands      = sug.get('candidates', sug.get('values', []))
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
def run_stage2_backtest():
    """全ペア x 全Stage2候補の一括比較"""
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
    import json
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\n出力: {out}')
# backtest.py への追記パッチ
# run_stage2_backtest() の直後に追加する

# ===== SL幅比較用シミュレーター =====
def simulate_with_sl(symbol, pair_cfg, sl_atr_mult, n_bars=5000):
    """
    sl_atr_multを差し替えてシンプルBT（Stage2なし・TP/SL固定）。
    tp_sl_ratioはpair_cfg準拠。
    戻り値: {'pf', 'win_rate', 'rr_actual', 'tp_rate', 'trades'} or None
    """
    cfg = get_base_params()
    cfg.update(pair_cfg)
    cfg['sl_atr_mult'] = sl_atr_mult  # 上書き

    df_5m = load_csv(symbol, '5m')
    df_1h = load_csv(symbol, '1h')
    if df_5m is None or df_1h is None:
        return None

    df_5m = df_5m.tail(n_bars).reset_index(drop=True)

    close = df_5m['close']
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
            wins     += 1
            tp_count += 1
            gross_profit += tp
        elif hit == 'sl':
            losses   += 1
            sl_count += 1
            gross_loss += sl
        else:
            continue  # タイムアウト除外

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


# ===== SL幅比較BT =====
SL_CANDIDATES = [1.5, 2.0, 2.5, 3.0]

def run_sl_backtest():
    print('=== SL幅比較BT ===')
    # Stage2はactivate=0.7/distance=1.0（現状ベースライン）で固定
    STAGE2_ACTIVATE_BASE = 0.70
    STAGE2_DISTANCE_BASE = 1.00
    rows = []

    for symbol, pair_cfg in BB_PAIRS_CFG.items():
        print(f'\n--- {symbol} (tp_sl_ratio={pair_cfg["tp_sl_ratio"]}) ---')
        print(f'  {"sl_mult":>8} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"TP率":>6} | {"Trail率":>7} | {"SL率":>6} | {"N":>5}')
        print(f'  {"-"*70}')

        for sl_mult in SL_CANDIDATES:
            res = simulate_with_stage2(          # ← ここだけ変更
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
            trades = res['trades']
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

    # サマリー：ペア別PF最大
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
    """
    グリッドサーチ: sl_atr_mult x stage2_distance の全組み合わせ
    stage2_activateは0.7固定
    出力: stage2_grid_results.json
    """
    print('=== Stage2グリッドサーチBT ===')
    print(f'sl_atr_mult={GRID_SL_CANDIDATES}')
    print(f'stage2_distance={GRID_STAGE2_DISTANCES}  activate={GRID_STAGE2_ACTIVATE}(固定)')

    rows = []
    for symbol, pair_cfg in BB_PAIRS_CFG.items():
        print(f'\n--- {symbol} ---')
        header = f'  {"sl_mult":>7} | {"s2_dist":>7} | {"PF":>6} | {"勝率":>6} | {"実RR":>6} | {"avg_exit":>8} | TP/Trail/SL | {"N":>5}'
        print(header)
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

    # ペア別PF最良サマリー
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

# ===== Stage2 distance sweep =====
DISTANCE_SWEEP_PAIRS   = ['GBPJPY', 'USDJPY', 'EURUSD', 'GBPUSD']
DISTANCE_SWEEP_VALUES  = [0.05, 0.1, 0.2, 0.3]
DISTANCE_SWEEP_ACTIVATE = 0.7   # activate固定

def run_distance_sweep():
    """
    Stage2 distanceを0.05/0.1/0.2/0.3の4パターンでペア別BT。
    sl_atr_multはBB_PAIRS_CFG準拠（ペア別設定を尊重）。
    出力: data/stage2_distance_bt.csv
    """
    print('=== Stage2 distance sweep BT ===')
    print(f'pairs={DISTANCE_SWEEP_PAIRS}')
    print(f'distances={DISTANCE_SWEEP_VALUES}  activate={DISTANCE_SWEEP_ACTIVATE}(固定)')

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
                'symbol':           symbol,
                'stage2_distance':  dist,
                'stage2_activate':  DISTANCE_SWEEP_ACTIVATE,
                'sl_atr_mult':      pair_cfg['sl_atr_mult'],
                'tp_sl_ratio':      pair_cfg['tp_sl_ratio'],
                'pf':               res['pf'],
                'win_rate':         res['win_rate'],
                'rr_actual':        res['rr_actual'],
                'avg_exit_pct':     res['avg_exit_pct'],
                'tp_count':         res['tp_count'],
                'trail_count':      res['trail_count'],
                'sl_count':         res['sl_count'],
                'trades':           res['trades'],
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
        print('[ERROR] 結果なし。CSVデータを確認してください。')
        return

    out_csv = str(Path(__file__).parent.parent / 'data' / 'stage2_distance_bt.csv')
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_csv, index=False, encoding='utf-8')
    print(f'\n出力: {out_csv}')

    # 比較表（ペア x distance）
    print('\n=== ペア別 PF比較表 ===')
    pivot_pf = df_out.pivot(index='symbol', columns='stage2_distance', values='pf')
    pivot_wr = df_out.pivot(index='symbol', columns='stage2_distance', values='win_rate')
    pivot_rr = df_out.pivot(index='symbol', columns='stage2_distance', values='rr_actual')
    pivot_n  = df_out.pivot(index='symbol', columns='stage2_distance', values='trades')

    header = f'  {"pair":>7} | ' + ' | '.join(f'd={d:.2f}' for d in DISTANCE_SWEEP_VALUES)
    print(f'\n[PF]')
    print(header)
    for sym in DISTANCE_SWEEP_PAIRS:
        if sym not in pivot_pf.index:
            continue
        vals = ' | '.join(f'{pivot_pf.loc[sym, d]:6.3f}' for d in DISTANCE_SWEEP_VALUES)
        print(f'  {sym:>7} | {vals}')

    print(f'\n[勝率%]')
    print(header)
    for sym in DISTANCE_SWEEP_PAIRS:
        if sym not in pivot_wr.index:
            continue
        vals = ' | '.join(f'{pivot_wr.loc[sym, d]:5.1f}%' for d in DISTANCE_SWEEP_VALUES)
        print(f'  {sym:>7} | {vals}')

    print(f'\n[実RR]')
    print(header)
    for sym in DISTANCE_SWEEP_PAIRS:
        if sym not in pivot_rr.index:
            continue
        vals = ' | '.join(f'{pivot_rr.loc[sym, d]:6.3f}' for d in DISTANCE_SWEEP_VALUES)
        print(f'  {sym:>7} | {vals}')

    print(f'\n[取引数N]')
    print(header)
    for sym in DISTANCE_SWEEP_PAIRS:
        if sym not in pivot_n.index:
            continue
        vals = ' | '.join(f'{int(pivot_n.loc[sym, d]):6d}' for d in DISTANCE_SWEEP_VALUES)
        print(f'  {sym:>7} | {vals}')

# ===== メイン =====
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',    default='USDCAD')
    parser.add_argument('--output',    default=OUTPUT_FILE)
    parser.add_argument('--mode',      default='auto',
                        choices=['auto', 'candidates', 'suggestions'],
                        help='auto: candidates.jsonがあればそちら優先')
    
    parser.add_argument('--stage2', action='store_true', help='Stage2パラメータ比較BT')
    parser.add_argument('--sl',     action='store_true', help='SL幅比較BT')
    parser.add_argument('--grid',           action='store_true', help='sl_atr_mult x stage2_distanceグリッドサーチ')
    parser.add_argument('--distance-sweep', action='store_true', help='Stage2 distance 0.05/0.1/0.2/0.3 ペア別比較')
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
    print('=== backtest.py Phase3 開始 ===')
    print(f'symbol={args.symbol}  mode={args.mode}  output={args.output}')

    use_candidates = (
        args.mode == 'candidates' or
        (args.mode == 'auto' and Path(CANDIDATES_FILE).exists())
    )

    if use_candidates:
        print(f'[モード] candidates.json（複合パラメータ）')
        results = run_backtest_from_candidates(args.symbol, args.output)
    else:
        print(f'[モード] suggestions.json（単一パラメータ・後方互換）')
        results = run_backtest_from_suggestions(args.symbol, args.output)

    # サマリー
    valid = [r for r in results if r.get('result') not in ('BASELINE', 'baseline') and r.get('trades', 0) >= 20]
    if valid:
        print('\n--- PF上位5 ---')
        for r in sorted(valid, key=lambda x: x['pf'], reverse=True)[:5]:
            label = r.get('id') or f'{r.get("param")}={r.get("candidate")}'
            print(f'  {label}  PF={r["pf"]}  勝率={r["win_rate"]}%  N={r["trades"]}')

if __name__ == '__main__':
    main()