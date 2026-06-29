"""
liquidity_sweep_bt.py - Liquidity Sweep (流動性スイープ・逆張り) バックテスト。

戦略概要 (仕様書 2026-06-29):
    前日の高値(PDH)/安値(PDL)を「流動性プール(ストップ狩りライン)」とみなし、
    そこへの「意図的なブレイクアウトの失敗 = フェイクアウト」を事後確認してから
    平均回帰方向にエントリーする逆張り戦略。
      - Short: 高値が PDH を上抜く(sweep)が、終値は PDH 下で確定(rejection)
      - Long : 安値が PDL を下抜く(sweep)が、終値は PDL 上で確定(rejection)
    確定足の「次足始値(Next Bar Open)」で成行。SL=スイープ足の極値±X pips。
    TP=固定RR or レンジ回帰(中央値/反対ライン)。

対象:
    レンジになりやすく平均回帰が効く相関クロス優先 (AUDCAD/EURGBP/AUDNZD/CADCHF)。
    本環境では Dukascopy 取得が network policy で不可のため、リポジトリ同梱の
    data/<PAIR>_<TF>.csv (存在するもの) を自動検出して使用する。

検証規律 (CLAUDE.md 既存方針を踏襲):
    - Lookahead 排除: PDH/PDL は前日足を shift(1)。全シグナルは確定足、約定は次足始値。
    - フルコスト: spread + slippage を pips で差し引く (往復)。
    - 早期切り分け: 「スイープ確認フィルタ on/off」x「London/NY 窓 on/off」の
      2x2 で PF が反転/改善するか (= Sneaky Pivot に対する優位性) を最初に出力。

使用法:
    python3 optimizer/liquidity_sweep_bt.py
    python3 optimizer/liquidity_sweep_bt.py --tf 15m --pairs AUDCAD EURGBP AUDNZD CADCHF
    python3 optimizer/liquidity_sweep_bt.py --tp-mode rr --rr 1.5 --sweep-pips 1.0 --sl-pips 1.0
"""

import argparse
import os
from itertools import product

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# 対象ペアの pip サイズ (全て非JPYクロス) と既定の往復コスト(spread+slippage, pips)。
PAIR_META = {
    'AUDCAD': {'pip': 0.0001, 'cost_pips': 2.0},
    'EURGBP': {'pip': 0.0001, 'cost_pips': 2.0},
    'AUDNZD': {'pip': 0.0001, 'cost_pips': 2.5},
    'CADCHF': {'pip': 0.0001, 'cost_pips': 2.5},
}
DEFAULT_PIP = 0.0001
DEFAULT_COST_PIPS = 2.0


# ----------------------------------------------------------------------------
# データロード
# ----------------------------------------------------------------------------
def load_data(pair, tf):
    """data/<pair>_<tf>.csv または _dukas 版を index_col=0 で読み込む。"""
    candidates = [f'{pair}_{tf}_dukas.csv', f'{pair}_{tf}.csv']
    path = None
    for c in candidates:
        p = os.path.join(DATA_DIR, c)
        if os.path.exists(p):
            path = p
            break
    if path is None:
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    # 重複カラム(EURGBP_1h は大文字/小文字併記)を排除し小文字 OHLC のみ残す。
    df = df.loc[:, ~df.columns.duplicated()]
    df.index = pd.to_datetime(df.index, utc=True, format='mixed')
    df = df[['open', 'high', 'low', 'close']].astype(float).sort_index()
    df = df[~df.index.duplicated(keep='first')].dropna()
    return df


def attach_pdhl(df):
    """前日の高値/安値を当日バーへ付与 (shift(1) で lookahead 排除)。"""
    daily = df.resample('1D').agg({'high': 'max', 'low': 'min'}).dropna()
    pdh = daily['high'].shift(1)          # 前日高値
    pdl = daily['low'].shift(1)           # 前日安値
    day_key = df.index.normalize()
    out = df.copy()
    out['pdh'] = pdh.reindex(day_key).to_numpy()
    out['pdl'] = pdl.reindex(day_key).to_numpy()
    return out.dropna(subset=['pdh', 'pdl'])


# ----------------------------------------------------------------------------
# バックテスト・エンジン
# ----------------------------------------------------------------------------
def run_bt(df, pip, cfg):
    """
    1ポジション同時保有 (no pyramiding)。
    cfg keys:
      use_sweep_filter (bool): True=rejection確定を要求(本来のスイープ),
                               False=ラインへの単純タッチでフェード(naive baseline)。
      use_session      (bool): True=session窓に限定。
      sess_start/sess_end (int): エントリー許可時間 [start, end) (データtz=UTC の hour)。
      sweep_pips       (float): ラインを抜けたと判定する最小 pips。
      sl_pips          (float): スイープ足極値からの SL バッファ pips。
      tp_mode          (str): 'rr' | 'mid' | 'opposite'。
      rr               (float): tp_mode='rr' のリスクリワード比。
      cost_pips        (float): 往復 spread+slippage (pips)。
    返り値: dict(metrics) + trades list。
    """
    o = df['open'].to_numpy()
    h = df['high'].to_numpy()
    l = df['low'].to_numpy()
    c = df['close'].to_numpy()
    pdh = df['pdh'].to_numpy()
    pdl = df['pdl'].to_numpy()
    hours = df.index.hour.to_numpy()
    n = len(df)

    sweep = cfg['sweep_pips'] * pip
    slbuf = cfg['sl_pips'] * pip
    cost = cfg['cost_pips'] * pip
    use_filter = cfg['use_sweep_filter']
    use_sess = cfg['use_session']
    s0, s1 = cfg['sess_start'], cfg['sess_end']
    tp_mode = cfg['tp_mode']
    rr = cfg['rr']

    def in_session(hr):
        if not use_sess:
            return True
        if s0 <= s1:
            return s0 <= hr < s1
        return hr >= s0 or hr < s1   # 日跨ぎ窓

    trades = []
    i = 0
    # 各バー i で「確定足」を判定 -> i+1 始値で約定 -> i+1 以降で SL/TP 監視。
    while i < n - 1:
        if not in_session(hours[i]):
            i += 1
            continue

        side = None
        # --- Short シグナル: PDH を上抜き(sweep) + 終値は PDH 下(rejection) ---
        short_sweep = h[i] > pdh[i] + sweep
        short_reject = c[i] < pdh[i]
        if (use_filter and short_sweep and short_reject) or (not use_filter and h[i] >= pdh[i]):
            side = 'short'
            swing = h[i]                 # スイープ足高値
        # --- Long シグナル: PDL を下抜き(sweep) + 終値は PDL 上(rejection) ---
        long_sweep = l[i] < pdl[i] - sweep
        long_reject = c[i] > pdl[i]
        if side is None and ((use_filter and long_sweep and long_reject) or
                             (not use_filter and l[i] <= pdl[i])):
            side = 'long'
            swing = l[i]

        if side is None:
            i += 1
            continue

        # --- 次足始値で約定 (スリッページは cost に含めて決済時に往復控除) ---
        entry_idx = i + 1
        entry = o[entry_idx]

        if side == 'short':
            sl = swing + slbuf
            risk = sl - entry
            if risk <= 0:
                i += 1
                continue
            if tp_mode == 'rr':
                tp = entry - rr * risk
            elif tp_mode == 'mid':
                tp = (pdh[i] + pdl[i]) / 2.0
            else:  # opposite line
                tp = pdl[i]
            if tp >= entry:          # ターゲットが逆 = スキップ
                i += 1
                continue
        else:
            sl = swing - slbuf
            risk = entry - sl
            if risk <= 0:
                i += 1
                continue
            if tp_mode == 'rr':
                tp = entry + rr * risk
            elif tp_mode == 'mid':
                tp = (pdh[i] + pdl[i]) / 2.0
            else:
                tp = pdh[i]
            if tp <= entry:
                i += 1
                continue

        # --- 約定後バーで SL/TP 監視。同足両ヒットは SL 優先(保守的) ---
        exit_price = None
        exit_idx = None
        j = entry_idx
        while j < n:
            if side == 'short':
                hit_sl = h[j] >= sl
                hit_tp = l[j] <= tp
            else:
                hit_sl = l[j] <= sl
                hit_tp = h[j] >= tp
            if hit_sl:
                exit_price = sl
                exit_idx = j
                break
            if hit_tp:
                exit_price = tp
                exit_idx = j
                break
            j += 1
        if exit_price is None:        # データ末尾で未決済 -> 最終終値でクローズ
            exit_price = c[-1]
            exit_idx = n - 1

        if side == 'short':
            gross = entry - exit_price
        else:
            gross = exit_price - entry
        net = gross - cost            # 往復コスト
        trades.append({
            'side': side, 'entry_t': df.index[entry_idx], 'exit_t': df.index[exit_idx],
            'entry': entry, 'exit': exit_price, 'net_pips': net / pip,
        })
        # 次の探索は決済足の次から (重複保有なし)
        i = exit_idx + 1

    return _metrics(trades), trades


def _metrics(trades):
    if not trades:
        return {'n': 0, 'pf': float('nan'), 'net_pips': 0.0, 'wr': float('nan'),
                'avg_win': 0.0, 'avg_loss': 0.0, 'expectancy': 0.0, 'max_dd_pips': 0.0}
    nets = np.array([t['net_pips'] for t in trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    equity = np.cumsum(nets)
    peak = np.maximum.accumulate(equity)
    max_dd = (peak - equity).max() if len(equity) else 0.0
    return {
        'n': len(trades), 'pf': pf, 'net_pips': float(nets.sum()),
        'wr': float((nets > 0).mean()),
        'avg_win': float(wins.mean()) if len(wins) else 0.0,
        'avg_loss': float(losses.mean()) if len(losses) else 0.0,
        'expectancy': float(nets.mean()), 'max_dd_pips': float(max_dd),
    }


# ----------------------------------------------------------------------------
# レポート
# ----------------------------------------------------------------------------
def base_cfg(args, meta):
    return {
        'use_sweep_filter': True, 'use_session': True,
        'sess_start': args.sess_start, 'sess_end': args.sess_end,
        'sweep_pips': args.sweep_pips, 'sl_pips': args.sl_pips,
        'tp_mode': args.tp_mode, 'rr': args.rr, 'cost_pips': meta['cost_pips'],
    }


def fmt_m(m):
    if m['n'] == 0:
        return 'n=0 (シグナルなし)'
    return (f"PF={m['pf']:.3f} net={m['net_pips']:>8.0f}pip n={m['n']:>4d} "
            f"WR={m['wr']*100:4.1f}% avgW={m['avg_win']:5.1f} avgL={m['avg_loss']:6.1f} "
            f"exp={m['expectancy']:5.2f} DD={m['max_dd_pips']:.0f}")


def early_cut_report(dfs, args):
    """早期切り分け: スイープ確認 on/off x session on/off の 2x2 を全ペアで。"""
    print('=' * 100)
    print('早期切り分けレポート: スイープ確認フィルタ x London/NY セッション窓')
    print(f"  TF={args.tf} tp_mode={args.tp_mode} rr={args.rr} sweep={args.sweep_pips}pip "
          f"sl={args.sl_pips}pip session={args.sess_start:02d}-{args.sess_end:02d}UTC")
    print('=' * 100)
    agg = {}
    rows = []
    for pair, df in dfs.items():
        meta = PAIR_META.get(pair, {'pip': DEFAULT_PIP, 'cost_pips': DEFAULT_COST_PIPS})
        print(f'\n[{pair}]  bars={len(df)}  '
              f"{df.index[0].date()}~{df.index[-1].date()}")
        for sweep_on, sess_on in product([True, False], [True, False]):
            cfg = base_cfg(args, meta)
            cfg['use_sweep_filter'] = sweep_on
            cfg['use_session'] = sess_on
            m, _ = run_bt(df, meta['pip'], cfg)
            tag = f"sweep={'ON ' if sweep_on else 'OFF'} session={'ON ' if sess_on else 'OFF'}"
            print(f'    {tag} | {fmt_m(m)}')
            agg.setdefault((sweep_on, sess_on), []).append(m)
            rows.append({'pair': pair, 'tf': args.tf, 'tp_mode': args.tp_mode, 'rr': args.rr,
                         'sweep_filter': sweep_on, 'session': sess_on, **m})
    # 全ペア合算 (pips 単純合算)
    print('\n' + '-' * 100)
    print('全ペア合算 (net pips 単純合算 / PF は gross 合算)')
    for sweep_on, sess_on in product([True, False], [True, False]):
        ms = agg[(sweep_on, sess_on)]
        gw = sum(max(m['avg_win'], 0) * m['n'] * m['wr'] for m in ms if m['n'])
        # gross 再計算は trades 無いので net 合算と n 合算のみ提示
        net = sum(m['net_pips'] for m in ms)
        n = sum(m['n'] for m in ms)
        tag = f"sweep={'ON ' if sweep_on else 'OFF'} session={'ON ' if sess_on else 'OFF'}"
        print(f'    {tag} | net={net:>9.0f}pip n={n:>4d}')
    print('\n注: sweep=ON は「上ヒゲ+実体レンジ内回帰(rejection)」確定を要求(本来のスイープ)。')
    print('    sweep=OFF は確認なしでラインへのタッチをフェード(Sneaky Pivot 相当のベースライン)。')
    return rows


def dump_csv(dfs, args, rows):
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'liquidity_sweep_bt_result.csv')
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f'\n[csv] {out} ({len(rows)} 行)')


def is_oos_report(dfs, args):
    """IS(前半)/OOS(後半) で本構成(sweep ON+session ON)の頑健性を確認。"""
    print('\n' + '=' * 100)
    print('IS/OOS 検証 (利用可能スパンを前半/後半に2分割, 本構成=sweep ON + session ON)')
    print('=' * 100)
    for pair, df in dfs.items():
        meta = PAIR_META.get(pair, {'pip': DEFAULT_PIP, 'cost_pips': DEFAULT_COST_PIPS})
        mid = df.index[len(df) // 2]
        is_df, oos_df = df[df.index < mid], df[df.index >= mid]
        cfg = base_cfg(args, meta)
        mi, _ = run_bt(is_df, meta['pip'], cfg)
        mo, _ = run_bt(oos_df, meta['pip'], cfg)
        print(f'\n[{pair}] split={mid.date()}')
        print(f'    IS  ({is_df.index[0].date()}~{is_df.index[-1].date()}) | {fmt_m(mi)}')
        print(f'    OOS ({oos_df.index[0].date()}~{oos_df.index[-1].date()}) | {fmt_m(mo)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='1h', help='時間足 (15m/5m/1h ... data/<PAIR>_<TF>.csv)')
    ap.add_argument('--pairs', nargs='+',
                    default=['AUDCAD', 'EURGBP', 'AUDNZD', 'CADCHF'])
    ap.add_argument('--tp-mode', dest='tp_mode', default='rr',
                    choices=['rr', 'mid', 'opposite'])
    ap.add_argument('--rr', type=float, default=1.5)
    ap.add_argument('--sweep-pips', dest='sweep_pips', type=float, default=1.0)
    ap.add_argument('--sl-pips', dest='sl_pips', type=float, default=2.0)
    # session 窓 (データtz=UTC)。既定 06-16 UTC ≈ 日本時間 15:00-25:00 (London前後~NY前後)。
    ap.add_argument('--sess-start', dest='sess_start', type=int, default=6)
    ap.add_argument('--sess-end', dest='sess_end', type=int, default=16)
    args = ap.parse_args()

    dfs = {}
    missing = []
    for pair in args.pairs:
        df = load_data(pair, args.tf)
        if df is None or len(df) < 100:
            missing.append(pair)
            continue
        dfs[pair] = attach_pdhl(df)
    if missing:
        print(f'[warn] データ未取得/不足のためスキップ: {missing} '
              f'(本環境は Dukascopy 取得不可。data/<PAIR>_{args.tf}.csv が必要)')
    if not dfs:
        print('[error] 使用可能なデータがありません。')
        return

    rows = early_cut_report(dfs, args)
    is_oos_report(dfs, args)
    dump_csv(dfs, args, rows)


if __name__ == '__main__':
    main()
