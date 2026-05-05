"""
MOM戦略 トレーリングストップ最適化バックテスト
- エントリー条件: daily_trade.py MOM_CONFIG のベストパラメータを使用（固定）
- 最適化対象: trail_monitor.py の stage2/stage3 パラメータ
- データ: D1 OHLC (yfinance 自動ダウンロード)
- 評価期間: 直近2年
"""
import os
import csv
import itertools
from datetime import datetime, timedelta

DATA_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mom_trail_bt_result.csv')

DOWNLOAD_YEARS = 3
ATR_PERIOD     = 14
MAX_HOLD_DAYS  = 10
MIN_TRADES     = 15
STAGE2_ACTIVATE = 0.7  # trail_monitor.py と同じ定数

# daily_trade.py MOM_CONFIG のベストパラメータ（固定）
MOM_CONFIG = {
    'MOM_JPY': {'symbol': 'USDJPY', 'filter_symbol': 'EURJPY',  'is_jpy': True,  'period': 10, 'mom_th': 0.015, 'filter_th': 0.005, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    'MOM_GBJ': {'symbol': 'GBPJPY', 'filter_symbol': 'USDJPY',  'is_jpy': True,  'period':  7, 'mom_th': 0.015, 'filter_th': 0.002, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    'MOM_ENZ': {'symbol': 'EURNZD', 'filter_symbol': 'EURUSD',  'is_jpy': False, 'period': 14, 'mom_th': 0.007, 'filter_th': 0.005, 'use_ema200_filter': False, 'monday_th_mult': 1.5},
    'MOM_ECA': {'symbol': 'EURCAD', 'filter_symbol': 'USDCAD',  'is_jpy': False, 'period':  7, 'mom_th': 0.015, 'filter_th': 0.002, 'use_ema200_filter': True,  'monday_th_mult': 1.5},
    'MOM_GBU': {'symbol': 'GBPUSD', 'filter_symbol': 'EURUSD',  'is_jpy': False, 'period': 10, 'mom_th': 0.007, 'filter_th': 0.002, 'use_ema200_filter': True,  'monday_th_mult': 1.0},
}

# risk_manager.py と同じ TP/SL 倍率
TP_SL_MULT = {
    'MOM_JPY': {'tp': 3.0, 'sl': 1.0},
    'MOM_GBJ': {'tp': 1.0, 'sl': 0.5},
}
DEFAULT_MULT = {'tp': 2.0, 'sl': 1.5}

# グリッドサーチパラメータ
STAGE2_VALS      = [True, False]
STAGE2_DIST_VALS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
STAGE3_ACT_VALS  = [0.5, 0.7, 0.8, 1.0, 1.2, 1.5]
STAGE3_DIST_VALS = [0.3, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2]


# ──────────────────────────────────────────
# データ取得・読み込み
# ──────────────────────────────────────────
def download_symbol(symbol):
    try:
        import yfinance as yf
    except ImportError:
        print('  [ERROR] yfinanceが未インストールです: pip install yfinance')
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    path   = os.path.join(DATA_DIR, f'{symbol}_D1.csv')
    ticker = f'{symbol}=X'
    end    = datetime.now()
    start  = end - timedelta(days=365 * DOWNLOAD_YEARS)
    print(f'  ダウンロード中: {ticker} ({start.date()} ~ {end.date()})')
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                         end=end.strftime('%Y-%m-%d'), interval='1d', progress=False)
        if df is None or len(df) == 0:
            print(f'  [WARN] データ取得0件: {ticker}')
            return None
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] for c in df.columns]
        df.index.name = 'Date'
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
        df.to_csv(path)
        print(f'  保存: {path} ({len(df)}件)')
        return path
    except Exception as e:
        print(f'  [ERROR] ダウンロード失敗 {ticker}: {e}')
        return None


def load_csv(symbol):
    path = os.path.join(DATA_DIR, f'{symbol}_D1.csv')
    if not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    'date':  datetime.strptime(row['Date'][:10], '%Y-%m-%d'),
                    'open':  float(row['Open']),
                    'high':  float(row['High']),
                    'low':   float(row['Low']),
                    'close': float(row['Close']),
                })
            except (ValueError, KeyError):
                continue
    rows.sort(key=lambda x: x['date'])
    return rows


# ──────────────────────────────────────────
# 指標計算
# ──────────────────────────────────────────
def calc_atr(rows, i, length=ATR_PERIOD):
    if i < length:
        return None
    trs = []
    for j in range(i - length + 1, i + 1):
        tr = max(
            rows[j]['high'] - rows[j]['low'],
            abs(rows[j]['high'] - rows[j - 1]['close']),
            abs(rows[j]['low']  - rows[j - 1]['close']),
        )
        trs.append(tr)
    return sum(trs) / length


def calc_ema200(rows, i, period=200):
    if i < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = rows[i - period]['close']
    for j in range(i - period + 1, i + 1):
        ema = alpha * rows[j]['close'] + (1 - alpha) * ema
    return ema


# ──────────────────────────────────────────
# エントリーシグナル生成（固定パラメータ）
# ──────────────────────────────────────────
def generate_trades(strategy_name, sym_rows, filter_rows):
    """
    MOM_CONFIG の固定パラメータでエントリーシグナルを生成。
    各トレードは (entry_i, direction, entry, sl, tp, sl_dist, tp_dist, atr_entry) を持つ。
    SL/TP は初期値（trailing前）。
    """
    cfg   = MOM_CONFIG[strategy_name]
    mult  = TP_SL_MULT.get(strategy_name, DEFAULT_MULT)
    period       = cfg['period']
    mom_th       = cfg['mom_th']
    filter_th    = cfg['filter_th']
    use_ema200   = cfg['use_ema200_filter']
    monday_mult  = cfg['monday_th_mult']

    cutoff = datetime.now() - timedelta(days=730)
    trades = []
    start_i = max(period + 1, ATR_PERIOD, 200)

    for i in range(start_i, len(sym_rows)):
        row = sym_rows[i]
        if row['date'] < cutoff:
            continue

        # フィルターシンボルの対応インデックスを探す
        fi = next((j for j in range(len(filter_rows) - 1, -1, -1)
                   if filter_rows[j]['date'] <= row['date']), None)
        if fi is None or fi < period:
            continue

        closes  = [sym_rows[k]['close']    for k in range(i - period, i + 1)]
        fcloses = [filter_rows[k]['close'] for k in range(fi - period, fi + 1)]

        mom  = (closes[-1]  - closes[0])  / closes[0]
        fmom = (fcloses[-1] - fcloses[0]) / fcloses[0]

        eff_mom_th = mom_th * monday_mult if row['date'].weekday() == 0 else mom_th

        if mom > eff_mom_th and fmom > filter_th:
            direction = 'buy'
        elif mom < -eff_mom_th and fmom < -filter_th:
            direction = 'sell'
        else:
            continue

        if use_ema200:
            ema200 = calc_ema200(sym_rows, i)
            if ema200 is not None:
                if direction == 'buy'  and row['close'] <= ema200:
                    continue
                if direction == 'sell' and row['close'] >= ema200:
                    continue

        atr = calc_atr(sym_rows, i)
        if atr is None or atr == 0:
            continue

        sl_dist = atr * mult['sl']
        tp_dist = atr * mult['tp']
        entry   = row['close']
        tp      = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl      = entry - sl_dist if direction == 'buy' else entry + sl_dist

        trades.append({
            'entry_i':  i,
            'direction': direction,
            'entry':    entry,
            'tp':       tp,
            'sl':       sl,
            'sl_dist':  sl_dist,
            'tp_dist':  tp_dist,
            'atr':      atr,
        })

    return trades


# ──────────────────────────────────────────
# トレーリングストップシミュレーション
# ──────────────────────────────────────────
def simulate_trailing(raw_trades, sym_rows, stage2, stage2_distance,
                      stage3_activate, stage3_distance):
    """
    raw_trades に対してトレーリングストップを適用し、PnL リストを返す。

    シミュレーション方針（D1バー単位）:
      1. バーの有利方向の extremum でトレーリングSLを更新
      2. TPヒット確認（有利extremum >= TP）
      3. SLヒット確認（不利extremum <= SL）
      TPとSLが同一バーで両方ヒットする場合: TP優先（バー内で上昇後に反落を想定）
    """
    results = []
    for trade in raw_trades:
        i         = trade['entry_i']
        direction = trade['direction']
        entry     = trade['entry']
        tp        = trade['tp']
        tp_dist   = trade['tp_dist']
        atr_entry = trade['atr']
        sl        = trade['sl']
        dir_sign  = 1 if direction == 'buy' else -1

        closed = False
        pnl    = None

        for j in range(i + 1, min(i + MAX_HOLD_DAYS + 1, len(sym_rows))):
            bar    = sym_rows[j]
            atr_j  = calc_atr(sym_rows, j)
            if atr_j is None:
                atr_j = atr_entry

            # バー内の有利/不利 extremum
            if direction == 'buy':
                fav_extreme  = bar['high']   # 有利方向の極値
                unfav_extreme = bar['low']   # 不利方向の極値
            else:
                fav_extreme  = bar['low']
                unfav_extreme = bar['high']

            profit_dist = (fav_extreme - entry) * dir_sign

            # Stage2: 小利益確定ライン
            if stage2 and profit_dist >= atr_j * STAGE2_ACTIVATE:
                s2_sl = entry + atr_j * stage2_distance * dir_sign
                if (s2_sl - sl) * dir_sign > 0:
                    sl = s2_sl

            # Stage3: トレーリング
            if profit_dist >= atr_j * stage3_activate:
                s3_sl = fav_extreme - atr_j * stage3_distance * dir_sign
                if (s3_sl - sl) * dir_sign > 0:
                    sl = s3_sl

            # TP判定（先にチェック）
            if (direction == 'buy'  and bar['high'] >= tp) or \
               (direction == 'sell' and bar['low']  <= tp):
                pnl    = tp_dist
                closed = True
                break

            # SL判定
            if (direction == 'buy'  and bar['low']  <= sl) or \
               (direction == 'sell' and bar['high'] >= sl):
                pnl    = (sl - entry) * dir_sign
                closed = True
                break

        if not closed:
            last_j   = min(i + MAX_HOLD_DAYS, len(sym_rows) - 1)
            last_bar = sym_rows[last_j]
            pnl      = (last_bar['close'] - entry) * dir_sign

        results.append(pnl)

    return results


# ──────────────────────────────────────────
# 評価指標
# ──────────────────────────────────────────
def evaluate(pnl_list):
    if not pnl_list:
        return None
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    gw     = sum(wins)
    gl     = abs(sum(losses))
    pf     = gw / gl if gl > 0 else 0.0
    wr     = len(wins) / len(pnl_list)
    return {'pf': round(pf, 4), 'wr': round(wr, 4), 'n': len(pnl_list)}


# ──────────────────────────────────────────
# グリッドサーチ
# ──────────────────────────────────────────
def grid_search(strategy_name, raw_trades, sym_rows):
    best     = None
    best_cfg = None

    # stage2=False の場合は stage2_distance は不要 → 1値のみ
    for stage2 in STAGE2_VALS:
        s2_dist_list = STAGE2_DIST_VALS if stage2 else [0.0]
        for stage2_dist, stage3_act, stage3_dist in itertools.product(
                s2_dist_list, STAGE3_ACT_VALS, STAGE3_DIST_VALS):

            pnl  = simulate_trailing(raw_trades, sym_rows, stage2, stage2_dist,
                                     stage3_act, stage3_dist)
            stat = evaluate(pnl)
            if stat is None or stat['n'] < MIN_TRADES:
                continue

            if best is None or stat['pf'] > best['pf'] or \
               (stat['pf'] == best['pf'] and stat['n'] > best['n']):
                best     = stat
                best_cfg = {
                    'stage2':          stage2,
                    'stage2_distance': stage2_dist,
                    'stage3_activate': stage3_act,
                    'stage3_distance': stage3_dist,
                }

    return best, best_cfg


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────
def main():
    print('MOM戦略 トレーリングストップ最適化バックテスト開始')

    # データ読み込み
    data_cache  = {}
    all_symbols = set()
    for info in MOM_CONFIG.values():
        all_symbols.add(info['symbol'])
        all_symbols.add(info['filter_symbol'])

    for sym in sorted(all_symbols):
        rows = load_csv(sym)
        if rows is None:
            print(f'  [WARN] データなし: {sym} → ダウンロード試行')
            download_symbol(sym)
            rows = load_csv(sym)
        if rows is None:
            print(f'  [ERROR] {sym} のデータ取得に失敗')
        else:
            data_cache[sym] = rows
            print(f'  データ読込: {sym} {len(rows)}件')

    print()

    best_results = {}
    csv_rows     = []

    for strategy_name, cfg in MOM_CONFIG.items():
        sym       = cfg['symbol']
        fsym      = cfg['filter_symbol']
        sym_rows  = data_cache.get(sym)
        filt_rows = data_cache.get(fsym)

        if sym_rows is None or filt_rows is None:
            print(f'[{strategy_name}] データ不足 → スキップ')
            continue

        raw_trades = generate_trades(strategy_name, sym_rows, filt_rows)
        print(f'[{strategy_name}] エントリー生成: {len(raw_trades)}件')

        if len(raw_trades) < MIN_TRADES:
            print(f'[{strategy_name}] サンプル数不足（n<{MIN_TRADES}）→ スキップ')
            continue

        best_stat, best_cfg = grid_search(strategy_name, raw_trades, sym_rows)

        if best_stat is None:
            print(f'[{strategy_name}] 有効パラメータなし → スキップ')
            continue

        best_results[strategy_name] = (best_stat, best_cfg)

        cfg_str = (
            f"stage2={best_cfg['stage2']}"
            + (f" stage2_distance={best_cfg['stage2_distance']}" if best_cfg['stage2'] else '')
            + f" stage3_activate={best_cfg['stage3_activate']}"
            + f" stage3_distance={best_cfg['stage3_distance']}"
        )
        print(f'\n=== {strategy_name} ({sym}) ===')
        print(f"Best: {cfg_str}")
        print(f"PF={best_stat['pf']:.3f} 勝率={best_stat['wr']:.1%} n={best_stat['n']}")

        csv_rows.append({
            'strategy':        strategy_name,
            'symbol':          sym,
            'stage2':          best_cfg['stage2'],
            'stage2_distance': best_cfg['stage2_distance'],
            'stage3_activate': best_cfg['stage3_activate'],
            'stage3_distance': best_cfg['stage3_distance'],
            'PF':              best_stat['pf'],
            'win_rate':        best_stat['wr'],
            'n':               best_stat['n'],
        })

    # CSV保存
    if csv_rows:
        fields = ['strategy', 'symbol', 'stage2', 'stage2_distance',
                  'stage3_activate', 'stage3_distance', 'PF', 'win_rate', 'n']
        with open(RESULT_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f'\n結果保存: {RESULT_PATH}')

    # TRAIL_CONFIG スニペット生成
    if best_results:
        print('\n' + '=' * 60)
        print('# TRAIL_CONFIG 追加スニペット（trail_monitor.py に貼り付け）')
        print('=' * 60)
        for strategy_name, (stat, cfg) in best_results.items():
            sym = MOM_CONFIG[strategy_name]['symbol']
            if cfg['stage2']:
                line = (
                    f"    '{strategy_name}':          "
                    f"{{'stage2': True,  'stage3_activate': {cfg['stage3_activate']}, "
                    f"'stage3_distance': {cfg['stage3_distance']},  "
                    f"'stage2_distance': {cfg['stage2_distance']}}},  "
                    f"# PF={stat['pf']:.3f} WR={stat['wr']:.1%} n={stat['n']}"
                )
                line2 = (
                    f"    '{strategy_name}_{sym}':   "
                    f"{{'stage2': True,  'stage3_activate': {cfg['stage3_activate']}, "
                    f"'stage3_distance': {cfg['stage3_distance']},  "
                    f"'stage2_distance': {cfg['stage2_distance']}}},  "
                    f"# 個別上書き用"
                )
            else:
                line = (
                    f"    '{strategy_name}':          "
                    f"{{'stage2': False, 'stage3_activate': {cfg['stage3_activate']}, "
                    f"'stage3_distance': {cfg['stage3_distance']}}},  "
                    f"# PF={stat['pf']:.3f} WR={stat['wr']:.1%} n={stat['n']}"
                )
                line2 = (
                    f"    '{strategy_name}_{sym}':   "
                    f"{{'stage2': False, 'stage3_activate': {cfg['stage3_activate']}, "
                    f"'stage3_distance': {cfg['stage3_distance']}}},  "
                    f"# 個別上書き用"
                )
            print(line)
            print(line2)

    print('\nバックテスト完了')


if __name__ == '__main__':
    main()
