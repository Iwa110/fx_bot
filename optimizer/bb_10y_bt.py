"""
bb_10y_bt.py - BB逆張り戦略 (bb_monitor.py v29) 10年バックテスト。

目的:
    現行ローカルデータは約2年(2024-04~2026-05)のみ。Dukascopyの5m足10年分で
    v29パラメータが長期的に頑健か(IS/OOS分割)を検証する。

データ:
    data/<SYM>_5m_10y.csv  (fetch_dukascopy_ohlc.py --tf 5m --suffix _10y で生成)
    columns: datetime,open,high,low,close,volume (UTC naive, BID側OHLC)

重要な実機整合:
    - ATR は 1h足ATR14(ewm span=14, adjust=False) を使う(risk_manager.get_atr と同一)。
      5m足ATRではなく1h足ATRが実機の実態(比率≈3.7~3.9倍, CLAUDE.md参照)。
      5m足を1h足にリサンプルして算出。各5m足は「直近に確定した1h足」のATRを参照。
    - htf4h filter は 4h足(5mリサンプル)EMA20の方向一致のみ(RSI無し / 仕様書準拠の簡略版)。
      get_htf4h_signal と同設計(close>EMA20 → Buy許可 / close<EMA20 → Sell許可)。

v29パラメータ(固定):
    | ペア   | sigma | sl_atr_mult | tp_sl_ratio | T_max          | htf4h | max_pos | 時間帯除外  |
    | GBPJPY | 2.0   | 2.5         | 1.5         | なし            | 方向  | 2       | なし        |
    | USDJPY | 2.0   | 2.5         | 1.5         | 8h + exp decay  | 方向  | 2       | なし        |
    | EURJPY | 1.5   | 2.5         | 1.5         | 6h 強制決済     | 方向  | 1       | UTC 9h,17h  |

実行:
    .venv_dukas/bin/python optimizer/bb_10y_bt.py --data-suffix _5m_10y
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

# ── v29 ペア別設定 ─────────────────────────────────────────────
PAIR_CFG = {
    'GBPJPY': {
        'sigma': 2.0, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        't_max_h': None, 'tp_decay': None, 'max_pos': 2, 'excl_hours': [],
    },
    'USDJPY': {
        'sigma': 2.0, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        't_max_h': 8.0, 'tp_decay': 'exp', 'tp_decay_tau': 8.0,
        'max_pos': 2, 'excl_hours': [],
    },
    'EURJPY': {
        'sigma': 1.5, 'sl_atr_mult': 2.5, 'tp_sl_ratio': 1.5,
        't_max_h': 6.0, 'tp_decay': None, 'max_pos': 1, 'excl_hours': [9, 17],
    },
}

BB_PERIOD   = 20
ATR_PERIOD  = 14
HTF4H_SPAN  = 20
ATR_FLOOR_JPY = 0.005      # risk_manager.ATR_FLOOR_JPY
PIP         = 0.01         # JPYペア
SPREAD_PIP  = 2.0          # JPYペア スプレッド(往復コストとして1トレード当たり差引)
COOLDOWN_BARS = 3          # 15分 = 5m × 3本
LOT         = 0.01         # 0.01lot想定 → JPYペアは 1pip ≈ 10円

IS_START  = pd.Timestamp('2016-01-01')
IS_END    = pd.Timestamp('2022-12-31 23:59:59')
OOS_START = pd.Timestamp('2023-01-01')
OOS_END   = pd.Timestamp('2026-06-01 23:59:59')


# ─────────────────────────────────────────────────────────────
# データ読込・インジケーター
# ─────────────────────────────────────────────────────────────
def load_5m(sym: str, suffix: str) -> pd.DataFrame:
    path = DATA_DIR / f'{sym}{suffix}.csv'
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.dropna(subset=['open', 'high', 'low', 'close']).sort_values('datetime')
    df = df.drop_duplicates(subset=['datetime'], keep='first').reset_index(drop=True)
    return df


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df['close'].shift(1)
    return pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)


def build_1h_atr(df5: pd.DataFrame) -> pd.Series:
    """5m足を1h足にリサンプルし ATR14(ewm) を算出。
    返り値: index=1h足の確定時刻(close_time=hour_start+1h), value=その時点で参照可能なATR。"""
    s = df5.set_index('datetime')
    o = s['open'].resample('1h').first()
    h = s['high'].resample('1h').max()
    lo = s['low'].resample('1h').min()
    c = s['close'].resample('1h').last()
    h1 = pd.DataFrame({'open': o, 'high': h, 'low': lo, 'close': c}).dropna()
    tr = true_range(h1)
    atr = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    atr = atr.clip(lower=ATR_FLOOR_JPY)
    # 1h足(hour_start)の確定時刻 = hour_start + 1h。以降の5m足が参照可能。
    atr.index = atr.index + pd.Timedelta(hours=1)
    return atr.dropna()


def build_4h_dir(df5: pd.DataFrame) -> pd.Series:
    """5m足を4h足にリサンプルし EMA20方向を算出。+1=上昇(Buy許可) / -1=下降(Sell許可)。
    返り値: index=4h足の確定時刻, value=方向。"""
    s = df5.set_index('datetime')
    c = s['close'].resample('4h').last().dropna()
    ema = c.ewm(span=HTF4H_SPAN, adjust=False).mean()
    direction = np.where(c > ema, 1, -1)
    out = pd.Series(direction, index=c.index + pd.Timedelta(hours=4))
    return out


# ─────────────────────────────────────────────────────────────
# シミュレーション
# ─────────────────────────────────────────────────────────────
def simulate(sym: str, df5: pd.DataFrame, cfg: dict) -> list:
    """next-bar fill のイベント駆動BT。返り値: トレードのリスト(dict)。"""
    sigma   = cfg['sigma']
    sl_mult = cfg['sl_atr_mult']
    rr      = cfg['tp_sl_ratio']
    t_max_h = cfg['t_max_h']
    decay   = cfg['tp_decay']
    tau     = cfg.get('tp_decay_tau', 8.0)
    max_pos = cfg['max_pos']
    excl    = set(cfg['excl_hours'])

    close = df5['close'].values
    high  = df5['high'].values
    low   = df5['low'].values
    openp = df5['open'].values
    times = df5['datetime'].values
    n = len(df5)

    ma  = df5['close'].rolling(BB_PERIOD).mean()
    std = df5['close'].rolling(BB_PERIOD).std()       # ddof=1 (bb_monitorと一致)
    upper = (ma + sigma * std).values
    lower = (ma - sigma * std).values

    # ATR / 4h方向 を 5m足時刻へ前方マージ(直近確定値を参照)
    atr_s = build_1h_atr(df5)
    dir_s = build_4h_dir(df5)
    t_index = pd.DatetimeIndex(times)
    atr_aligned = atr_s.reindex(atr_s.index.union(t_index)).ffill().reindex(t_index).values
    dir_aligned = dir_s.reindex(dir_s.index.union(t_index)).ffill().reindex(t_index).values

    open_positions = []   # list of dict
    trades = []
    cooldown_until = -1    # bar index まで再エントリー禁止

    spread_price = SPREAD_PIP * PIP

    for i in range(BB_PERIOD + 1, n - 1):
        # ── 既存ポジションの決済処理(当バー i の高安で判定)──────────
        still_open = []
        for pos in open_positions:
            exited = _try_exit(pos, i, high[i], low[i], close[i], times[i],
                               t_max_h, decay, tau)
            if exited is not None:
                trades.append(exited)
                cooldown_until = i + COOLDOWN_BARS
            else:
                still_open.append(pos)
        open_positions = still_open

        # ── エントリー判定(バー i のcloseでシグナル → i+1 openで約定)──
        if i <= cooldown_until:
            continue
        if len(open_positions) >= max_pos:
            continue
        u, l = upper[i], lower[i]
        if np.isnan(u) or np.isnan(l):
            continue

        direction = None
        if close[i] >= u:
            direction = -1   # sell
        elif close[i] <= l:
            direction = 1    # buy
        if direction is None:
            continue

        # 時間帯除外(エントリー足の次バー=約定足のUTC時)
        entry_time = pd.Timestamp(times[i + 1])
        if entry_time.hour in excl:
            continue

        # htf4h 方向一致フィルター
        d4 = dir_aligned[i]
        if np.isnan(d4) or int(d4) != direction:
            continue

        atr = atr_aligned[i]
        if np.isnan(atr) or atr <= 0:
            continue

        entry_price = openp[i + 1]
        sl_dist = atr * sl_mult
        tp_dist = sl_dist * rr
        if direction == 1:
            sl = entry_price - sl_dist
            tp = entry_price + tp_dist
        else:
            sl = entry_price + sl_dist
            tp = entry_price - tp_dist

        open_positions.append({
            'sym': sym, 'dir': direction, 'entry_i': i + 1,
            'entry_price': entry_price, 'entry_time': entry_time,
            'sl': sl, 'tp': tp, 'tp_dist0': tp_dist, 'spread': spread_price,
        })

    # 期末に残ったポジションは最終バーcloseで決済
    for pos in open_positions:
        trades.append(_close_at(pos, n - 1, close[n - 1], times[n - 1], 'EOD'))

    return trades


def _decayed_tp(pos, elapsed_h, tau):
    """USDJPY exp TP decay: TP(t) = tp_dist0 × (1/3.75 + 2.75/3.75 × exp(-t/tau))。"""
    ratio = (1.0 / 3.75) + (2.75 / 3.75) * math.exp(-elapsed_h / tau)
    tp_dist = pos['tp_dist0'] * ratio
    if pos['dir'] == 1:
        return pos['entry_price'] + tp_dist
    return pos['entry_price'] - tp_dist


def _try_exit(pos, i, hi, lo, cl, t, t_max_h, decay, tau):
    """当バーで SL/TP/T_max のいずれかに該当すれば決済トレードdictを返す。否なら None。
    SL/TP同時タッチは保守的にSL優先。"""
    elapsed_h = (pd.Timestamp(t) - pos['entry_time']).total_seconds() / 3600.0

    # TP decay (USDJPY) はバー時点の経過時間で縮小したTPを使う
    tp = pos['tp']
    if decay == 'exp':
        tp = _decayed_tp(pos, max(elapsed_h, 0.0), tau)

    sl = pos['sl']
    hit_sl = (lo <= sl) if pos['dir'] == 1 else (hi >= sl)
    hit_tp = (hi >= tp) if pos['dir'] == 1 else (lo <= tp)

    if hit_sl:   # SL優先(保守的)
        return _close_at_price(pos, i, sl, t, 'SL')
    if hit_tp:
        return _close_at_price(pos, i, tp, t, 'TP')

    # T_max 強制決済(バーcloseで)
    if t_max_h is not None and elapsed_h >= t_max_h:
        return _close_at_price(pos, i, cl, t, 'T_max')
    return None


def _pnl(pos, exit_price):
    raw = (exit_price - pos['entry_price']) * pos['dir']
    raw -= pos['spread']           # 往復スプレッドコスト
    pips = raw / PIP
    jpy = raw * (LOT * 100_000)    # JPYクオート: price差 × units
    return pips, jpy


def _close_at_price(pos, i, exit_price, t, reason):
    pips, jpy = _pnl(pos, exit_price)
    return {'sym': pos['sym'], 'dir': pos['dir'], 'entry_time': pos['entry_time'],
            'exit_time': pd.Timestamp(t), 'exit_i': i, 'reason': reason,
            'pips': pips, 'jpy': jpy}


def _close_at(pos, i, exit_price, t, reason):
    return _close_at_price(pos, i, exit_price, t, reason)


# ─────────────────────────────────────────────────────────────
# 指標
# ─────────────────────────────────────────────────────────────
def metrics(trades: list) -> dict:
    if not trades:
        return {'pf': 0.0, 'wr': 0.0, 'n': 0, 'net_pip': 0.0, 'net_jpy': 0.0,
                'maxdd_pip': 0.0, 'sharpe': 0.0}
    df = pd.DataFrame(trades).sort_values('exit_time').reset_index(drop=True)
    pips = df['pips']
    gross_win = pips[pips > 0].sum()
    gross_loss = -pips[pips < 0].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    wr = (pips > 0).mean() * 100
    net_pip = pips.sum()
    net_jpy = df['jpy'].sum()

    # maxDD (pip, 累積エクイティのピークからの最大下落)
    eq = pips.cumsum()
    dd = eq - eq.cummax()
    maxdd_pip = dd.min()

    # Sharpe: 月次pipリターンの mean/std × sqrt(12)
    m = df.set_index('exit_time')['pips'].resample('ME').sum()
    if len(m) > 1 and m.std() > 0:
        sharpe = m.mean() / m.std() * math.sqrt(12)
    else:
        sharpe = 0.0

    return {'pf': pf, 'wr': wr, 'n': len(df), 'net_pip': net_pip,
            'net_jpy': net_jpy, 'maxdd_pip': maxdd_pip, 'sharpe': sharpe}


def split_trades(trades: list):
    is_t, oos_t = [], []
    for t in trades:
        et = t['entry_time']
        if IS_START <= et <= IS_END:
            is_t.append(t)
        elif OOS_START <= et <= OOS_END:
            oos_t.append(t)
    return is_t, oos_t


# ─────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-suffix', default='_5m_10y')
    ap.add_argument('--pairs', nargs='+', default=list(PAIR_CFG.keys()))
    args = ap.parse_args()

    rows = []          # result.csv 用
    monthly_rows = []  # monthly.csv 用
    all_results = {}

    for sym in args.pairs:
        cfg = PAIR_CFG[sym]
        print(f'\n[{sym}] 読込中... ({sym}{args.data_suffix}.csv)')
        df5 = load_5m(sym, args.data_suffix)
        span = f"{df5['datetime'].iloc[0].date()}~{df5['datetime'].iloc[-1].date()}"
        print(f'[{sym}] {len(df5):,}本 ({span}) シミュレーション中...')
        trades = simulate(sym, df5, cfg)
        is_t, oos_t = split_trades(trades)
        all_results[sym] = {'IS': metrics(is_t), 'OOS': metrics(oos_t),
                            'ALL': metrics(is_t + oos_t)}

        for period, tl in [('IS', is_t), ('OOS', oos_t)]:
            m = all_results[sym][period]
            rows.append({'pair': sym, 'period': period, 'pf': round(m['pf'], 3),
                         'wr_pct': round(m['wr'], 1), 'n': m['n'],
                         'net_pip': round(m['net_pip'], 1),
                         'net_jpy': round(m['net_jpy']),
                         'maxdd_pip': round(m['maxdd_pip'], 1),
                         'sharpe': round(m['sharpe'], 2)})

        # 月次
        if trades:
            dft = pd.DataFrame(trades).sort_values('exit_time')
            g = dft.set_index('exit_time').resample('ME').agg(
                net_pip=('pips', 'sum'), net_jpy=('jpy', 'sum'), n=('pips', 'size'))
            for ts, r in g.iterrows():
                monthly_rows.append({'pair': sym, 'month': ts.strftime('%Y-%m'),
                                     'net_pip': round(r['net_pip'], 1),
                                     'net_jpy': round(r['net_jpy']), 'n': int(r['n'])})

    # ── コンソール出力 ──────────────────────────────────────────
    print('\n' + '=' * 72)
    print('=== BB 10年BT 結果 (v29パラメータ / 1h ATR / next-bar fill / spread込) ===')
    print('=' * 72)
    for period, label in [('IS', f'IS: {IS_START.date()}~{IS_END.date()}'),
                          ('OOS', f'OOS: {OOS_START.date()}~{OOS_END.date()}')]:
        print(f'\n{label}')
        print(f'{"ペア":<8}{"PF":>7}{"WR":>8}{"n":>7}{"net(pip)":>11}'
              f'{"net(円)":>11}{"maxDD(pip)":>12}{"Sharpe":>8}')
        print('-' * 72)
        for sym in args.pairs:
            m = all_results[sym][period]
            pf = f'{m["pf"]:.3f}' if math.isfinite(m['pf']) else 'inf'
            print(f'{sym:<8}{pf:>7}{m["wr"]:>7.1f}%{m["n"]:>7}'
                  f'{m["net_pip"]:>+11.0f}{m["net_jpy"]:>+11.0f}'
                  f'{m["maxdd_pip"]:>12.0f}{m["sharpe"]:>8.2f}')

    # 判定
    print('\n' + '=' * 72)
    print('判定 (IS/OOS両方 PF>1.2 かつ n>200 → 頑健エッジ):')
    print('-' * 72)
    for sym in args.pairs:
        mi, mo = all_results[sym]['IS'], all_results[sym]['OOS']
        robust = mi['pf'] > 1.2 and mi['n'] > 200 and mo['pf'] > 1.2 and mo['n'] > 200
        overfit = mi['pf'] > 1.2 and mo['pf'] < 1.0
        if robust:
            verdict = 'OK 頑健エッジ確認'
        elif overfit:
            verdict = 'NG 過適合(IS良/OOS<1.0)'
        elif mo['pf'] < 1.0:
            verdict = 'NG OOS PF<1.0'
        else:
            verdict = '△ 部分的(基準一部未達)'
        print(f'  {sym}: IS PF={mi["pf"]:.3f}(n={mi["n"]}) / '
              f'OOS PF={mo["pf"]:.3f}(n={mo["n"]}) → {verdict}')

    # ── CSV保存 ─────────────────────────────────────────────────
    res_path = Path(__file__).resolve().parent / 'bb_10y_bt_result.csv'
    mon_path = Path(__file__).resolve().parent / 'bb_10y_monthly.csv'
    pd.DataFrame(rows).to_csv(res_path, index=False)
    pd.DataFrame(monthly_rows).to_csv(mon_path, index=False)
    print(f'\n保存: {res_path}')
    print(f'保存: {mon_path}')


if __name__ == '__main__':
    main()
