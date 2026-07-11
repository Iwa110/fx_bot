"""
crypto_tax_gate.py - Stage0 税ゲート (候補C トレンドフォロー実装の"前"に単独で回す)。

背景 (memory: project_crypto_extension_plan_20260711):
    crypto 拡張 C0-C3 完走後、Chat で「候補C(トレンドフォロー)を税を第一級制約にして
    一度だけ決定的に検証し、超えなければ crypto 拡張を凍結する」方針を確定。本スクリプトは
    その第一関門。「戦略実装の前」に税ハードルの高さだけを単独で数値化し、非現実的に高ければ
    C を実装せず即 Close の判断材料にする。

大前提 (Chat で確定):
    国内 crypto = 雑所得・累進最大55%・損益通算なし・繰越なし。
    - 含み益で繰延できる buy&hold は「最終清算で一度だけ 55% 課税」。
    - 能動戦略は毎年 realize → 勝ち年に 55% 課税・負け年は救済なし・繰越なし。
    - よって能動戦略の課税ベース = Σ(positive years) >= buy&hold の課税ベース
      (複利済み最終純利益)。負け年が1つでもあれば厳密に大きい (ほぼ定理)。
    - 帰結: 能動戦略は buy&hold と「同じ税引後資産」を得るためだけに、より多くの税引前利益を
      生まねばならない。この "税引前プレミアム" の高さ = 税ハードル。

税モデル (年次 realize, 損失繰越/通算なし):
    W=1 から開始し各年 r_i を適用。
      gain_i = W*r_i ;  tax_i = 0.55*gain_i  (gain_i>0 のみ, 負の年は 0) ;  W += gain_i - tax_i
    => 勝ち年は (1 + 0.45*r_i)、負け年は (1 + r_i) の係数で複利。
    buy&hold (繰延) は最終清算のみ: after_tax = 1 + (G-1)*0.45  (G>1 のとき, G<=1 は課税なしで G)。

出力:
    各資産×窓 (IS=2017-21 / OOS=2022-26 / FULL) について
      - buy&hold の税引前 gross/CAGR と税引後ターミナル資産
      - それを税引後で "追い抜く" ために能動戦略が必要とする税引前 CAGR
        (ケース: 負け年0本=最も税効率が良い best-case, および 負け年 L 本の劣化)
      - 税引前プレミアム = 必要能動CAGR - buy&hold CAGR
    加えて汎用感度テーブル (仮想 buy&hold CAGR × 能動の負け年本数 → 必要能動税引前CAGR)。

実行 (専用venv):
    .venv_crypto/bin/python optimizer/crypto_tax_gate.py
    .venv_crypto/bin/python optimizer/crypto_tax_gate.py --tax 0.55 --loss-mag 0.20
"""
import argparse
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')

TAX = 0.55                                   # 国内 crypto 雑所得 最高税率 (住民税込)
ASSETS = ['BTCUSDT', 'ETHUSDT']
WINDOWS = {                                  # (start_year, end_year) 両端含む
    'IS(2017-21)': (2017, 2021),
    'OOS(2022-26)': (2022, 2026),
    'FULL(2017-26)': (2017, 2026),
}


# ---------------------------------------------------------------------------
# 税モデル (task で指定された after_tax(annual_realized_returns))
# ---------------------------------------------------------------------------
def after_tax_active(annual_returns, tax=TAX):
    """年次 realize の能動戦略の税引後ターミナル資産倍率 (W0=1)。
       勝ち年に tax を課し・負け年は救済なし・繰越/通算なし。"""
    w = 1.0
    for r in annual_returns:
        gain = w * r
        paid = tax * gain if gain > 0 else 0.0
        w += gain - paid
    return w


def after_tax_bh(gross_multiple, tax=TAX):
    """繰延 buy&hold の税引後ターミナル資産倍率 (最終清算で一度だけ課税)。"""
    gain = gross_multiple - 1.0
    paid = tax * gain if gain > 0 else 0.0
    return 1.0 + gain - paid


# ---------------------------------------------------------------------------
# 必要能動 CAGR: 税引後で目標 (=buy&hold 税引後) を達成する税引前年率
# ---------------------------------------------------------------------------
def required_active_cagr_smooth(target_after_tax, n_years, tax=TAX):
    """負け年0本 (毎年一定 r, 最も税効率が良い best-case) で target を達成する税引前 r。
       (1 + (1-tax)*r)^n = target  =>  r = (target^(1/n) - 1) / (1-tax)。
       target<=1 (buy&hold が損) の場合は r<=0 で足りる可能性 → そのまま返す。"""
    if n_years <= 0:
        return float('nan')
    root = target_after_tax ** (1.0 / n_years)
    return (root - 1.0) / (1.0 - tax)


def required_active_cagr_lumpy(target_after_tax, n_years, n_loss, loss_mag, tax=TAX):
    """負け年 n_loss 本 (各 -loss_mag) + 勝ち年 (n_years-n_loss) 本 (各一定 r_win) で
       target を達成する税引前の勝ち年利率 r_win を数値解。返り値は税引前 CAGR (幾何平均)。
       負け年は税救済なしで W*(1-loss_mag) を掛ける。"""
    n_win = n_years - n_loss
    if n_win <= 0:
        return float('nan')
    loss_factor = (1.0 - loss_mag) ** n_loss              # 負け年の複利係数 (税なし)
    # target = loss_factor * (1 + 0.45*r_win)^n_win  =>  解く
    rhs = target_after_tax / loss_factor
    if rhs <= 0:
        return float('nan')
    win_at = rhs ** (1.0 / n_win)                          # (1 + (1-tax)*r_win)
    r_win = (win_at - 1.0) / (1.0 - tax)
    # 税引前 CAGR: 幾何平均年率 = (prod(1+r_i))^(1/n) - 1
    pre_gross = ((1.0 + r_win) ** n_win) * ((1.0 - loss_mag) ** n_loss)
    return pre_gross ** (1.0 / n_years) - 1.0


def load_close(sym):
    p = os.path.join(DATA, f'{sym}_1d.csv')
    df = pd.read_csv(p, parse_dates=['datetime']).set_index('datetime')
    return df['close']


def window_bh(close, y0, y1):
    seg = close[(close.index.year >= y0) & (close.index.year <= y1)]
    g = float(seg.iloc[-1] / seg.iloc[0])
    yrs = (seg.index[-1] - seg.index[0]).days / 365.25
    n_cal = len({t.year for t in seg.index})               # 能動が realize する暦年数
    return g, yrs, n_cal


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tax', type=float, default=TAX)
    ap.add_argument('--loss-mag', type=float, default=0.20,
                    help='lumpy ケースの負け年 1本あたり損失率 (既定 -20%%)')
    args = ap.parse_args()
    tax = args.tax

    print('=' * 100)
    print(f'Stage0 crypto 税ゲート  税率={tax*100:.0f}% (繰越/通算なし)  '
          f'lumpy 負け年={args.loss_mag*100:.0f}%')
    print('  能動戦略が「繰延 buy&hold の税引後」を追い抜くのに必要な税引前 CAGR を数値化。')
    print('  best = 負け年0本 (最も税効率の良い理想パス) / L1,L2 = 負け年 1,2 本の劣化')
    print('=' * 100)

    rows = []
    for sym in ASSETS:
        close = load_close(sym)
        for wname, (y0, y1) in WINDOWS.items():
            g, yrs, n = window_bh(close, y0, y1)
            bh_cagr = g ** (1.0 / yrs) - 1.0
            bh_at = after_tax_bh(g, tax)                   # buy&hold 税引後ターミナル
            # 能動が達成すべき税引後ターゲット = buy&hold 税引後
            req_best = required_active_cagr_smooth(bh_at, n, tax)
            req_l1 = required_active_cagr_lumpy(bh_at, n, 1, args.loss_mag, tax)
            req_l2 = required_active_cagr_lumpy(bh_at, n, 2, args.loss_mag, tax)
            rows.append(dict(asset=sym, window=wname, n_years=n, bh_gross=g,
                             bh_cagr=bh_cagr, bh_after_tax=bh_at,
                             req_best=req_best, req_l1=req_l1, req_l2=req_l2,
                             premium_best=req_best - bh_cagr))
            print(f'\n{sym}  {wname}  (暦年 N={n}, 実期間 {yrs:.2f}y)')
            print(f'  buy&hold : gross={g:6.2f}x  税引前CAGR={bh_cagr*100:6.1f}%  '
                  f'-> 税引後ターミナル={bh_at:5.2f}x')
            if bh_at <= 1.0:
                print(f'  => buy&hold は税引後でも元本割れ ({bh_at:.2f}x)。'
                      f'能動は税引前CAGR {req_best*100:+.1f}% で追い抜ける (低ハードル窓)。')
            else:
                print(f'  必要 能動 税引前CAGR : best(負け0本)={req_best*100:6.1f}%  '
                      f'L1={req_l1*100:6.1f}%  L2={req_l2*100:6.1f}%')
                print(f'  => 税引前プレミアム(best) = 必要{req_best*100:.1f}% - buy&hold'
                      f'{bh_cagr*100:.1f}% = {(req_best-bh_cagr)*100:+.1f}%pt '
                      f'(能動はこれだけ余分に税引前で稼がねば追い抜けない)')

    df = pd.DataFrame(rows)
    out = os.path.join(HERE, 'crypto_tax_gate_result.csv')
    df.to_csv(out, index=False)

    # ------------------------------------------------------------------
    # 汎用感度テーブル: 仮想 buy&hold CAGR × 能動の負け年本数 → 必要能動税引前CAGR
    # N=5 年窓を想定 (IS/OOS と同じ)。
    # ------------------------------------------------------------------
    print('\n' + '=' * 100)
    print('汎用感度: N=5年窓, buy&hold の税引前CAGR ごとに 能動が必要とする税引前CAGR')
    print(f'  (負け年 0/1/2 本, 負け年損失={args.loss_mag*100:.0f}%, 税率={tax*100:.0f}%)')
    print('=' * 100)
    N = 5
    print(f'{"BH CAGR":>9} | {"BH税引後":>8} | {"必要能動CAGR best":>16} | '
          f'{"L1":>7} | {"L2":>7} | {"premium(best)":>13}')
    print('-' * 80)
    sens = []
    for bh_cagr in [0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.70]:
        g = (1.0 + bh_cagr) ** N
        bh_at = after_tax_bh(g, tax)
        rb = required_active_cagr_smooth(bh_at, N, tax)
        r1 = required_active_cagr_lumpy(bh_at, N, 1, args.loss_mag, tax)
        r2 = required_active_cagr_lumpy(bh_at, N, 2, args.loss_mag, tax)
        sens.append(dict(bh_cagr=bh_cagr, bh_after_tax=bh_at, req_best=rb,
                         req_l1=r1, req_l2=r2, premium_best=rb - bh_cagr))
        print(f'{bh_cagr*100:8.0f}% | {bh_at:7.2f}x | {rb*100:15.1f}% | '
              f'{r1*100:6.1f}% | {r2*100:6.1f}% | {(rb-bh_cagr)*100:+12.1f}%pt')
    pd.DataFrame(sens).to_csv(os.path.join(HERE, 'crypto_tax_gate_sensitivity.csv'),
                              index=False)

    print('\n' + '=' * 100)
    print('判定材料 (pre-registered):')
    print('  - IS(強気相場)窓は buy&hold が 10x 超 → 税引後を追い抜く税引前ハードルが')
    print('    非現実的に高ければ、その窓での C 採用は原理的に不可能。')
    print('  - OOS/レンジ・弱気窓で buy&hold が低い/元本割れなら、能動が回避できれば勝てる')
    print('    余地がある = C 本体 BT で検証する価値の有無を左右する。')
    print(f'  結果 CSV: {out}')
    print('=' * 100)


if __name__ == '__main__':
    main()
