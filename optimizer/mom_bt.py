"""
MOM戦略バックテスト & グリッドサーチ
- データ: C:/Users/Administrator/fx_bot/data/{symbol}_D1.csv (yfinance標準)
- 評価期間: 直近2年
- シグナル: モメンタム + フィルター通貨
- TP/SL: ATR(14)ベース RR=1.5固定、最大保有10日で強制クローズ
"""
import os
import csv
import itertools
from datetime import datetime, timedelta

DATA_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mom_bt_result.csv')

DOWNLOAD_YEARS = 3  # 直近何年分をダウンロードするか


def download_symbol(symbol):
    """yfinanceでD1データをダウンロードしてCSV保存。失敗時はNoneを返す。"""
    try:
        import yfinance as yf
    except ImportError:
        print('  [ERROR] yfinanceが未インストールです: pip install yfinance')
        return None

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f'{symbol}_D1.csv')
    ticker = f'{symbol}=X'
    end   = datetime.now()
    start = end - timedelta(days=365 * DOWNLOAD_YEARS)
    print(f'  ダウンロード中: {ticker} ({start.date()} ~ {end.date()})')
    try:
        df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                         end=end.strftime('%Y-%m-%d'), interval='1d', progress=False)
        if df is None or len(df) == 0:
            print(f'  [WARN] データ取得0件: {ticker}')
            return None
        # MultiIndexの場合はflatten
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

STRATEGIES = {
    'MOM_JPY': {'symbol': 'USDJPY', 'filter_symbol': 'EURJPY',  'is_jpy': True},
    'MOM_GBJ': {'symbol': 'GBPJPY', 'filter_symbol': 'USDJPY',  'is_jpy': True},
    'MOM_ENZ': {'symbol': 'EURNZD', 'filter_symbol': 'EURUSD',  'is_jpy': False},
    'MOM_ECA': {'symbol': 'EURCAD', 'filter_symbol': 'USDCAD',  'is_jpy': False},
    'MOM_GBU': {'symbol': 'GBPUSD', 'filter_symbol': 'EURUSD',  'is_jpy': False},
}

GRID = {
    'period':           [7, 10, 14, 20],
    'mom_th':           [0.005, 0.007, 0.010, 0.015],
    'filter_th':        [0.001, 0.002, 0.003, 0.005],
    'use_ema200_filter': [True, False],
    'monday_th_mult':   [1.0, 1.5],
}

RR            = 1.5
ATR_PERIOD    = 14
MAX_HOLD_DAYS = 10
MIN_TRADES    = 10
TOP_N         = 5


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


def calc_atr(rows, i, length=14):
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


def run_backtest(sym_rows, filter_rows, period, mom_th, filter_th,
                 use_ema200_filter, monday_th_mult):
    cutoff = datetime.now() - timedelta(days=730)
    trades = []
    open_trade = None

    for i in range(max(period + 1, ATR_PERIOD, 200), len(sym_rows)):
        row = sym_rows[i]
        if row['date'] < cutoff:
            continue

        # 強制クローズ判定
        if open_trade is not None:
            held = (row['date'] - open_trade['entry_date']).days
            hit_tp = (open_trade['direction'] == 'buy'  and row['high'] >= open_trade['tp']) or \
                     (open_trade['direction'] == 'sell' and row['low']  <= open_trade['tp'])
            hit_sl = (open_trade['direction'] == 'buy'  and row['low']  <= open_trade['sl']) or \
                     (open_trade['direction'] == 'sell' and row['high'] >= open_trade['sl'])

            if hit_tp:
                trades.append(open_trade['sl_dist'] * RR)
                open_trade = None
            elif hit_sl:
                trades.append(-open_trade['sl_dist'])
                open_trade = None
            elif held >= MAX_HOLD_DAYS:
                pnl = (row['close'] - open_trade['entry']) if open_trade['direction'] == 'buy' \
                      else (open_trade['entry'] - row['close'])
                trades.append(pnl)
                open_trade = None

        if open_trade is not None:
            continue

        # シグナル計算
        fi = next((j for j in range(len(filter_rows) - 1, -1, -1)
                   if filter_rows[j]['date'] <= row['date']), None)
        if fi is None or fi < period:
            continue

        closes  = [sym_rows[k]['close']    for k in range(i - period, i + 1)]
        fcloses = [filter_rows[k]['close'] for k in range(fi - period, fi + 1)]

        mom  = (closes[-1]  - closes[0])  / closes[0]
        fmom = (fcloses[-1] - fcloses[0]) / fcloses[0]

        # 月曜日は閾値を緩める
        eff_mom_th = mom_th * monday_th_mult if row['date'].weekday() == 0 else mom_th

        if mom > eff_mom_th and fmom > filter_th:
            direction = 'buy'
        elif mom < -eff_mom_th and fmom < -filter_th:
            direction = 'sell'
        else:
            continue

        # EMA200フィルター
        if use_ema200_filter:
            ema200 = calc_ema200(sym_rows, i)
            if ema200 is not None:
                if direction == 'buy'  and row['close'] <= ema200:
                    continue
                if direction == 'sell' and row['close'] >= ema200:
                    continue

        atr = calc_atr(sym_rows, i)
        if atr is None or atr == 0:
            continue

        sl_dist = atr
        tp_dist = atr * RR
        entry   = row['close']
        tp      = entry + tp_dist if direction == 'buy' else entry - tp_dist
        sl      = entry - sl_dist if direction == 'buy' else entry + sl_dist

        open_trade = {
            'direction':  direction,
            'entry':      entry,
            'tp':         tp,
            'sl':         sl,
            'sl_dist':    sl_dist,
            'entry_date': row['date'],
        }

    if not trades:
        return None

    wins     = [t for t in trades if t > 0]
    losses   = [t for t in trades if t <= 0]
    gross_w  = sum(wins)
    gross_l  = abs(sum(losses))
    pf       = gross_w / gross_l if gross_l > 0 else 0.0
    win_rate = len(wins) / len(trades)

    # Max Drawdown（累積PnLベース）
    equity   = 0.0
    peak     = 0.0
    max_dd   = 0.0
    for t in trades:
        equity += t
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        'PF':       round(pf, 4),
        'win_rate': round(win_rate, 4),
        'trades':   len(trades),
        'max_dd':   round(max_dd, 6),
    }


def main():
    print('MOM戦略バックテスト開始')

    # データ読み込み
    data_cache = {}
    all_symbols = set()
    for info in STRATEGIES.values():
        all_symbols.add(info['symbol'])
        all_symbols.add(info['filter_symbol'])

    for sym in sorted(all_symbols):
        rows = load_csv(sym)
        if rows is None:
            print(f'  [WARN] データなし: {sym} → ダウンロード試行')
            download_symbol(sym)
            rows = load_csv(sym)
        if rows is None:
            print(f'  [ERROR] {sym} のデータ取得に失敗しました')
        else:
            data_cache[sym] = rows
            print(f'  データ読込: {sym} {len(rows)}件')

    param_keys   = list(GRID.keys())
    param_values = list(GRID.values())
    all_combos   = list(itertools.product(*param_values))
    print(f'グリッド組み合わせ数: {len(all_combos)} × {len(STRATEGIES)}戦略')

    results = []

    for strategy_name, info in STRATEGIES.items():
        sym    = info['symbol']
        fsym   = info['filter_symbol']
        sym_rows    = data_cache.get(sym)
        filter_rows = data_cache.get(fsym)

        if sym_rows is None or filter_rows is None:
            print(f'  [{strategy_name}] データ不足 → スキップ')
            continue

        strat_results = []
        for combo in all_combos:
            params = dict(zip(param_keys, combo))
            r = run_backtest(
                sym_rows, filter_rows,
                params['period'], params['mom_th'], params['filter_th'],
                params['use_ema200_filter'], params['monday_th_mult'],
            )
            if r is None or r['trades'] < MIN_TRADES:
                continue
            strat_results.append({
                'strategy':         strategy_name,
                'period':           params['period'],
                'mom_th':           params['mom_th'],
                'filter_th':        params['filter_th'],
                'use_ema200_filter': params['use_ema200_filter'],
                'monday_th_mult':   params['monday_th_mult'],
                **r,
            })

        strat_results.sort(key=lambda x: x['PF'], reverse=True)
        top = strat_results[:TOP_N]

        print(f'\n[{strategy_name}] {sym}/{fsym} 上位{len(top)}件:')
        for row in top:
            print(
                f"  PF={row['PF']:.3f} WR={row['win_rate']:.1%} n={row['trades']:3d}"
                f" period={row['period']} mom_th={row['mom_th']}"
                f" filter_th={row['filter_th']} ema200={row['use_ema200_filter']}"
                f" mon_mult={row['monday_th_mult']}"
            )

        results.extend(top)

    # CSV保存
    if results:
        fieldnames = ['strategy', 'period', 'mom_th', 'filter_th',
                      'use_ema200_filter', 'monday_th_mult', 'PF', 'win_rate', 'trades', 'max_dd']
        with open(RESULT_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f'\n結果保存: {RESULT_PATH} ({len(results)}件)')
    else:
        print('\n[WARN] 有効な結果なし（データ不足の可能性）')

    print('バックテスト完了')


if __name__ == '__main__':
    main()
