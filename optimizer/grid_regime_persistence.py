"""
grid_regime_persistence.py - E1: 地合い(regime)持続性・先行予測力テスト。

問い: 「今年このペアが grid 向きか」を事前予測できるか。年次PF診断
(grid_yearly_pf_diag.py)で「grid PFは path_eff/trend_atr と逆相関・FS頻度が決め手」
と判明済=何が地合いを決めるかは既知。残る問いは **それが先行的に予測可能か**
(=trailing の状態指標が forward の grid 損益を当てるか)。

これが決まらないと動的地合い案(A2 lotスロットル/C3 hostile手仕舞い/E2 VRゲート/
E3 throttle/E4 配分チルト)は全て前提を欠く。持続性が無ければ動的地合い系は全Close、
静的+構造ゲートへ集約する。

設計(確定Grid 4本・暦月基盤・lot1.0):
  1. 各ペアの月次 grid PnL を DB.run_bt(collect=True) で取得(確定構成)。暦月0埋め。
  2. 各暦月末で **trailing** 状態指標を価格のみから causal に算出(K=3/6/12ヶ月):
       path_eff  : |終-始| / Σ|日次変位|     (1=完全トレンド, 0=往復レンジ)
       vr5       : variance ratio q=5 (日次logret)  (<1=平均回帰, >1=トレンド)
       trend_z   : |終-始| / (trailing日次vol×√日数)  (トレンド距離のz)
       gate_sh   : trailing 期間の CI>th バー比率 (=CIが既に拾う coincident 対照)
  3. forward grid PnL = 次の H ヶ月(H=3/6/12)の月次PnL合計。
  4. 検定:
     (P) 持続性 : corr(feat_t, feat_{t+H})  — regime はそもそも持続するか
     (A) 予測力 : Spearman(feat_t, fwd_grid_pnl)  — IS / OOS 別に算出
     (G) AR(1)  : 月次grid PnL自体の自己相関 (良月/悪月は続くか)
  採用条件(動的地合いが正当化される):
     ① |Spearman(feat, fwd_pnl)| が有意 ∧ 符号が path_eff↑→pnl↓(=-)
     ② その符号が **IS と OOS の両方で一致** (動的化Closeの教訓=IS↔OOS逆相関を棄却)
     ③ feature 自体に持続性(AR)がある
  ①〜③のどれかが崩れれば「予測力ゼロ、静的CIで十分」を採択=動的地合い系Close。

実行: .venv_dukas/bin/python optimizer/grid_regime_persistence.py
出力: grid_regime_persistence_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
from scipy import stats
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY

OUT = Path(__file__).resolve().parent / 'grid_regime_persistence_result.csv'
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
CI_TH = 65.0
IS_END = pd.Timestamp('2021-12-31', tz='UTC')


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def cadchf_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df)
    qj = QUOTE_JPY['CADCHF']
    fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
    return template_cfg(qj, fs)


def build_defs():
    """確定Grid 4本(grid_joint_stepb.build_defs と同一)。"""
    return [
        ('AUDCAD', D.AUDCAD, lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('CADCHF', cadchf_cfg(), lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200)}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)),
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('EURGBP', D.EURGBP, lambda df, atr: {'short_lot_mult': 0.5, **COMBO}),
    ]


def monthly_pnl(pair, cfg, kwfn):
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    res = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kwfn(df, atr))
    return df, res['monthly']


def variance_ratio(logret, q):
    """VR(q) = Var(q期リターン)/(q*Var(1期リターン))。<1平均回帰 >1トレンド。"""
    r = logret[~np.isnan(logret)]
    if len(r) < q * 3:
        return np.nan
    v1 = np.var(r, ddof=1)
    if v1 <= 0:
        return np.nan
    rq = np.add.reduceat(r, np.arange(0, len(r) - len(r) % q, q))  # 非重複q期和
    if len(rq) < 3:
        return np.nan
    return float(np.var(rq, ddof=1) / (q * v1))


def trailing_features(daily, ci_daily_open, month_end):
    """month_end(暦月末)時点で trailing K ヶ月の causal 特徴。daily=日次close。"""
    out = {}
    logret_all = np.log(daily).diff()
    for k in (3, 6, 12):
        start = month_end - pd.DateOffset(months=k)
        win = daily[(daily.index > start) & (daily.index <= month_end)]
        if len(win) < 20:
            for nm in ('pe', 'vr5', 'tz', 'gate'):
                out[f'{nm}_{k}'] = np.nan
            continue
        disp = abs(win.iloc[-1] - win.iloc[0])
        dsum = win.diff().abs().sum()
        out[f'pe_{k}'] = disp / dsum if dsum > 0 else np.nan
        lr = logret_all[(logret_all.index > start) & (logret_all.index <= month_end)].to_numpy()
        out[f'vr5_{k}'] = variance_ratio(lr, 5)
        vol = np.nanstd(lr, ddof=1)
        out[f'tz_{k}'] = (abs(np.log(win.iloc[-1] / win.iloc[0])) / (vol * np.sqrt(len(lr)))
                          if vol > 0 and len(lr) > 1 else np.nan)
        g = ci_daily_open[(ci_daily_open.index > start) & (ci_daily_open.index <= month_end)]
        out[f'gate_{k}'] = float(g.mean()) if len(g) else np.nan
    return out


def build_panel(pair, cfg, kwfn):
    df, monthly = monthly_pnl(pair, cfg, kwfn)
    daily = df['close'].resample('D').last().dropna()
    ci = G.compute_ci_series(df)
    ci_daily_open = (ci > CI_TH).resample('D').mean().dropna()  # 日次のゲート開放割合
    # 暦月レンジ(0埋め=活動の無い月もPnL=0)
    if not monthly:
        return None
    months = pd.period_range(min(monthly), max(monthly), freq='M')
    rows = []
    for per in months:
        me = per.to_timestamp(how='end').tz_localize('UTC')
        feat = trailing_features(daily, ci_daily_open, me)
        rows.append({'pair': pair, 'month': str(per), 'me': me,
                     'pnl': monthly.get(str(per), 0.0), **feat})
    panel = pd.DataFrame(rows)
    # forward grid PnL (次のHヶ月合計)
    for h in (3, 6, 12):
        panel[f'fwd_{h}'] = panel['pnl'][::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)
    return panel


def spear(a, b):
    m = (~pd.isna(a)) & (~pd.isna(b))
    if m.sum() < 12:
        return np.nan, np.nan, int(m.sum())
    rho, p = stats.spearmanr(a[m], b[m])
    return float(rho), float(p), int(m.sum())


def main():
    print('=== E1: 地合い持続性・先行予測力テスト (確定Grid 4本 / 暦月基盤 / Dukascopy 11年) ===\n')
    panels = []
    for pair, cfg, kwfn in build_defs():
        p = build_panel(pair, cfg, kwfn)
        if p is not None:
            panels.append(p)
            act = (p['pnl'] != 0).sum()
            print(f'{pair}: 暦月 {len(p)} (活動月 {act})  '
                  f'net/yr={p["pnl"].sum()/(len(p)/12):,.0f}')
    panel = pd.concat(panels, ignore_index=True)
    panel['is_oos'] = np.where(panel['me'] <= IS_END, 'IS', 'OOS')

    feats = [f'{nm}_{k}' for nm in ('pe', 'vr5', 'tz', 'gate') for k in (3, 6, 12)]

    # ── (G) 月次grid PnL の自己相関(良月/悪月の持続) ──
    print('\n' + '=' * 96)
    print('[G] 月次 grid PnL の自己相関 (lag1/3/6, ペア別・暦月0埋め)  — 良月/悪月は続くか')
    print('=' * 96)
    for pair in panel['pair'].unique():
        s = panel[panel.pair == pair].set_index('month')['pnl']
        acs = []
        for lag in (1, 3, 6):
            a, b = s.iloc[:-lag], s.shift(-lag).iloc[:-lag]
            r, pv, n = spear(a.reset_index(drop=True), b.reset_index(drop=True))
            acs.append(f'lag{lag}={r:+.2f}(p={pv:.2f})')
        print(f'  {pair:7s} ' + '  '.join(acs))

    # ── (P) regime feature 自体の持続性 ──
    print('\n' + '=' * 96)
    print('[P] regime 特徴の持続性: corr(feat_t, feat_{t+3ヶ月})  — regime はそもそも持続するか')
    print('=' * 96)
    print(f'{"feat":8s} ' + '  '.join(f'{p:>10s}' for p in panel['pair'].unique()) + '   pooled')
    persist_rows = []
    for f in feats:
        cells = []
        for pair in panel['pair'].unique():
            s = panel[panel.pair == pair].reset_index(drop=True)[f]
            r, pv, n = spear(s.iloc[:-3].reset_index(drop=True), s.shift(-3).iloc[:-3].reset_index(drop=True))
            cells.append(r)
        # pooled
        pr, ppv, pn = spear(panel.groupby('pair')[f].shift(0).reset_index(drop=True),
                            panel.groupby('pair')[f].shift(-3).reset_index(drop=True))
        print(f'{f:8s} ' + '  '.join(f'{c:>+10.2f}' for c in cells) + f'   {pr:+.2f}')
        persist_rows.append({'metric': 'persist3', 'feat': f, 'pooled': pr})

    # ── (A) 予測力: trailing feat → forward grid PnL, IS/OOS別 ──
    print('\n' + '=' * 96)
    print('[A] 先行予測力: Spearman(trailing feat_t, forward grid PnL)  — IS vs OOS で符号一致するか')
    print('    採用には path_eff/tz/vr が負(トレンド↑→PnL↓) ∧ IS/OOS同符号 ∧ 有意 が必要')
    print('=' * 96)
    rows = []
    for h in (3, 6, 12):
        print(f'\n--- forward H={h}ヶ月 ---')
        print(f'{"feat":8s} {"IS_rho":>8s} {"IS_p":>7s} {"IS_n":>5s} | '
              f'{"OOS_rho":>8s} {"OOS_p":>7s} {"OOS_n":>5s} | {"同符号":>6s} {"判定":>4s}')
        for f in feats:
            res = {}
            for grp in ('IS', 'OOS'):
                sub = panel[panel.is_oos == grp]
                r, pv, n = spear(sub[f].reset_index(drop=True), sub[f'fwd_{h}'].reset_index(drop=True))
                res[grp] = (r, pv, n)
            ir, ip, inn = res['IS']; o_r, op, on = res['OOS']
            same = (not np.isnan(ir)) and (not np.isnan(o_r)) and (np.sign(ir) == np.sign(o_r))
            # 採用signature: path_eff/tz/vr 系は負・両期有意(<0.10)・同符号
            want_neg = f.split('_')[0] in ('pe', 'vr5', 'tz')
            ok = (same and not np.isnan(ip) and ip < 0.10 and op < 0.10
                  and ((ir < 0) if want_neg else True))
            print(f'{f:8s} {ir:>+8.2f} {ip:>7.3f} {inn:>5d} | '
                  f'{o_r:>+8.2f} {op:>7.3f} {on:>5d} | {("Yes" if same else "No"):>6s} '
                  f'{("採用?" if ok else "-"):>4s}')
            rows.append({'h': h, 'feat': f, 'is_rho': ir, 'is_p': ip, 'is_n': inn,
                         'oos_rho': o_r, 'oos_p': op, 'oos_n': on, 'same_sign': same, 'pass': ok})

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    n_pass = int(out['pass'].sum())
    print('\n' + '=' * 96)
    print(f'採用signature通過セル数: {n_pass} / {len(out)}')
    if n_pass == 0:
        print('→ trailing regime は forward grid PnL を IS/OOS一貫して予測しない。')
        print('  動的地合い系(A2/C3/E2-4)は前提を欠く=Close。静的+構造ゲート(D1)へ集約。')
    else:
        print('→ 一部セルが通過。プラトー/局面依存を要精査(単一H・単一feat の偶然を排除)。')
    print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
