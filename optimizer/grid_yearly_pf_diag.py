"""
grid_yearly_pf_diag.py - Grid No-Goペア(PF<1.2)の年次PF算出と原因診断。

各ペア×各年(2015-2026)で baseline Grid を回し、年次PFと同年の相場特性を突き合わせる:
  trend_atr  : |年間リターン| / 中央値ATR(1h) = 年のネット・トレンド距離(ATR本数)
  path_eff   : |年間リターン| / Σ|日次リターン| = 経路効率(1に近いほど一方向トレンド)
  rng_atr    : 年高値-年安値 / 中央値ATR = 年のレンジ幅
  gate_share : CI>閾値のバー比率(ゲート開放率=Gridが建てに行く時間の割合)
  fs_loss_sh : 年のgross_lossに占めるFS/B48イベント損の割合

仮説: No-Goペアの負け年は trend_atr/path_eff が高い年(トレンド焼け)に集中し、
Goペア(AUDCAD/EURGBP)はそもそも trend_atr が構造的に低い(相関通貨クロス)。

設定は grid_dd_reduction_transfer.py と同一(確立済みテンプレ・チューニング無し)。
実行: .venv_dukas/bin/python optimizer/grid_yearly_pf_diag.py
出力: grid_yearly_pf_diag.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D

OUT = Path(__file__).resolve().parent / 'grid_yearly_pf_diag.csv'

V7 = GI.V7_CONFIG


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def run_year(cfg, df, atr, ci, year):
    m = (df.index.year == year)
    sub = df[m]
    if len(sub) < 1000:
        return None
    return D.run_bt(cfg, sub, atr, ci)


def year_market_stats(df, atr, ci, ci_th, year):
    m = (df.index.year == year)
    sub = df[m]
    if len(sub) < 1000:
        return None
    a_med = float(atr.reindex(sub.index).median())
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
    # 転移検証と同一の設定群
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref_atr_jpy = float(atr_ac.median()) * 108.0

    pairs = {
        'AUDCAD': ('GO', D.AUDCAD),
        'EURGBP': ('GO', D.EURGBP),
        'GBPJPY': ('NOGO', {**V7['GBPJPY']}),
        'CHFJPY': ('NOGO', {**V7['CHFJPY']}),
        'NZDJPY': ('NOGO', {**V7['NZDJPY']}),
        'AUDNZD': ('NOGO', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0))),
        'EURCHF': ('NOGO', template_cfg(170.0, round(-750_000.0 * 170.0 / 108.0, 0))),
        'USDJPY': ('NOGO', None),
        'EURUSD': ('NOGO', None),
    }
    explore_qj = {'USDJPY': 1.0, 'EURUSD': 155.0}

    rows = []
    for pair, (grp, cfg) in pairs.items():
        df = D.load_duk(pair)
        atr = G.compute_atr_series(df)
        ci = G.compute_ci_series(df)
        if cfg is None:
            qj = explore_qj[pair]
            fs = round(-750_000.0 * (float(atr.median()) * qj) / ref_atr_jpy, 0)
            cfg = template_cfg(qj, fs)
        print(f'\n=== {pair} [{grp}] ===')
        print(f'{"year":>5} {"PF":>6} {"net":>12} {"nTP":>5} {"nFS":>4} '
              f'{"trend_atr":>9} {"path_eff":>8} {"rng_atr":>8} {"gate%":>6}')
        for y in range(2015, 2027):
            r = run_year(cfg, df, atr, ci, y)
            s = year_market_stats(df, atr, ci, cfg['ci_threshold'], y)
            if r is None or s is None:
                continue
            rows.append({'pair': pair, 'grp': grp, 'year': y, 'pf': r['pf'],
                         'net': r['total_pnl'], 'n_tp': r['n_tp'], 'n_fs': r['n_fstop'],
                         'n_b48': r['n_b48'], 'max_dd': r['max_dd'],
                         'worst': r['worst_event'], **s})
            print(f'{y:>5} {r["pf"]:>6.2f} {r["total_pnl"]:>12,.0f} {r["n_tp"]:>5d} '
                  f'{r["n_fstop"]:>4d} {s["trend_atr"]:>9.0f} {s["path_eff"]:>8.2f} '
                  f'{s["rng_atr"]:>8.0f} {s["gate_share"]*100:>5.0f}%')

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')

    # ── 集計分析 ──
    print('\n' + '=' * 100)
    print('原因分析サマリー')
    print('=' * 100)
    out['win'] = out['net'] > 0
    out['logpf'] = np.log(out['pf'].clip(0.05, 20))

    print('\n[1] 年次PF と相場特性の相関 (全ペア・年プール, Spearman)')
    for v in ['trend_atr', 'path_eff', 'rng_atr', 'gate_share']:
        c = out['logpf'].corr(out[v], method='spearman')
        print(f'  logPF vs {v:11s}: {c:+.3f}')

    print('\n[2] 負け年(net<0) vs 勝ち年 の相場特性 (中央値)')
    g = out.groupby('win')[['trend_atr', 'path_eff', 'rng_atr', 'gate_share', 'n_fs']].median()
    print(g.round(3).to_string())

    print('\n[3] グループ別の構造比較 (中央値: Go 2ペア vs No-Go 7ペア)')
    g2 = out.groupby('grp')[['pf', 'trend_atr', 'path_eff', 'rng_atr', 'gate_share', 'n_fs']].median()
    print(g2.round(3).to_string())
    print('\n  黒字年率: ' + ' / '.join(
        f'{grp}={out[out.grp == grp]["win"].mean()*100:.0f}%' for grp in ['GO', 'NOGO']))

    print('\n[4] ペア別: 黒字年率・負け年のtrend_atr超過')
    for pair in out['pair'].unique():
        sp = out[out['pair'] == pair]
        lose = sp[~sp['win']]; winy = sp[sp['win']]
        print(f'  {pair:7s} 黒字{winy.shape[0]:2d}/{sp.shape[0]:2d}年  '
              f'med PF={sp["pf"].median():.2f}  '
              f'負け年trend_atr={lose["trend_atr"].median() if len(lose) else float("nan"):.0f} '
              f'vs 勝ち年={winy["trend_atr"].median() if len(winy) else float("nan"):.0f}  '
              f'負け年path_eff={lose["path_eff"].median() if len(lose) else float("nan"):.2f} '
              f'vs 勝ち年={winy["path_eff"].median() if len(winy) else float("nan"):.2f}')


if __name__ == '__main__':
    main()
