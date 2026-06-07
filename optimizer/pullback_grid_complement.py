"""
pullback_grid_complement.py - P2: Trend(プルバック) と Grid の補完性分析。

目的:
  Trend戦略は単体ではPF<1(エッジ無し / pullback_trend_bt.py)。だが Grid(レンジ回帰)は
  トレンド局面でfloat-stop/B48を踏んで損失を出す。両者が「いつ勝つか」がズレている
  (負相関)なら、ブレンドで Grid単体より PF改善 / DD圧縮 が可能 = 補完弁としての価値。

手順:
  1. Grid のBT損益を「実現イベントの日付つき時系列」として再構築
     (grid_floatstop_bt.py のロジック/configを忠実に踏襲。ライブv7 GBPJPY等)。
  2. Trend(pullback_trend_bt_trades.csv)の決済損益を日次R系列に集計。
  3. Grid日次PnL と Trend日次PnL の相関係数(Pearson/Spearman)。
  4. Grid DD上位5区間で Trend損益を集計(Gridが最も痛い時にTrendが稼ぐか)。
  5. 補完PF: combined = Grid_jpy + k*Trend_R を k(=Trend1Rの円換算)でスイープし、
     combined の PF / maxDD を Grid単体と比較。

出力: optimizer/pullback_grid_complement_result.csv (相関・DD区間・ブレンドPF表)
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G

OUT_DIR = Path(__file__).resolve().parent
TRADES_CSV = OUT_DIR / 'pullback_trend_bt_trades.csv'
RESULT_CSV = OUT_DIR / 'pullback_grid_complement_result.csv'

# 解析対象Gridペア: 実マネー候補(GBPJPY最優先, AUDCAD次点) + ライブv7 combined
GRID_PAIRS = ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD']


# --- Grid: 日付つき実現PnLイベントを再構築 (grid_floatstop_bt のロジック忠実複製) ----
def grid_pnl_events(pair, cfg, df, atr_series, ci_series):
    """戻り値: list[(timestamp, pnl_jpy)] 全実現イベント(TP/float-stop/B48)。"""
    G._QJ = cfg.get('quote_jpy', 1.0)
    lot = cfg['lot']
    atr_mult, ci_threshold = cfg['atr_mult'], cfg['ci_threshold']
    b48_hours, max_levels, float_stop = cfg['b48_hours'], cfg['max_levels'], cfg['float_stop']

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    events = []

    def pj(pd_diff):
        return G.pnl_jpy(pd_diff, lot)

    for ts, row in df.iterrows():
        atr = atr_series.get(ts)
        ci = ci_series.get(ts)
        if pd.isna(atr) or atr <= 0:
            continue
        gw = atr * atr_mult
        bar_h, bar_l, bar_cl = row['high'], row['low'], row['close']
        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        # TP
        for p in [p for p in long_pos if bar_h >= p['tp']]:
            events.append((ts, pj(p['tp'] - p['entry']))); long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            events.append((ts, pj(p['entry'] - p['tp']))); short_pos.remove(p)

        # FLOAT STOP
        if long_pos:
            unreal = sum(pj(bar_l - p['entry']) for p in long_pos)
            if unreal <= float_stop:
                events.append((ts, sum(pj(bar_l - p['entry']) for p in long_pos)))
                long_pos = []; b48_long_start = None
        if short_pos:
            unreal = sum(pj(p['entry'] - bar_h) for p in short_pos)
            if unreal <= float_stop:
                events.append((ts, sum(pj(p['entry'] - bar_h) for p in short_pos)))
                short_pos = []; b48_short_start = None

        # B48 timer reset
        if long_was_max and len(long_pos) < max_levels:
            b48_long_start = None
        if short_was_max and len(short_pos) < max_levels:
            b48_short_start = None

        # B48 expiry
        if b48_long_start is not None and (ts - b48_long_start).total_seconds() / 3600.0 >= b48_hours:
            events.append((ts, sum(pj(bar_cl - p['entry']) for p in long_pos)))
            long_pos = []; b48_long_start = None
        if b48_short_start is not None and (ts - b48_short_start).total_seconds() / 3600.0 >= b48_hours:
            events.append((ts, sum(pj(p['entry'] - bar_cl) for p in short_pos)))
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

    return events


def build_grid_daily(pairs):
    """各ペア + combined の日次実現PnL(JPY)を返す。index=naive date。"""
    per_pair = {}
    for pair in pairs:
        try:
            df = G.load_data(pair)
        except FileNotFoundError:
            print(f'[SKIP] {pair} data not found'); continue
        atr_s = G.compute_atr_series(df)
        ci_s = G.compute_ci_series(df)
        ev = grid_pnl_events(pair, G.PAIR_CONFIG[pair], df, atr_s, ci_s)
        if not ev:
            continue
        s = pd.Series([v for _, v in ev], index=[t for t, _ in ev])
        s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
        per_pair[pair] = s.groupby(level=0).sum()
    daily = pd.DataFrame(per_pair).fillna(0.0)
    daily['COMBINED'] = daily.sum(axis=1)
    return daily


def build_trend_daily():
    """Trend決済損益(R)を決済日でまとめた日次系列。"""
    t = pd.read_csv(TRADES_CSV, parse_dates=['entry_time', 'exit_time'])
    t['date'] = t['exit_time'].dt.normalize()
    return t.groupby('date')['pnl_r'].sum()


def pf_and_dd(daily_pnl):
    """日次PnL系列から PF / maxDD / net を計算。"""
    v = daily_pnl.values
    gp = v[v > 0].sum()
    gl = -v[v < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    return pf, dd, float(v.sum())


def top_dd_windows(daily_pnl, k=5):
    """日次PnLのドローダウン episode を抽出し、深い順 k 件返す。
    各episode = (peak_date, trough_date, depth)。"""
    s = daily_pnl.sort_index()
    eq = s.cumsum()
    peak = eq.cummax()
    dd = eq - peak  # <=0
    episodes = []
    in_dd = False
    start = None
    peak_val = None
    for i, (d, val) in enumerate(dd.items()):
        if not in_dd and val < 0:
            in_dd = True
            start = s.index[i - 1] if i > 0 else d
        if in_dd:
            if val == 0:  # recovered
                trough = dd.loc[start:d].idxmin()
                episodes.append((start, trough, d, float(dd.loc[trough])))
                in_dd = False
    if in_dd:  # 未回復で末尾
        seg = dd.loc[start:]
        trough = seg.idxmin()
        episodes.append((start, trough, s.index[-1], float(seg.min())))
    episodes.sort(key=lambda e: e[3])  # 深い(負)順
    return episodes[:k]


def main():
    print('=== P2: Trend x Grid 補完性分析 ===\n')
    grid_daily = build_grid_daily(GRID_PAIRS)
    trend_daily = build_trend_daily()

    g_range = (grid_daily.index.min(), grid_daily.index.max())
    t_range = (trend_daily.index.min(), trend_daily.index.max())
    print(f'Grid  期間: {g_range[0].date()} ~ {g_range[1].date()}  (events日数={len(grid_daily)})')
    print(f'Trend 期間: {t_range[0].date()} ~ {t_range[1].date()}  (trade日数={len(trend_daily)})')

    # 共通日付グリッド(両者0埋め, union)で揃える
    lo = max(g_range[0], t_range[0])
    hi = min(g_range[1], t_range[1])
    cal = pd.date_range(lo, hi, freq='D')
    gd = grid_daily.reindex(cal).fillna(0.0)
    td = trend_daily.reindex(cal).fillna(0.0)
    print(f'共通解析期間: {lo.date()} ~ {hi.date()} ({len(cal)}日)\n')

    rows = []

    # --- 相関係数 (日次/週次) ---
    print('--- 相関係数 (Trend日次R vs Grid日次JPY) ---')
    for col in list(grid_daily.columns):
        g = gd[col]
        # 両方ゼロの日を除くと活動日のみの相関も見える -> 両方掲載
        pear_all = np.corrcoef(g.values, td.values)[0, 1]
        active = (g != 0) | (td != 0)
        pear_act = np.corrcoef(g[active].values, td[active].values)[0, 1] if active.sum() > 2 else np.nan
        # 週次集計の相関(ノイズ低減)
        gw = g.resample('W').sum(); tw = td.resample('W').sum()
        pear_w = np.corrcoef(gw.values, tw.values)[0, 1]
        print(f'  {col:9s}  日次(全)={pear_all:+.3f}  日次(活動日)={pear_act:+.3f}  週次={pear_w:+.3f}')
        rows.append({'metric': 'corr', 'grid': col, 'daily_all': round(pear_all, 3),
                     'daily_active': round(pear_act, 3), 'weekly': round(pear_w, 3)})

    # --- Grid DD上位5区間でのTrend損益 ---
    print('\n--- Grid(COMBINED) DD上位5区間 と その間のTrend損益(R) ---')
    eps = top_dd_windows(gd['COMBINED'], k=5)
    for j, (p, tr, end, depth) in enumerate(eps, 1):
        # DD区間 = peak -> trough(損失蓄積レッグ)で評価
        tr_sum = td.loc[p:tr].sum()
        g_sum = gd['COMBINED'].loc[p:tr].sum()
        print(f'  #{j} peak {p.date()} -> trough {tr.date()}: '
              f'Grid={g_sum:>12,.0f}  深さ={depth:>12,.0f}  Trend={tr_sum:+7.2f}R')
        rows.append({'metric': f'ddwin{j}', 'grid': 'COMBINED',
                     'start': p.date(), 'trough': tr.date(),
                     'grid_pnl': round(g_sum), 'dd_depth': round(depth),
                     'trend_R': round(tr_sum, 2)})

    # 同じくGBPJPY単体(実マネー最優先)
    if 'GBPJPY' in gd.columns:
        print('\n--- Grid(GBPJPY) DD上位5区間 と その間のTrend損益(R) ---')
        eps_g = top_dd_windows(gd['GBPJPY'], k=5)
        for j, (p, tr, end, depth) in enumerate(eps_g, 1):
            tr_sum = td.loc[p:tr].sum()
            g_sum = gd['GBPJPY'].loc[p:tr].sum()
            print(f'  #{j} peak {p.date()} -> trough {tr.date()}: '
                  f'Grid={g_sum:>12,.0f}  深さ={depth:>12,.0f}  Trend={tr_sum:+7.2f}R')
            rows.append({'metric': f'ddwin_gbpjpy{j}', 'grid': 'GBPJPY',
                         'start': p.date(), 'trough': tr.date(),
                         'grid_pnl': round(g_sum), 'dd_depth': round(depth),
                         'trend_R': round(tr_sum, 2)})

    # --- 補完PF: combined = Grid + k*Trend_R をスイープ ---
    print('\n--- 補完PF: combined_daily = Grid_jpy + (R_yen)*Trend_R ---')
    for gcol in ['GBPJPY', 'COMBINED']:
        if gcol not in gd.columns:
            continue
        g = gd[gcol]
        pf0, dd0, net0 = pf_and_dd(g)
        pft, ddt, nett = pf_and_dd(td)
        print(f'\n  [{gcol}] Grid単体 : PF={pf0:.3f}  maxDD={dd0:,.0f}  net={net0:,.0f}')
        print(f'  [{gcol}] Trend単体: PF={pft:.3f}  maxDD={ddt:.1f}R net={nett:+.1f}R (R単位)')
        rows.append({'metric': 'blend', 'grid': gcol, 'R_yen': 0,
                     'pf': round(pf0, 3), 'maxDD': round(dd0), 'net': round(net0)})
        for r_yen in [10_000, 25_000, 50_000, 100_000, 200_000, 400_000]:
            combined = g + r_yen * td
            pf, dd, net = pf_and_dd(combined)
            tag = '  <- DD最小化候補' if False else ''
            print(f'    R={r_yen:>8,}円/trade : combined PF={pf:.3f}  '
                  f'maxDD={dd:>12,.0f}  net={net:>14,.0f}{tag}')
            rows.append({'metric': 'blend', 'grid': gcol, 'R_yen': r_yen,
                         'pf': round(pf, 3), 'maxDD': round(dd), 'net': round(net)})

        # 比率指定ブレンド (Grid:Trend = 10:1 / 5:1 / 3:1)。
        # 比率 = 資金スループット比。R_yen = (1/ratio) * Grid総スループット / Trend総R(絶対)。
        g_turn = np.abs(g.values).sum()
        t_turn = np.abs(td.values).sum()
        print(f'  [{gcol}] 比率ブレンド (資金スループット比 Grid:Trend):')
        for ratio in [10, 5, 3]:
            r_yen = (g_turn / ratio) / t_turn if t_turn > 0 else 0.0
            combined = g + r_yen * td
            pf, dd, net = pf_and_dd(combined)
            print(f'    {ratio:>2d}:1  (R≈{r_yen:>9,.0f}円/trade) : combined PF={pf:.3f}  '
                  f'maxDD={dd:>12,.0f}  net={net:>14,.0f}  (Grid単体PF={pf0:.3f})')
            rows.append({'metric': 'blend_ratio', 'grid': gcol, 'ratio': f'{ratio}:1',
                         'R_yen': round(r_yen), 'pf': round(pf, 3),
                         'maxDD': round(dd), 'net': round(net)})

    pd.DataFrame(rows).to_csv(RESULT_CSV, index=False)
    print(f'\nSaved: {RESULT_CSV}')


if __name__ == '__main__':
    main()
