"""
shiba_inu_fx_bt.py - 柴犬式FX理論 バックテスト

戦略概要:
    SMA75とSMA200のクロスを起点に、エリオット波動の第1〜第3波を定義。
    第2波がSMA75に実体でタッチした後、第1波高値をブレイクするタイミングでエントリー。
    フィボナッチ0.764倍でSL/TPを設定し、RR1:1を維持する。

波動定義:
    第1波: MAクロス後のトレンド方向への初動
    第2波: 第1波極値(wave1_peak)からSMA75への調整戻し
    第3波: 第2波がSMA75で反発し、wave1_peakをブレイク → エントリー

エントリー条件:
    ロング (GC後): 価格がSMA75に実体タッチ → wave1_high ブレイクで逆指値買い
    ショート(DC後): 価格がSMA75に実体タッチ → wave1_low  ブレイクで逆指値売り

エグジット:
    D = |wave1_peak - wave2_extreme|  (wave2_extreme = 第2波の最深ヒゲ先)
    ロング: SL = エントリー - D×0.764 / TP = エントリー + D×0.764
    ショート: SL = エントリー + D×0.764 / TP = エントリー - D×0.764
    ※ RR = 1:1

フィルター:
    第2波フェーズで、SMA75にヒゲが届いたが実体タッチせず反発する動きが
    2回発生した場合は無効化 → 次のMAクロスまでスキップ

IS/OOS分割:
    IS: データ前半 / OOS: データ後半 (--is-split で比率調整可)

実行:
    python optimizer/shiba_inu_fx_bt.py
    python optimizer/shiba_inu_fx_bt.py --pairs USDJPY GBPJPY NZDUSD
    python optimizer/shiba_inu_fx_bt.py --pairs GBPJPY --spread 0.03
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

FIB_MULT = 0.764  # SL/TP計算係数（フィボナッチ比率）


# ──────────────────────────────────────────────────────────────────────────────
# データロード
# ──────────────────────────────────────────────────────────────────────────────

def load_ohlc(pair: str, tf: str = '1h') -> pd.DataFrame:
    # 10年モード: _1h_10y.csv を優先
    if tf == '1h':
        path_10y = DATA_DIR / f'{pair}_1h_10y.csv'
        if path_10y.exists():
            path = path_10y
        else:
            path = DATA_DIR / f'{pair}_{tf}.csv'
    else:
        path = DATA_DIR / f'{pair}_{tf}.csv'

    if not path.exists():
        raise FileNotFoundError(f"Data not found: {path}")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    df = df[['open', 'high', 'low', 'close']].copy()
    df.dropna(inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep='first')]
    # タイムゾーン正規化 (UTC naive に統一)
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_convert('UTC').tz_localize(None)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# インジケーター
# ──────────────────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['sma75'] = df['close'].rolling(75, min_periods=75).mean()
    df['sma200'] = df['close'].rolling(200, min_periods=200).mean()

    prev_diff = (df['sma75'] - df['sma200']).shift(1)
    curr_diff = df['sma75'] - df['sma200']

    df['cross'] = 0
    df.loc[(prev_diff <= 0) & (curr_diff > 0), 'cross'] = 1   # ゴールデンクロス
    df.loc[(prev_diff >= 0) & (curr_diff < 0), 'cross'] = -1  # デッドクロス

    return df


# ──────────────────────────────────────────────────────────────────────────────
# バックテスト本体
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, pair: str, spread: float = 0.0) -> tuple[list[dict], dict]:
    """
    柴犬式FX理論のバックテスト。

    Parameters
    ----------
    df     : OHLCデータ（indicators追加済み）
    pair   : ペア名
    spread : スプレッド（価格単位、往復コストとして差し引き）

    Returns
    -------
    trades     : トレード記録のリスト
    statistics : セットアップ統計
    """

    # 状態定義
    IDLE = 0         # MAクロス待ち
    SETUP = 1        # クロス後: 第1波追跡 + 第2波SMA75タッチ待ち
    ENTRY_WATCH = 2  # 第2波確定: wave1_peak ブレイクアウト待ち
    IN_TRADE = 3     # ポジション保有中
    INVALIDATED = 4  # 無効化: 次クロスまでスキップ

    state = IDLE
    direction = 0        # 1=ロング, -1=ショート
    wave1_peak = None    # 第1波の極値 (ロング:最高値ヒゲ先, ショート:最安値ヒゲ先)
    wave2_extreme = None # 第2波の最深点 (ロング:最安値ヒゲ先, ショート:最高値ヒゲ先)
    failed_approaches = 0
    entry_price = None
    sl = None
    tp = None
    entry_time = None
    entry_bar = None
    cross_time = None

    trades = []

    stats = {
        'total_crosses': 0,
        'setups_invalidated': 0,
        'setups_completed': 0,   # Wave2 SMA75タッチ確定数
        'entries_triggered': 0,
        'setups_no_entry': 0,    # Wave2確定後、エントリーなし（次クロスでリセット）
    }

    arr_o = df['open'].values
    arr_h = df['high'].values
    arr_l = df['low'].values
    arr_c = df['close'].values
    arr_s75 = df['sma75'].values
    arr_cross = df['cross'].values
    times = df.index

    def reset_to_cross(new_dir: int, h_val: float, l_val: float, t):
        nonlocal state, direction, wave1_peak, wave2_extreme, failed_approaches, cross_time
        # 既存セットアップのカウント
        if state in (ENTRY_WATCH,):
            stats['setups_no_entry'] += 1
        state = SETUP
        direction = new_dir
        wave1_peak = h_val if new_dir == 1 else l_val
        wave2_extreme = None
        failed_approaches = 0
        cross_time = t
        stats['total_crosses'] += 1

    for i in range(1, len(df)):
        o = arr_o[i]
        h = arr_h[i]
        l = arr_l[i]
        c = arr_c[i]
        s75 = arr_s75[i]
        cross_val = int(arr_cross[i])
        t = times[i]

        if np.isnan(s75):
            continue

        # ── IDLE ──────────────────────────────────────────────────────────────
        if state == IDLE:
            if cross_val != 0:
                reset_to_cross(cross_val, h, l, t)

        # ── INVALIDATED ───────────────────────────────────────────────────────
        elif state == INVALIDATED:
            if cross_val != 0:
                reset_to_cross(cross_val, h, l, t)

        # ── SETUP: 第1波追跡 + 第2波SMA75実体タッチ待ち ─────────────────────
        elif state == SETUP:
            # 逆方向クロス → リセット
            if cross_val != 0 and cross_val != direction:
                stats['setups_no_entry'] += 1
                reset_to_cross(cross_val, h, l, t)
                continue

            body_low = min(o, c)
            body_high = max(o, c)

            if direction == 1:  # ロング
                # 第1波高値を更新（ランニングmax）
                wave1_peak = max(wave1_peak, h)

                # 第2波フェーズ: wave1_peakから下落中
                in_wave2 = (c < wave1_peak) and (h < wave1_peak)

                # 第2波 SMA75実体タッチ確認
                # 条件: 実体の低い方がSMA75以下 かつ 上方ブレイクしていない
                if body_low <= s75 and h < wave1_peak and wave1_peak > s75:
                    wave2_extreme = l  # 第2波終点ヒゲ先
                    state = ENTRY_WATCH
                    stats['setups_completed'] += 1

                # 失敗アプローチ: ヒゲはSMA75に届くが実体は届かない
                elif in_wave2 and wave1_peak > s75:
                    if l <= s75 and body_low > s75:
                        failed_approaches += 1
                        if failed_approaches >= 2:
                            state = INVALIDATED
                            stats['setups_invalidated'] += 1

            else:  # ショート
                # 第1波安値を更新（ランニングmin）
                wave1_peak = min(wave1_peak, l)

                in_wave2 = (c > wave1_peak) and (l > wave1_peak)

                if body_high >= s75 and l > wave1_peak and wave1_peak < s75:
                    wave2_extreme = h
                    state = ENTRY_WATCH
                    stats['setups_completed'] += 1

                elif in_wave2 and wave1_peak < s75:
                    if h >= s75 and body_high < s75:
                        failed_approaches += 1
                        if failed_approaches >= 2:
                            state = INVALIDATED
                            stats['setups_invalidated'] += 1

        # ── ENTRY_WATCH: wave1_peak ブレイクアウト待ち ────────────────────────
        elif state == ENTRY_WATCH:
            # 逆方向クロス → リセット
            if cross_val != 0 and cross_val != direction:
                stats['setups_no_entry'] += 1
                reset_to_cross(cross_val, h, l, t)
                continue

            # 第2波最深点を更新（価格が継続して下落/上昇する場合）
            if direction == 1:
                wave2_extreme = min(wave2_extreme, l)
            else:
                wave2_extreme = max(wave2_extreme, h)

            # エントリーブレイクアウト判定
            if direction == 1:
                if h >= wave1_peak:
                    entry_price = wave1_peak
                    D = entry_price - wave2_extreme
                    if D <= 0:
                        state = IDLE
                        continue
                    sl = entry_price - D * FIB_MULT
                    tp = entry_price + D * FIB_MULT
                    state = IN_TRADE
                    entry_time = t
                    entry_bar = i
                    stats['entries_triggered'] += 1
            else:
                if l <= wave1_peak:
                    entry_price = wave1_peak
                    D = wave2_extreme - entry_price
                    if D <= 0:
                        state = IDLE
                        continue
                    sl = entry_price + D * FIB_MULT
                    tp = entry_price - D * FIB_MULT
                    state = IN_TRADE
                    entry_time = t
                    entry_bar = i
                    stats['entries_triggered'] += 1

        # ── IN_TRADE: SL/TP監視 ───────────────────────────────────────────────
        elif state == IN_TRADE:
            if direction == 1:
                sl_hit = l <= sl
                tp_hit = h >= tp
                if sl_hit and tp_hit:
                    exit_price, result = sl, 'SL'  # 同一バー内はSL優先(保守)
                elif sl_hit:
                    exit_price, result = sl, 'SL'
                elif tp_hit:
                    exit_price, result = tp, 'TP'
                else:
                    continue

                pnl_price = exit_price - entry_price
                pnl_net = pnl_price - spread

            else:
                sl_hit = h >= sl
                tp_hit = l <= tp
                if sl_hit and tp_hit:
                    exit_price, result = sl, 'SL'
                elif sl_hit:
                    exit_price, result = sl, 'SL'
                elif tp_hit:
                    exit_price, result = tp, 'TP'
                else:
                    continue

                pnl_price = entry_price - exit_price
                pnl_net = pnl_price - spread

            trades.append({
                'pair': pair,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'entry_time': str(entry_time)[:19],
                'exit_time': str(t)[:19],
                'entry': round(entry_price, 5),
                'exit': round(exit_price, 5),
                'sl': round(sl, 5),
                'tp': round(tp, 5),
                'D': round(abs(tp - entry_price) / FIB_MULT, 5),
                'pnl_price': round(pnl_price, 5),
                'pnl_net': round(pnl_net, 5),
                'result': result,
                'bars_held': i - entry_bar,
            })
            state = IDLE

    return trades, stats


# ──────────────────────────────────────────────────────────────────────────────
# ヘルパー：pip換算
# ──────────────────────────────────────────────────────────────────────────────

def to_pip(price_val: float, pair: str) -> float:
    """価格単位をpipに変換 (JPY: ×100, その他: ×10000)"""
    return price_val * (100 if 'JPY' in pair else 10000)


# ──────────────────────────────────────────────────────────────────────────────
# 結果集計
# ──────────────────────────────────────────────────────────────────────────────

def calc_metrics(trades: list[dict], pair: str = '') -> dict:
    if not trades:
        return dict(pair=pair, n=0, win_rate=0.0, pf=0.0, avg_win_p=0.0,
                    avg_loss_p=0.0, total_pnl=0.0, max_dd=0.0, sharpe=0.0,
                    expectancy=0.0, avg_bars=0.0)

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wins = tdf[tdf['result'] == 'TP']
    losses = tdf[tdf['result'] == 'SL']

    win_rate = len(wins) / n
    gross_win = wins['pnl_net'].sum()
    gross_loss = abs(losses['pnl_net'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')

    avg_win_p = wins['pnl_price'].mean() if len(wins) > 0 else 0.0
    avg_loss_p = losses['pnl_price'].mean() if len(losses) > 0 else 0.0
    total_pnl = tdf['pnl_net'].sum()
    expectancy = tdf['pnl_net'].mean()
    avg_bars = tdf['bars_held'].mean()

    cum_pnl = tdf['pnl_net'].cumsum()
    running_max = cum_pnl.cummax()
    max_dd = (cum_pnl - running_max).min()
    sharpe = expectancy / tdf['pnl_net'].std() if tdf['pnl_net'].std() > 0 else 0.0

    return dict(pair=pair, n=n, win_rate=win_rate, pf=pf,
                avg_win_p=avg_win_p, avg_loss_p=avg_loss_p,
                total_pnl=total_pnl, max_dd=max_dd, sharpe=sharpe,
                expectancy=expectancy, avg_bars=avg_bars)


# ──────────────────────────────────────────────────────────────────────────────
# 結果レポート
# ──────────────────────────────────────────────────────────────────────────────

def print_report(all_trades: list[dict], all_stats: dict, all_metrics: list[dict]):
    W = 80
    print("\n" + "=" * W)
    print("  柴犬式FX理論 バックテスト結果")
    print("=" * W)

    # ── ペア別サマリー ────────────────────────────────────────────────────────
    print(f"\n{'Pair':<10} {'N':>4} {'WR%':>6} {'PF':>5} "
          f"{'Exp(pip)':>9} {'MaxDD(pip)':>11} {'Sharpe':>7} {'AvgBars':>8}")
    print("-" * W)

    combined_trades = []
    for m in all_metrics:
        pair = m['pair']
        exp_pip = to_pip(m['expectancy'], pair)
        dd_pip = to_pip(m['max_dd'], pair)
        print(f"{pair:<10} {m['n']:>4} {m['win_rate']*100:>5.1f}% {m['pf']:>5.2f} "
              f"{exp_pip:>+9.2f} {dd_pip:>+11.2f} {m['sharpe']:>7.3f} {m['avg_bars']:>8.1f}")
        combined_trades.extend([t for t in all_trades if t['pair'] == pair])

    if combined_trades:
        print("-" * W)
        ov = calc_metrics(combined_trades, 'ALL')
        exp_pip = sum(to_pip(t['pnl_net'], t['pair']) for t in combined_trades) / len(combined_trades)
        dd_pip_all = None  # 異種通貨の合算DDは参考値
        print(f"{'ALL':<10} {ov['n']:>4} {ov['win_rate']*100:>5.1f}% {ov['pf']:>5.2f} "
              f"{exp_pip:>+9.2f} {'---':>11} {ov['sharpe']:>7.3f} {ov['avg_bars']:>8.1f}")

    # ── IS / OOS 分割 ─────────────────────────────────────────────────────────
    # 10年データ(2016〜)の場合: IS=2016-2021(6年)/OOS=2022-2026(4年+)
    # 2年データの場合: 前半/後半 分割
    all_years = [t['entry_time'][:4] for t in all_trades if all_trades]
    use_calendar_split = all_trades and min(all_years) <= '2020'
    if use_calendar_split:
        is_end = '2022'
        split_label = f'IS/OOS 分割 (IS=2016-2021 / OOS=2022-2026)'
    else:
        split_label = 'IS/OOS 分割 (前半/後半)'

    print(f"\n--- {split_label}")
    print(f"{'Pair':<10} {'IS-WR%':>7} {'IS-PF':>6} {'IS-n':>5} {'OOS-WR%':>8} {'OOS-PF':>7} {'OOS-n':>6}")
    print("-" * W)

    for pair in [m['pair'] for m in all_metrics]:
        pair_trades = [t for t in all_trades if t['pair'] == pair]
        if not pair_trades:
            continue
        if use_calendar_split:
            is_t = [t for t in pair_trades if t['entry_time'][:4] < is_end]
            oos_t = [t for t in pair_trades if t['entry_time'][:4] >= is_end]
        else:
            mid = len(pair_trades) // 2
            is_t = pair_trades[:mid]
            oos_t = pair_trades[mid:]
        is_m = calc_metrics(is_t, pair)
        oos_m = calc_metrics(oos_t, pair)
        print(f"{pair:<10} {is_m['win_rate']*100:>6.1f}% {is_m['pf']:>6.2f} {is_m['n']:>5} "
              f"{oos_m['win_rate']*100:>7.1f}% {oos_m['pf']:>7.2f} {oos_m['n']:>6}")

    # ── セットアップファネル ────────────────────────────────────────────────────
    print(f"\n--- セットアップファネル")
    print(f"{'Pair':<10} {'Crosses':>8} {'Wave2OK':>8} "
          f"{'Entered':>8} {'Entry%':>7} {'Invldtd':>8} {'NoEntry':>8}")
    print("-" * W)

    for pair, s in all_stats.items():
        e_rate = s['entries_triggered'] / s['setups_completed'] * 100 if s['setups_completed'] > 0 else 0
        print(f"{pair:<10} {s['total_crosses']:>8} {s['setups_completed']:>8} "
              f"{s['entries_triggered']:>8} {e_rate:>6.0f}% {s['setups_invalidated']:>8} {s['setups_no_entry']:>8}")

    # ── 年別勝率 ──────────────────────────────────────────────────────────────
    if combined_trades:
        tdf = pd.DataFrame(combined_trades)
        tdf['year'] = tdf['entry_time'].str[:4].astype(int)
        print(f"\n--- 年別成績 (全ペア合算)")
        print(f"{'Year':<6} {'N':>4} {'WR%':>7} {'PF':>6}")
        print("-" * 30)
        for yr, g in tdf.groupby('year'):
            wr = (g['result'] == 'TP').mean()
            wins_g = g[g['result'] == 'TP']['pnl_net'].sum()
            loss_g = abs(g[g['result'] == 'SL']['pnl_net'].sum())
            pf_yr = wins_g / loss_g if loss_g > 0 else float('inf')
            print(f"{yr:<6} {len(g):>4} {wr*100:>6.1f}% {pf_yr:>6.2f}")

    # ── パフォーマンス上位ペア ─────────────────────────────────────────────────
    print(f"\n--- パフォーマンスランキング (PF順)")
    sorted_m = sorted(all_metrics, key=lambda x: x['pf'], reverse=True)
    for rank, m in enumerate(sorted_m, 1):
        pair = m['pair']
        exp_p = to_pip(m['expectancy'], pair)
        verdict = "◎" if m['pf'] >= 1.3 and m['win_rate'] >= 0.55 else \
                  "○" if m['pf'] >= 1.1 else "△" if m['pf'] >= 0.9 else "×"
        print(f"  {rank}. {pair:<8} PF={m['pf']:.2f} WR={m['win_rate']:.0%} "
              f"exp={exp_p:+.2f}pip n={m['n']} {verdict}")

    # ── トレード詳細 (上位/下位ペアのみ) ─────────────────────────────────────
    best_pairs = [m['pair'] for m in sorted_m[:3] if m['pf'] > 1.0]
    if best_pairs:
        print(f"\n--- トレード詳細 (PF上位: {', '.join(best_pairs)})")
        hdr = f"{'Pair':<8} {'Dir':<5} {'EntryTime':<18} {'ExitTime':<18} " \
              f"{'Entry':>8} {'TP':>8} {'SL':>8} {'R':<3} {'PnL(pip)':>9} {'Bars':>5}"
        print(hdr)
        print("-" * W)
        filtered = [t for t in combined_trades if t['pair'] in best_pairs]
        for t in sorted(filtered, key=lambda x: x['entry_time']):
            pip_pnl = to_pip(t['pnl_net'], t['pair'])
            print(f"{t['pair']:<8} {t['direction']:<5} "
                  f"{t['entry_time'][:16]:<18} {t['exit_time'][:16]:<18} "
                  f"{t['entry']:>8.4f} {t['tp']:>8.4f} {t['sl']:>8.4f} "
                  f"{t['result']:<3} {pip_pnl:>+9.2f} {t['bars_held']:>5}")

    print("\n" + "=" * W)


# ──────────────────────────────────────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_PAIRS = [
    'USDJPY', 'GBPJPY', 'EURUSD', 'GBPUSD',
    'EURJPY', 'AUDUSD', 'USDCAD', 'AUDCAD',
    'EURGBP', 'NZDUSD', 'USDCHF',
]

JPY_SPREAD = 0.02    # JPYペア: 往復2pip ≒ 0.02
STD_SPREAD = 0.0002  # その他: 往復2pip ≒ 0.0002


def main():
    parser = argparse.ArgumentParser(description='柴犬式FX理論 バックテスト')
    parser.add_argument('--pairs', nargs='+', default=None,
                        help='対象ペア (デフォルト: 全利用可能ペア)')
    parser.add_argument('--tf', default='1h', help='時間足 (デフォルト: 1h)')
    parser.add_argument('--spread', type=float, default=None,
                        help='スプレッド (価格単位、未指定でペア別自動設定)')
    parser.add_argument('--save-csv', default='optimizer/shiba_inu_fx_bt_result.csv',
                        help='結果CSV保存先')
    args = parser.parse_args()

    # 対象ペア決定
    if args.pairs:
        pairs = args.pairs
    else:
        pairs = [
            p.stem.replace(f'_{args.tf}', '')
            for p in DATA_DIR.glob(f'*_{args.tf}.csv')
        ]
        pairs = sorted(set(pairs) & set(DEFAULT_PAIRS))

    print(f"柴犬式FX理論 バックテスト")
    print(f"対象ペア: {', '.join(pairs)}")
    print(f"時間足  : {args.tf}")
    print(f"スプレッド: {'ペア別自動 (JPY=2pip/非JPY=2pip)' if args.spread is None else args.spread}")

    all_trades = []
    all_stats = {}
    all_metrics = []

    for pair in pairs:
        try:
            df_raw = load_ohlc(pair, args.tf)
        except FileNotFoundError:
            print(f"  {pair}: データなし。スキップ。")
            continue

        if len(df_raw) < 210:
            print(f"  {pair}: データ不足 ({len(df_raw)}行)。スキップ。")
            continue

        df = add_indicators(df_raw)
        spread = args.spread if args.spread is not None else (JPY_SPREAD if 'JPY' in pair else STD_SPREAD)

        trades, stats = run_backtest(df, pair, spread)
        metrics = calc_metrics(trades, pair)

        all_trades.extend(trades)
        all_stats[pair] = stats
        all_metrics.append(metrics)

        if metrics['n'] > 0:
            exp_pip = to_pip(metrics['expectancy'], pair)
            print(f"  {pair}: n={metrics['n']}, WR={metrics['win_rate']:.0%}, "
                  f"PF={metrics['pf']:.2f}, exp={exp_pip:+.2f}pip"
                  f" | crosses={stats['total_crosses']}, wave2={stats['setups_completed']}"
                  f", entries={stats['entries_triggered']}")
        else:
            print(f"  {pair}: トレードなし | crosses={stats['total_crosses']}")

    print_report(all_trades, all_stats, all_metrics)

    # CSV保存
    if all_trades:
        out_csv = Path(args.save_csv)
        pd.DataFrame(all_trades).to_csv(out_csv, index=False)
        print(f"結果保存: {out_csv} ({len(all_trades)} trades)\n")


if __name__ == '__main__':
    main()
