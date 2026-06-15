"""
grid_corrcross_screen.py - Stage A: 相関クロスの構造プレスクリーン。

確定エッジ「相関クロスのGrid平均回帰」を未検証クロスへスケールできるか、まず
構造メトリクスで候補宇宙をランクし、Goアンカーの構造圏内にある候補のみを Stage B
(grid_corrcross_screen_bt.py)へ通す。新エッジ探索でなく確定エッジの横展開。

事前知見(project_grid_yearly_pf_diag_20260611): GoとNo-Goの差は「年間トレンドの中央値」
でなく「強制決済(FS+B48)の頻度」。Go=<2回/年・TP:FS≈100:1、No-Go=2.4-6.3回/年。
→ 構造メトリクス:
  (1) trend_atr    : |年間純変位| / 中央値ATR(1h)  (小さいほどレンジ的=Go寄り)
  (2) path_eff     : |年間純変位| / Σ|日次変位|     (低いほどレンジ的)
  (3) fs_per_yr    : テンプレGridの年あたり強制決済(FS+B48)回数 (Go<2が目安)
  (4) tp_fs_ratio  : TP数 / max(FS+B48,1) の年中央値    (Go≈100:1が損益分岐目安)
  (5) gate_share   : CI>閾値バー比率(Gridが建てに行く時間割合)

較正アンカー: Go=AUDCAD/EURGBP/AUDNZD、No-Go=CHFJPY/GBPJPY/EURCHF/EURUSD。
候補が Go アンカーの構造分布(trend_atr/path_eff/fs_per_yr)に入るか/No-Go側かで足切り。

quote_jpy は各ペアのquote通貨のJPYレート概算。float_stop は AUDCAD 基準で price距離を
正規化(grid_yearly_pf_diag と同一手法)し FS頻度をペア横断で比較可能にする。

実行: .venv_dukas/bin/python optimizer/grid_corrcross_screen.py
出力: grid_corrcross_screen_result.csv (年次明細) + grid_corrcross_screen_rank.csv (ペア集計)
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D

OUTYR = Path(__file__).resolve().parent / 'grid_corrcross_screen_result.csv'
OUTRK = Path(__file__).resolve().parent / 'grid_corrcross_screen_rank.csv'

# quote通貨 -> JPYレート概算(PnL換算係数)。JPY-quote=1.0。
QUOTE_JPY = {
    'AUDCAD': 108.0, 'EURGBP': 190.0, 'AUDNZD': 90.0,          # Go アンカー
    'CHFJPY': 1.0, 'GBPJPY': 1.0, 'EURCHF': 170.0, 'EURUSD': 155.0,  # No-Go アンカー
    # 候補(quote通貨で決まる)
    'NZDCAD': 108.0,   # quote CAD
    'GBPCHF': 170.0, 'AUDCHF': 170.0, 'NZDCHF': 170.0, 'CADCHF': 170.0,  # quote CHF
    'EURCAD': 108.0, 'GBPCAD': 108.0,  # quote CAD
    'EURAUD': 100.0, 'GBPAUD': 100.0,  # quote AUD
    'EURNZD': 92.0,  'GBPNZD': 92.0,   # quote NZD
}

GROUP = {  # 較正アンカー / 候補(経済ブロック)
    'AUDCAD': 'ANCHOR_GO', 'EURGBP': 'ANCHOR_GO', 'AUDNZD': 'ANCHOR_GO',
    'CHFJPY': 'ANCHOR_NOGO', 'GBPJPY': 'ANCHOR_NOGO', 'EURCHF': 'ANCHOR_NOGO', 'EURUSD': 'ANCHOR_NOGO',
    'NZDCAD': 'CAND_RESOURCE', 'GBPCHF': 'CAND_EUROPE',
    'AUDCHF': 'CAND_CHF', 'NZDCHF': 'CAND_CHF', 'CADCHF': 'CAND_CHF',
    'EURCAD': 'CAND_MIXED', 'GBPCAD': 'CAND_MIXED', 'EURAUD': 'CAND_MIXED',
    'EURNZD': 'CAND_MIXED', 'GBPAUD': 'CAND_MIXED', 'GBPNZD': 'CAND_MIXED',
}

PAIRS = list(GROUP.keys())


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def year_stats(df, atr, ci, ci_th, year):
    m = (df.index.year == year)
    sub = df[m]
    if len(sub) < 2000:
        return None
    a_med = float(atr.reindex(sub.index).median())
    if not np.isfinite(a_med) or a_med <= 0:
        return None
    c0, c1 = sub['close'].iloc[0], sub['close'].iloc[-1]
    daily = sub['close'].resample('D').last().dropna()
    dsum = float(daily.diff().abs().sum())
    civ = ci.reindex(sub.index)
    return {
        'trend_atr': abs(c1 - c0) / a_med,
        'path_eff': abs(c1 - c0) / dsum if dsum > 0 else 0.0,
        'rng_atr': (sub['high'].max() - sub['low'].min()) / a_med,
        'gate_share': float((civ > ci_th).mean()),
    }


def main():
    # AUDCAD 基準で float_stop price距離を正規化(FS頻度をペア横断で比較可能に)
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * QUOTE_JPY['AUDCAD']

    rows = []
    missing = []
    for pair in PAIRS:
        try:
            df = D.load_duk(pair)
        except FileNotFoundError:
            print(f'[{pair}] data無し, skip'); missing.append(pair); continue
        if len(df) < 20000:
            print(f'[{pair}] 薄データ {len(df)}本, 構造評価のみ(Stage B不可)')
        atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        qj = QUOTE_JPY[pair]
        fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
        cfg = template_cfg(qj, fs)
        span = f'{df.index[0].date()}~{df.index[-1].date()}'
        print(f'\n=== {pair} [{GROUP[pair]}] {len(df)}本 {span} fs={fs:,.0f} ===')
        print(f'{"yr":>4} {"PF":>6} {"nTP":>5} {"nFS":>4} {"nB48":>5} '
              f'{"trend_atr":>9} {"path_eff":>8} {"gate%":>6}')
        for y in range(2015, 2027):
            sub = df[df.index.year == y]
            if len(sub) < 2000:
                continue
            r = D.run_bt(cfg, sub, atr, ci)
            s = year_stats(df, atr, ci, cfg['ci_threshold'], y)
            if s is None:
                continue
            nforce = r['n_fstop'] + r['n_b48']
            rows.append({'pair': pair, 'grp': GROUP[pair], 'year': y, 'pf': r['pf'],
                         'net': r['total_pnl'], 'n_tp': r['n_tp'], 'n_fs': r['n_fstop'],
                         'n_b48': r['n_b48'], 'n_force': nforce,
                         'tp_fs_ratio': r['n_tp'] / max(nforce, 1),
                         'max_dd': r['max_dd'], 'worst': r['worst_event'], **s})
            print(f'{y:>4} {r["pf"]:>6.2f} {r["n_tp"]:>5d} {r["n_fstop"]:>4d} {r["n_b48"]:>5d} '
                  f'{s["trend_atr"]:>9.0f} {s["path_eff"]:>8.2f} {s["gate_share"]*100:>5.0f}%')

    out = pd.DataFrame(rows)
    out.to_csv(OUTYR, index=False)

    # ── ペア集計(完全年=本数十分の年のみ) ──
    agg = out.groupby('pair').agg(
        grp=('grp', 'first'), n_years=('year', 'nunique'),
        trend_atr_med=('trend_atr', 'median'), path_eff_med=('path_eff', 'median'),
        fs_per_yr=('n_force', 'mean'), tp_fs_ratio_med=('tp_fs_ratio', 'median'),
        gate_share_med=('gate_share', 'median'), pf_med=('pf', 'median'),
        win_yr_rate=('net', lambda s: float((s > 0).mean())),
    ).reset_index()

    # Go アンカーの構造圏(エンベロープ)を定義
    go = agg[agg.grp == 'ANCHOR_GO']
    nogo = agg[agg.grp == 'ANCHOR_NOGO']
    env = {
        'trend_atr_max': float(go['trend_atr_med'].max()),
        'path_eff_max': float(go['path_eff_med'].max()),
        'fs_per_yr_max': float(go['fs_per_yr'].max()),
        'tp_fs_ratio_min': float(go['tp_fs_ratio_med'].min()),
    }
    print('\n' + '=' * 110)
    print('Go アンカー構造エンベロープ(候補がこの圏内ならStage Bへ):')
    print(f'  trend_atr_med <= {env["trend_atr_max"]:.0f}   path_eff_med <= {env["path_eff_max"]:.3f}   '
          f'fs_per_yr <= {env["fs_per_yr_max"]:.1f}   tp_fs_ratio_med >= {env["tp_fs_ratio_min"]:.0f}')
    print(f'  (参考 No-Go: trend_atr_med {nogo["trend_atr_med"].min():.0f}-{nogo["trend_atr_med"].max():.0f}  '
          f'fs_per_yr {nogo["fs_per_yr"].min():.1f}-{nogo["fs_per_yr"].max():.1f})')

    def in_env(r):
        score = 0
        score += r['trend_atr_med'] <= env['trend_atr_max']
        score += r['path_eff_med'] <= env['path_eff_max']
        score += r['fs_per_yr'] <= env['fs_per_yr_max']
        score += r['tp_fs_ratio_med'] >= env['tp_fs_ratio_min']
        return score
    agg['env_score'] = agg.apply(in_env, axis=1)  # 0-4. 構造圏内度
    # Stage B 推奨 = 候補 かつ env_score>=3 (4基準中3つ以上Go圏内)
    agg['stageB'] = (agg.grp.str.startswith('CAND')) & (agg.env_score >= 3)

    agg = agg.sort_values(['grp', 'fs_per_yr'])
    agg.to_csv(OUTRK, index=False)

    print('\n' + '=' * 110)
    print('構造ランク(grp / trend_atr / path_eff / fs_per_yr / tp_fs / gate% / pf_med / 黒字年率 / env / StageB)')
    print('=' * 110)
    for _, r in agg.sort_values(['env_score', 'fs_per_yr'], ascending=[False, True]).iterrows():
        flag = '★StageB' if r['stageB'] else ('anchor' if r['grp'].startswith('ANCHOR') else '-')
        print(f'  {r["pair"]:7s} {r["grp"]:14s} trend={r["trend_atr_med"]:>4.0f} '
              f'peff={r["path_eff_med"]:.3f} fs/yr={r["fs_per_yr"]:>4.1f} '
              f'tp:fs={r["tp_fs_ratio_med"]:>5.0f} gate={r["gate_share_med"]*100:>3.0f}% '
              f'pf={r["pf_med"]:.2f} win={r["win_yr_rate"]*100:>3.0f}% env={r["env_score"]}/4  {flag}')

    cands = agg[agg.stageB]['pair'].tolist()
    print(f'\n>>> Stage B 推奨候補: {cands if cands else "なし(Go圏内の候補無し)"}')
    if missing:
        print(f'>>> データ欠損(取得失敗): {missing}')
    print(f'\nsaved {OUTYR}\nsaved {OUTRK}')


if __name__ == '__main__':
    main()
