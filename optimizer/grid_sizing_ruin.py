"""
grid_sizing_ruin.py - Step B: テール → 必要証拠金・安全lot・破産確率・必要口座残高。

入力 = Dukascopy 11年 v7 実績 (grid_floatstop_bt.run_backtest, lot=1.0):
  - 月次PnL系列        → ブロック・ブートストラップで maxDD 分布 → 必要資本/破産確率
  - float-stop / B48 単発損 + ギャップ貫通(A4) → 単発テールで一撃破産しない最低資本
  - 月次平均/分散      → 連続Kelly比 → fractional-Kelly 安全lot

注意:
  - net円は quote_jpy (AUDCAD=CADJPY≈108) 想定でスケール。判定軸はPF/比率(Step A)。円額は概算。
  - lot線形性: grid損益はlotに線形 → 必要資本/テールも lot/1.0 でスケール。
  - 破産(ruin) = 開始資本に対しエクイティ・ドローダウンが資本を食い潰す事象。

実行: python3 optimizer/grid_sizing_ruin.py   出力: grid_sizing_ruin_result.csv + console
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_sizing_ruin_result.csv'
PAIRS = ['AUDCAD', 'NZDJPY', 'CHFJPY', 'GBPJPY']   # AUDCAD先頭(唯一のGo候補)

RNG = np.random.default_rng(42)
N_MC = 20000
BLOCK = 3                 # 月ブロック長(自己相関を一部保持)
HORIZON_MONTHS = 60       # 5年運用を想定したMCホライズン
GAP_BUFFER = {'GBPJPY': 1.83, 'CHFJPY': 1.05, 'NZDJPY': 1.20, 'AUDCAD': 1.10}  # A4 max_ratio


def load_duk(pair):
    df = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    return df.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def monthly_series(res):
    m = res['monthly']
    ks = sorted(m)
    return np.array([m[k] for k in ks], dtype=float)


def block_bootstrap_maxdd(monthly, horizon, n_mc, block):
    """月次PnLをブロック再標本化し horizon ヶ月のエクイティ曲線を生成 → maxDD と最終PnL分布。"""
    n = len(monthly)
    n_blocks = int(np.ceil(horizon / block))
    maxdds = np.empty(n_mc)
    finals = np.empty(n_mc)
    starts_all = RNG.integers(0, n - block + 1, size=(n_mc, n_blocks))
    for i in range(n_mc):
        seq = np.concatenate([monthly[s:s + block] for s in starts_all[i]])[:horizon]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        dd = (peak[1:] - eq)
        maxdds[i] = dd.max()
        finals[i] = eq[-1]
    return maxdds, finals


def main():
    rows = []
    print('=== Step B: 必要証拠金・安全lot・破産確率 (Dukascopy 11年 v7, lot=1.0基準) ===')
    print(f'  MC: {N_MC}回 / horizon={HORIZON_MONTHS}ヶ月 / 月ブロック={BLOCK} / quote_jpy換算')
    for pair in PAIRS:
        cfg = GI.V7_CONFIG[pair]
        df = load_duk(pair)
        res = G.run_backtest(pair, cfg, df, G.compute_atr_series(df), G.compute_ci_series(df))
        m = monthly_series(res)
        n_years = len(m) / 12.0
        net_per_yr = res['total_pnl'] / n_years
        # 単発テール (FS + B48), ギャップ貫通込みの最悪
        fs = np.array(res['fs_events'], dtype=float)
        b48 = np.array([x for x in res['b48_events'] if x < 0], dtype=float)
        single_losses = np.concatenate([fs, b48]) if len(fs) + len(b48) else np.array([0.0])
        worst_single = -single_losses.min() if len(single_losses) else 0.0
        worst_single_gap = worst_single * GAP_BUFFER.get(pair, 1.2)  # 11年外のギャップ余裕
        # 両建て同時stop最悪 (保守: 単発 + 反対側B48相当 ≈ 1.5x) — グリッドは通常片側だが安全側
        two_sided_worst = worst_single_gap * 1.5

        # ブートストラップ maxDD
        maxdds, finals = block_bootstrap_maxdd(m, HORIZON_MONTHS, N_MC, BLOCK)
        dd99 = np.percentile(maxdds, 99)
        dd999 = np.percentile(maxdds, 99.9)
        dd_med = np.percentile(maxdds, 50)

        # 必要資本(lot=1.0): max(DD99, 単発two-sided) を吸収 + 余裕。破産確率<1%基準=DD99。
        req_cap_99 = max(dd99, two_sided_worst)
        req_cap_999 = max(dd999, two_sided_worst)

        # 破産確率: 開始資本 = req_cap_99 のとき maxDD>資本 となる率(定義上≈1%)。
        # 参考に「資本= two_sided_worst のみ」しか無い場合の破産率も出す。
        ruin_at_twosided = float((maxdds > two_sided_worst).mean())
        ruin_at_dd99 = float((maxdds > req_cap_99).mean())

        # 連続Kelly (月次): f* = mean/var (1単位資本あたり). half-Kelly採用.
        mu, var = m.mean(), m.var()
        kelly_full = mu / var if var > 0 else 0.0   # 1円リスクあたりの最適レバ比
        # 安全lot: req_cap_99 を保有資本Cとし、Kellyが許す名目リスク = kelly_full * C.
        # ここでは「DD99 を C の X% に抑える」運用基準で安全lot上限を出す方が実務的:
        #   DD99(lot=1.0) を 口座の 25%(=DD<25%基準) に収める最大lot at 各口座サイズ.

        # 月利目標シナリオ(将来30万=300,000円/月)に必要なlotと口座残高
        tgt_month = 300_000.0
        mean_month_lot1 = mu
        lot_for_target = tgt_month / mean_month_lot1 if mean_month_lot1 > 0 else float('inf')
        # その lot での必要資本(DD99スケール) と DD/残高比
        req_cap_target = req_cap_99 * lot_for_target if np.isfinite(lot_for_target) else float('inf')

        rows.append({
            'pair': pair, 'years': round(n_years, 1), 'pf': res['pf'],
            'net11yr': round(res['total_pnl'], 0), 'net_per_yr': round(net_per_yr, 0),
            'mean_month': round(mu, 0), 'worst_single': round(worst_single, 0),
            'worst_single_gap': round(worst_single_gap, 0), 'two_sided_worst': round(two_sided_worst, 0),
            'maxDD_hist11yr': res['max_dd'], 'mc_dd_med': round(dd_med, 0),
            'mc_dd99': round(dd99, 0), 'mc_dd999': round(dd999, 0),
            'req_cap_99(lot1)': round(req_cap_99, 0), 'req_cap_999(lot1)': round(req_cap_999, 0),
            'ruin@2sided': round(ruin_at_twosided, 4), 'kelly_full_x': round(kelly_full, 6),
            'lot_for_300k/mo': round(lot_for_target, 3) if np.isfinite(lot_for_target) else None,
            'req_cap_300k/mo': round(req_cap_target, 0) if np.isfinite(req_cap_target) else None,
        })

    rdf = pd.DataFrame(rows)
    rdf.to_csv(OUT, index=False)

    # ---- print scorecard ----
    print('\n--- B1. テール & 必要証拠金 (lot=1.0, 円, AUDCAD=CADJPY108概算) ---')
    print(f'{"pair":7s} {"PF":>5s} {"net/yr":>11s} {"mean/mo":>10s} {"worst1":>10s} '
          f'{"worst1+gap":>11s} {"2sided":>11s} {"hist_maxDD":>11s} {"MC_DD99":>11s} {"MC_DD99.9":>11s}')
    for r in rows:
        print(f'{r["pair"]:7s} {r["pf"]:5.2f} {r["net_per_yr"]:11,.0f} {r["mean_month"]:10,.0f} '
              f'{r["worst_single"]:10,.0f} {r["worst_single_gap"]:11,.0f} {r["two_sided_worst"]:11,.0f} '
              f'{r["maxDD_hist11yr"]:11,.0f} {r["mc_dd99"]:11,.0f} {r["mc_dd999"]:11,.0f}')

    print('\n--- B2. 安全資本・破産確率・Kelly (lot=1.0) ---')
    print(f'{"pair":7s} {"req_cap_99":>12s} {"req_cap_999":>12s} {"ruin@2sided":>12s} '
          f'{"kelly_full":>11s} {"halfKelly_note"}')
    for r in rows:
        hk = r['kelly_full_x']
        note = ('Kelly>0(正期待値)' if hk > 0 else 'Kelly<=0(負期待値=張れない)')
        print(f'{r["pair"]:7s} {r["req_cap_99(lot1)"]:12,.0f} {r["req_cap_999(lot1)"]:12,.0f} '
              f'{r["ruin@2sided"]:12.3f} {hk:11.6f}  {note}')

    print('\n--- B3. 月利30万円シナリオ (必要lot & 必要口座残高=DD99×lot) ---')
    print(f'{"pair":7s} {"lot_for_300k":>13s} {"req_account":>13s}  (正期待値ペアのみ意味あり)')
    for r in rows:
        lt = r['lot_for_300k/mo']; rc = r['req_cap_300k/mo']
        print(f'{r["pair"]:7s} {str(lt):>13s} {(f"{rc:,.0f}" if rc else "-"):>13s}')

    print(f'\nsaved {OUT}')
    print('\n注: req_cap_99 = 破産確率≈1%に抑える開始資本(MCのmaxDD 99%ile と単発two-sidedの大きい方)。')
    print('    安全lot = 自己資本 / req_cap_99(lot1)。例 AUDCAD: 口座200万なら lot≈200万/req_cap_99。')


if __name__ == '__main__':
    main()
