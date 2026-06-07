"""
pullback_gated_trend_bt.py - 案D: Grid含み損ゲート式 Trend BT (GBPJPY).

背景 / 案Bの失敗:
  案B(常時稼働 Donchian breakout)は Grid最大DD局面でTrendが「不在」(corr≈0,
  Grid DD上位5区間でのTrend損益 ≈ +1.2R)で、補完弁として機能しなかった。
  → 真因 = "DD窓不在": Trendを常時走らせても Gridが痛む局面に居合わせない。

案Dの設計 (DD窓不在の解消):
  Trend を常時走らせず、「Grid が含み損で痛んでいる時だけ」ゲートを開けて
  順行方向にTrendを入れる。Grid が損切り(float-stop/B48)に向かう正にその窓で
  Trendが順行レッグを取りに行く -> 構造的に補完が成立するはず、を検証する。

ゲート信号 (因果整合のため GBPJPY グリッド単体を使用 = Trendと同ペア):
  - Grid を H1 で再構築(grid_floatstop_bt のロジック/ライブv7 GBPJPY config 忠実複製)し、
    各H1バー終値で mark-to-market エクイティ = 実現累積PnL + 含み損(floating) を算出。
  - これが「Grid日次累積PnL(含み損込み)」。残高 BALANCE に対する drawdown(peak比)で判定。
  - ゲート開放: dd = equity - running_peak が <= -gate_th * BALANCE に落ちた時点。
  - ゲート閉鎖: equity が 開放時水準 + 0.01*BALANCE まで回復(+1%回復)した時点で
                Trend強制決済・停止。
  - BALANCE = 5,000,000円 (GBPJPY grid 1ブック lot=1.0 / float_stop=-1.5M を吸収する想定残高)。
    gate_th=-2/-3/-5% -> -100k/-150k/-250k のDDトリガ、+1%回復=+50k。

方向 (Grid含み損の偏り = トレンド方向):
  - long_float < short_float (買い側が相対的に深い含み損 = 価格下落/下降ト) -> Trend 売り(-1)
  - 逆(売り側が深い = 上昇ト) -> Trend 買い(+1)
  → Gridを痛めているトレンドにTrendが順行する。

Trend 売買 (magic=20260050 / STRATEGY_TAG='TREND_GATE' / 案Bが未使用返却した番号を流用):
  - ペア      : GBPJPY (Grid同ペア)
  - エントリー: ゲート開放(または開放中の再エントリ条件成立)の翌1H足寄りで成行。
                方向 = その時点の Grid含み損の偏り。MAX_POS=1。
  - 出口      : (a) time-based  : 保有 time_max 時間で成行
                (b) ATRトレイル : 係数 trail_mult × ATR14(H1 / grid同一ATR)
                (c) ゲート閉鎖  : Grid +1%回復で強制決済
                (a)(b)(c) 先着で決済。
  - 再エントリ: ゲート開放中に(a)(b)で決済後、cooldown_h 経過で再度寄り成市(方向は再判定)。
  - スプレッド: GBPJPY 2pips 往復控除。会計 = R倍率(risk=初期SL距離=trail_mult*ATR_entry=1R)。

探索グリッド (3*3*3*2*2 = 108通り):
  gate_th[-0.02,-0.03,-0.05] x time_max[24,48,72] x trail_mult[1.5,2.0,3.0]
  x lot[0.1,0.2] x cooldown_h[0,12]
  ※ R系列(PF/WR/Sharpe/maxDD%/ddwin_R)は lot 不感(riskで正規化)。lot は
    JPY建てブレンド(補完評価)・JPY-DD にのみ効く。

IS/OOS: IS 2024-04-01..2025-06-30 / OOS 2025-07-01..2026-05-31 (案Bと同一)。

採用基準:
  IS : Grid DD上位5区間でのTrend損益合計 > +5R (補完性最優先) かつ PF > 1.1
  OOS: DD区間合計 > 0R

出力 (スコアカードのみ):
  - optimizer/pullback_gated_trend_bt_result.csv : グリッド x (IS/OOS) スコアカード
  - optimizer/pullback_gated_trend_bt_trades.csv : 採用上位構成のトレード台帳(補完分析用)
  - 標準出力: 全組合せ表 / 採用上位3詳細 / 2025春Grid最大DD局面ピンポイント / 補完性評価
"""

import itertools
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

import grid_floatstop_bt as G
import pullback_trend_bt as PT           # _daily_R, seg_metrics, split_seg,
                                          # load_grid_dd_windows, dd_window_trend_R
from pullback_grid_complement import build_grid_daily

from pathlib import Path
OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV = str(OUTPUT_DIR / 'pullback_gated_trend_bt_result.csv')
TRADES_CSV = str(OUTPUT_DIR / 'pullback_gated_trend_bt_trades.csv')

# --- config -----------------------------------------------------------------
PAIR        = 'GBPJPY'
MAGIC       = 20260050
PIP         = 0.01
SPREAD      = 2 * PIP
BALANCE     = 5_000_000.0     # GBPJPY grid 1ブック想定残高 (lot=1.0)
RECOVER_PCT = 0.01            # ゲート閉鎖 = +1% of balance 回復
MIN_TRADES  = 10             # セグメント当たり最低トレード数 (ゲート式で頻度低め)
ANN         = 252

IS_START,  IS_END  = PT.IS_START,  PT.IS_END
OOS_START, OOS_END = PT.OOS_START, PT.OOS_END

GRID = {
    # 案A: float-stop(-1.5M = 残高比-30%)近傍まで深掘り。「本当に痛い窓だけ」に絞る。
    'gate_th':    [-0.05, -0.10, -0.15, -0.20, -0.25],
    'time_max':   [24, 48, 72],
    'trail_mult': [1.5, 2.0, 3.0],
    'lot':        [0.1, 0.2],
    'cooldown_h': [0, 12],
}


# --- Grid gate signal (GBPJPY single book, H1 mark-to-market) ----------------
def grid_gate_series(cfg, df, atr_series, ci_series):
    """GBPJPYグリッドを H1 で再構築し、各バー終値の
    (realized_cum, float_long, float_short, equity_mtm) を返す DataFrame。
    grid_floatstop_bt / pullback_grid_complement.grid_pnl_events のロジック忠実複製。"""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot = cfg['lot']
    atr_mult, ci_threshold = cfg['atr_mult'], cfg['ci_threshold']
    b48_hours, max_levels, float_stop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    realized = 0.0

    idx, r_cum, fL, fS, eq = [], [], [], [], []

    def pj(d):
        return G.pnl_jpy(d, lot)

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci = ci_series.get(ts)
        bar_h, bar_l, bar_cl = row['high'], row['low'], row['close']

        if not (pd.isna(atr) or atr <= 0):
            gw = atr * atr_mult
            long_was_max = len(long_pos) >= max_levels
            short_was_max = len(short_pos) >= max_levels

            # TP
            for p in [p for p in long_pos if bar_h >= p['tp']]:
                realized += pj(p['tp'] - p['entry']); long_pos.remove(p)
            for p in [p for p in short_pos if bar_l <= p['tp']]:
                realized += pj(p['entry'] - p['tp']); short_pos.remove(p)

            # FLOAT STOP
            if long_pos:
                unreal = sum(pj(bar_l - p['entry']) for p in long_pos)
                if unreal <= float_stop:
                    realized += sum(pj(bar_l - p['entry']) for p in long_pos)
                    long_pos = []; b48_long_start = None
            if short_pos:
                unreal = sum(pj(p['entry'] - bar_h) for p in short_pos)
                if unreal <= float_stop:
                    realized += sum(pj(p['entry'] - bar_h) for p in short_pos)
                    short_pos = []; b48_short_start = None

            # B48 timer reset
            if long_was_max and len(long_pos) < max_levels:
                b48_long_start = None
            if short_was_max and len(short_pos) < max_levels:
                b48_short_start = None

            # B48 expiry
            if b48_long_start is not None and (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
                realized += sum(pj(bar_cl - p['entry']) for p in long_pos)
                long_pos = []; b48_long_start = None
            if b48_short_start is not None and (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
                realized += sum(pj(p['entry'] - bar_cl) for p in short_pos)
                short_pos = []; b48_short_start = None

            # New entries
            ci_ok = (not pd.isna(ci)) and (ci > ci_threshold)
            if len(long_pos) == 0:
                if ci_ok:
                    long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                    if len(long_pos) == max_levels: b48_long_start = ts
            elif len(long_pos) < max_levels:
                if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                    long_pos.append({'entry': bar_cl, 'tp': bar_cl + gw})
                    if len(long_pos) == max_levels: b48_long_start = ts

            if len(short_pos) == 0:
                if ci_ok:
                    short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                    if len(short_pos) == max_levels: b48_short_start = ts
            elif len(short_pos) < max_levels:
                if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                    short_pos.append({'entry': bar_cl, 'tp': bar_cl - gw})
                    if len(short_pos) == max_levels: b48_short_start = ts

        # mark-to-market (bar close) ----------------------------------------
        f_long = sum(pj(bar_cl - p['entry']) for p in long_pos)
        f_short = sum(pj(p['entry'] - bar_cl) for p in short_pos)
        idx.append(ts); r_cum.append(realized); fL.append(f_long); fS.append(f_short)
        eq.append(realized + f_long + f_short)

    return pd.DataFrame({'realized_cum': r_cum, 'float_long': fL, 'float_short': fS,
                         'equity_mtm': eq}, index=idx)


def compute_gate(gate_df, gate_th):
    """equity_mtm の peak比 drawdown でゲート開閉状態と方向を生成。
    戻り: (gate_open[bool array], gate_dir[int array]) df順。
      gate_dir: -1 売り(買い側が深い含み損) / +1 買い / 0 不明(両0)。"""
    eq = gate_df['equity_mtm'].values
    fL = gate_df['float_long'].values
    fS = gate_df['float_short'].values
    n = len(eq)
    open_arr = np.zeros(n, dtype=bool)
    dir_arr = np.zeros(n, dtype=int)

    peak = -np.inf
    is_open = False
    open_level = None
    trig = gate_th * BALANCE          # 負
    recover = RECOVER_PCT * BALANCE
    for i in range(n):
        peak = max(peak, eq[i])
        dd = eq[i] - peak
        if not is_open:
            if dd <= trig:
                is_open = True
                open_level = eq[i]
        else:
            if eq[i] >= open_level + recover:
                is_open = False
                open_level = None
        open_arr[i] = is_open
        if is_open:
            # 含み損が深い側 = よりマイナスの float -> その方向に順行
            if fL[i] < fS[i]:
                dir_arr[i] = -1       # 買い負け -> 売り
            elif fS[i] < fL[i]:
                dir_arr[i] = +1       # 売り負け -> 買い
            else:
                dir_arr[i] = 0
    return open_arr, dir_arr


# --- Trend backtest (gated) -------------------------------------------------
def run_gated_backtest(df, atr_vals, gate_open, gate_dir, trail_mult, time_max, lot, cooldown_h):
    opn = df['open'].values
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    time = df.index.values
    N = len(df)

    trades = []
    pos = None
    pending = None            # +1/-1 : 次バー寄りでエントリー
    cd_until = -1             # cooldown 終了 index (この index 以降に再エントリ可)

    for i in range(N):
        # 1) 予約エントリーを当バー寄りで約定
        just_entered = False
        if pending is not None and pos is None:
            atr_e = atr_vals[i]
            if atr_e > 0 and not np.isnan(atr_e):
                side = pending
                entry = opn[i] + (SPREAD / 2) * side
                risk = trail_mult * atr_e
                stop0 = entry - risk if side == 1 else entry + risk
                pos = {'side': side, 'entry': entry, 'entry_time': time[i],
                       'stop': stop0, 'risk': risk, 'ext': entry, 'bars': 0}
                just_entered = True
            pending = None

        # 2) 出口管理 (エントリー足はスキップ)
        if pos is not None and not just_entered:
            pos['bars'] += 1
            exit_price = None
            reason = None
            # (c) ゲート閉鎖 -> 強制決済 (最優先 / 当バー終値)
            if not gate_open[i]:
                exit_price = close[i]; reason = 'gate'
            else:
                # (b) ATRトレイル
                if pos['side'] == 1:
                    if opn[i] <= pos['stop']:
                        exit_price = opn[i]; reason = 'trail'
                    elif low[i] <= pos['stop']:
                        exit_price = pos['stop']; reason = 'trail'
                else:
                    if opn[i] >= pos['stop']:
                        exit_price = opn[i]; reason = 'trail'
                    elif high[i] >= pos['stop']:
                        exit_price = pos['stop']; reason = 'trail'
                # (a) time-based (トレイル未約定時)
                if exit_price is None and pos['bars'] >= time_max:
                    exit_price = opn[i]; reason = 'time'

            if exit_price is not None:
                gross = (exit_price - pos['entry']) * pos['side']
                net = gross - SPREAD
                trades.append({
                    'entry_time': pos['entry_time'], 'exit_time': time[i],
                    'side': pos['side'], 'entry': pos['entry'], 'exit': exit_price,
                    'risk': pos['risk'], 'pnl_r': net / pos['risk'],
                    'pnl_jpy': net * lot * G.CONTRACT,   # GBPJPY quote_jpy=1.0
                    'bars': pos['bars'], 'reason': reason,
                })
                pos = None
                cd_until = i + cooldown_h
            else:
                # トレイル更新 (1H毎・片側)
                if pos['side'] == 1:
                    pos['ext'] = max(pos['ext'], high[i])
                    pos['stop'] = max(pos['stop'], pos['ext'] - trail_mult * atr_vals[i])
                else:
                    pos['ext'] = min(pos['ext'], low[i])
                    pos['stop'] = min(pos['stop'], pos['ext'] + trail_mult * atr_vals[i])

        # 3) 新規シグナル: ゲート開放中 & flat & cooldown 経過 -> 次バー予約
        if pos is None and pending is None and gate_open[i] and i >= cd_until:
            d = gate_dir[i]
            if d != 0:
                pending = d

    return pd.DataFrame(trades)


def count_gate_opens(gate_open, time_index):
    """ゲート開放(False->True)回数を IS/OOS に振り分けて返す。"""
    opens = []
    prev = False
    for i, o in enumerate(gate_open):
        if o and not prev:
            opens.append(pd.Timestamp(time_index[i]))
        prev = o
    is_end = pd.Timestamp(IS_END)
    oos_start = pd.Timestamp(OOS_START)
    n_is = sum(1 for t in opens if t <= is_end)
    n_oos = sum(1 for t in opens if t >= oos_start)
    return n_is, n_oos


# --- main -------------------------------------------------------------------
def main():
    print(f'PAIR={PAIR}  magic={MAGIC}  BALANCE={BALANCE:,.0f}  recover=+{RECOVER_PCT:.0%}')

    # データ & グリッド再構築 (一度だけ)
    df = G.load_data(PAIR)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df = df[['open', 'high', 'low', 'close']].sort_index()
    df = df[~df.index.duplicated(keep='last')]
    atr_series = G.compute_atr_series(df)
    ci_series = G.compute_ci_series(df)
    atr_vals = atr_series.reindex(df.index).values
    print(f'{PAIR} H1 bars: {len(df)}  ({df.index.min()} .. {df.index.max()})')

    gate_df = grid_gate_series(G.PAIR_CONFIG[PAIR], df, atr_series, ci_series)
    print(f'Grid mtm equity 再構築: net実現={gate_df["realized_cum"].iloc[-1]:,.0f}円  '
          f'min含み込みDD={ (gate_df["equity_mtm"]-gate_df["equity_mtm"].cummax()).min():,.0f}円')

    # ゲート状態 (gate_th 別に precompute)
    gate_cache = {}
    for gth in GRID['gate_th']:
        gate_cache[gth] = compute_gate(gate_df, gth)
        n_is, n_oos = count_gate_opens(gate_cache[gth][0], df.index.values)
        print(f'  gate_th={gth:+.0%}: 開放回数 IS={n_is} / OOS={n_oos}  '
              f'(開放バー比 {gate_cache[gth][0].mean():.1%})')

    # Grid DD上位5区間 (補完評価 / 案Bと同じ COMBINED)
    dd_windows = PT.load_grid_dd_windows(k=5)
    if dd_windows:
        print('\nGrid(COMBINED) DD上位5区間:')
        for j, (p, t) in enumerate(dd_windows, 1):
            seg = 'IS' if t <= pd.Timestamp(IS_END) else 'OOS'
            print(f'  #{j} {p.date()} -> {t.date()}  [{seg}]')

    rows = []
    ledger = {}      # (params)->trades for採用候補
    for gth, tmax, trail, lot, cd in itertools.product(
            GRID['gate_th'], GRID['time_max'], GRID['trail_mult'],
            GRID['lot'], GRID['cooldown_h']):
        gate_open, gate_dir = gate_cache[gth]
        tr = run_gated_backtest(df, atr_vals, gate_open, gate_dir,
                                trail, tmax, lot, cd)
        is_tr = PT.split_seg(tr, IS_START, IS_END)
        oos_tr = PT.split_seg(tr, OOS_START, OOS_END)
        m_is = PT.seg_metrics(is_tr)
        m_oos = PT.seg_metrics(oos_tr)
        is_dd, oos_dd = PT.dd_window_trend_R(tr, dd_windows)
        n_open_is, n_open_oos = count_gate_opens(gate_open, df.index.values)
        key = (f'{gth:+.0%}', tmax, trail, lot, cd)   # wide pivot index と同形式
        ledger[key] = tr
        for seg, m, ddR, nop in [('IS', m_is, is_dd, n_open_is),
                                 ('OOS', m_oos, oos_dd, n_open_oos)]:
            rows.append({'gate_th': f'{gth:+.0%}', 'time_max': tmax, 'trail': trail,
                         'lot': lot, 'cd': cd, 'seg': seg, **m,
                         'ddwin_R': ddR, 'gate_opens': nop})

    res = pd.DataFrame(rows)
    res.to_csv(RESULT_CSV, index=False)
    print(f'\n=== 全{len(ledger)}組合せ スコアカード -> {RESULT_CSV} ===')
    piv = res.pivot_table(index=['gate_th', 'time_max', 'trail', 'lot', 'cd'],
                          columns='seg',
                          values=['N', 'WR', 'PF', 'Sharpe', 'maxDD_pct', 'ddwin_R', 'gate_opens'])
    with pd.option_context('display.width', 260, 'display.max_columns', 80, 'display.max_rows', 400):
        print(piv.to_string())

    # --- 採用基準 ---------------------------------------------------------
    print('\n=== 採用基準: IS[ddwin_R>+5 & PF>1.1] / OOS[ddwin_R>0] ===')
    wide = res.pivot_table(index=['gate_th', 'time_max', 'trail', 'lot', 'cd'],
                           columns='seg', values=['PF', 'ddwin_R', 'N', 'net_R'])
    accepted = []
    for idx, r in wide.iterrows():
        is_pf = r[('PF', 'IS')]; is_dd = r[('ddwin_R', 'IS')]
        oos_dd = r[('ddwin_R', 'OOS')]
        if (np.isfinite(is_pf) and is_pf > 1.1 and is_dd > 5.0 and oos_dd > 0.0):
            accepted.append((idx, is_dd, is_pf, oos_dd))
    accepted.sort(key=lambda x: x[1], reverse=True)   # IS ddwin_R 降順 (補完性最優先)

    if not accepted:
        print('  採用基準を満たす構成なし。')
        # 参考: IS ddwin_R 上位を提示
        cand = [(idx, r[('ddwin_R', 'IS')], r[('PF', 'IS')], r[('ddwin_R', 'OOS')])
                for idx, r in wide.iterrows() if np.isfinite(r[('PF', 'IS')])]
        cand.sort(key=lambda x: x[1], reverse=True)
        print('  参考: IS ddwin_R 上位5 (採用外):')
        for idx, isd, ispf, oosd in cand[:5]:
            print(f'    {idx}: IS ddwin_R={isd:+.2f} PF={ispf:.3f} | OOS ddwin_R={oosd:+.2f}')
    else:
        print(f'  採用 {len(accepted)} 件。上位3詳細:')
        for rank, (idx, isd, ispf, oosd) in enumerate(accepted[:3], 1):
            gth, tmax, trail, lot, cd = idx
            tr = ledger[idx]
            print(f'\n  [#{rank}] gate_th={gth} time_max={tmax} trail={trail} '
                  f'lot={lot} cd={cd}')
            print(f'    IS : {PT.seg_metrics(PT.split_seg(tr, IS_START, IS_END))}  '
                  f'ddwin_R={isd:+.2f}')
            print(f'    OOS: {PT.seg_metrics(PT.split_seg(tr, OOS_START, OOS_END))}  '
                  f'ddwin_R={oosd:+.2f}')

    # ベスト構成のトレード台帳を保存 (採用 -> なければ IS ddwin_R 最大)
    if accepted:
        best_key = accepted[0][0]
    else:
        cand = [(idx, r[('ddwin_R', 'IS')]) for idx, r in wide.iterrows()]
        cand.sort(key=lambda x: (x[1] if np.isfinite(x[1]) else -1e9), reverse=True)
        best_key = cand[0][0]
    best_tr = ledger[best_key].copy()
    if len(best_tr):
        best_tr['entry_time'] = pd.to_datetime(best_tr['entry_time'])
        best_tr = best_tr.sort_values('entry_time')
        best_tr['cum_R'] = best_tr['pnl_r'].cumsum()
        best_tr['seg'] = np.where(best_tr['entry_time'] <= pd.Timestamp(IS_END), 'IS',
                          np.where(best_tr['entry_time'] >= pd.Timestamp(OOS_START), 'OOS', 'GAP'))
        best_tr.to_csv(TRADES_CSV, index=False)
    print(f'\nベスト構成 {best_key} 台帳 -> {TRADES_CSV} (n={len(best_tr)})')

    # --- 2025春 Grid最大DD局面 ピンポイント ------------------------------
    print('\n=== 2025春 Grid最大DD局面でのTrend損益 (ピンポイント) ===')
    spring_p, spring_t = pd.Timestamp('2025-03-16'), pd.Timestamp('2025-05-13')
    print(f'  窓: {spring_p.date()} -> {spring_t.date()} (COMBINED最深DD -4.77M / GBPJPY -3.29M)')
    for label, key in ([('採用#1', accepted[0][0])] if accepted else []) + [('ddwin最大', best_key)]:
        tr = ledger[key]
        daily = PT._daily_R(tr)
        if len(daily):
            wsum = daily.loc[(daily.index >= spring_p) & (daily.index <= spring_t)].sum()
        else:
            wsum = 0.0
        print(f'  [{label}] {key}: Trend損益 = {wsum:+.2f}R')

    # --- 補完性評価 (pullback_grid_complement と同一指標 / ベスト構成) -----
    print('\n=== 補完性評価 (ベスト構成 ' + str(best_key) + ') ===')
    grid_daily = build_grid_daily(['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD'])
    trend_daily = PT._daily_R(ledger[best_key])
    if len(trend_daily):
        lo = max(grid_daily.index.min(), trend_daily.index.min())
        hi = min(grid_daily.index.max(), trend_daily.index.max())
        cal = pd.date_range(lo, hi, freq='D')
        gd = grid_daily.reindex(cal).fillna(0.0)
        td = trend_daily.reindex(cal).fillna(0.0)

        print('  相関係数 (Trend日次R vs Grid日次JPY) [目標 < -0.3]:')
        for col in ['GBPJPY', 'COMBINED']:
            corr = np.corrcoef(gd[col].values, td.values)[0, 1]
            print(f'    {col:9s} 日次(全) = {corr:+.3f}')

        print('  Grid DD上位5区間でのTrend損益合計 [目標 > +5R]:')
        is_dd, oos_dd = PT.dd_window_trend_R(ledger[best_key], dd_windows)
        print(f'    IS={is_dd:+.2f}R  OOS={oos_dd:+.2f}R  合計={is_dd+oos_dd:+.2f}R')

        print('  ブレンドPF (Grid:Trend = 資金スループット比):')
        for gcol in ['GBPJPY', 'COMBINED']:
            g = gd[gcol]
            pf0, dd0, net0 = _pf_dd(g)
            g_turn = np.abs(g.values).sum(); t_turn = np.abs(td.values).sum()
            line = f'    [{gcol}] Grid単体 PF={pf0:.3f}'
            for ratio in [10, 5, 3]:
                r_yen = (g_turn / ratio) / t_turn if t_turn > 0 else 0.0
                pf, dd, net = _pf_dd(g + r_yen * td)
                line += f'  | {ratio}:1 PF={pf:.3f}'
            print(line)
    else:
        print('  ベスト構成のトレードが0件のため補完性評価をスキップ。')


def _pf_dd(daily_pnl):
    v = daily_pnl.values
    gp = v[v > 0].sum(); gl = -v[v < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    return pf, dd, float(v.sum())


if __name__ == '__main__':
    main()
