"""
pairs_cointegration_screen.py - Stage A: 共和分プレスクリーン(IS only・安価)

新戦略「日足ペアトレード(相関クロスのスプレッド平均回帰)」の Stage A。
確定エッジ「相関クロス=同一ドライバ共有→構造的レンジ」の延長として、同一ドライバを
共有する2銘柄の *相対水準=スプレッド* が共和分なら、その OU 平均回帰を刈れば Grid(絶対
水準のレンジ)とは独立した別エッジ源になりうる、という仮説の事前スクリーン。

過去Close(三角stat_arb 2026-06-07)との区別:
  - 日足(1h でなく) / 全約定 next-bar open / 2脚分コスト差引 / 三角恒等式ペアは除外。
  - ここでは共和分(Engle-Granger)・ヘッジ比・OU半減期・z分布を IS=2015-2021 でのみ算定し、
    ヘッジ比とスプレッド統計の IS->OOS 安定性を確認する。共和分のOOS崩壊は pairs-trade の典型死因。

★重要な構造的注意(beta collapse): 同一クォートの2メジャーの共和分スプレッドは、beta≈1(or -1)
  のとき単一クロス(=既存のtradeable, しばしば既存Goグリッド)に縮約する。
  例: log(AUDUSD)-1*log(NZDUSD)=log(AUDNZD)[Go grid] / log(EURCHF)-log(GBPCHF)=log(EURGBP)[Go grid]
      log(AUDUSD)+log(USDCAD)=log(AUDCAD)[Go grid](=beta -1)。
  → beta が ±1 近傍のペアは「既存エッジの言い換え」で独立でない。このスクリプトは |beta| を出力し、
    beta が 1/-1 から十分離れているか(=本当に2脚の一般化スプレッドか)も判定材料にする。

実行: .venv_dukas/bin/python optimizer/pairs_cointegration_screen.py
出力: pairs_cointegration_screen_result.csv + console
"""
import numpy as np
import pandas as pd
from pathlib import Path
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint, adfuller

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'pairs_cointegration_screen_result.csv'

IS_START, IS_END = '2015-01-01', '2021-12-31'
OOS_START, OOS_END = '2022-01-01', '2026-12-31'


def load_d1(sym):
    """日足 close 系列を返す。D1_dukas があれば優先、無ければ 1h_dukas を D1 リサンプル。"""
    d1 = DATA / f'{sym}_D1_dukas.csv'
    h1 = DATA / f'{sym}_1h_dukas.csv'
    if d1.exists():
        df = pd.read_csv(d1, parse_dates=['datetime'])
        s = df.set_index('datetime')['close']
    elif h1.exists():
        df = pd.read_csv(h1, parse_dates=['datetime'])
        s = df.set_index('datetime')['close'].resample('1D').last().dropna()
    else:
        raise FileNotFoundError(sym)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s


# 候補ペア(同一ドライバ共有・経済的に別物=三角恒等式でない)。
# tag: 'antipodean'(AUD/NZD)・'resource'(資源 vs USD/CAD)・'euro_chf'・'euro_cad'・'cad_chf'
CANDIDATES = [
    # アンティポデアン(beta≈1 だと AUDNZD に縮約しうる -> 後で beta で判定)
    ('AUDUSD', 'NZDUSD', 'antipodean'),
    ('AUDCAD', 'NZDCAD', 'antipodean'),
    ('AUDCHF', 'NZDCHF', 'antipodean'),
    # 資源ブロック(USD/CAD 共通ドライバ。beta≈-1 だと AUDCAD/NZDCAD に縮約しうる)
    ('AUDUSD', 'USDCAD', 'resource'),
    ('NZDUSD', 'USDCAD', 'resource'),
    # 欧州/CHF(beta≈1 だと EURGBP[Go] に縮約しうる)
    ('EURCHF', 'GBPCHF', 'euro_chf'),
    ('GBPCHF', 'CADCHF', 'euro_chf'),
    ('EURCHF', 'CADCHF', 'euro_chf'),
    # 欧州/CAD
    ('EURCAD', 'GBPCAD', 'euro_cad'),
    # CHF safe-haven クロス対 (CADCHF は新Go)
    ('AUDCHF', 'CADCHF', 'cad_chf'),
    ('NZDCHF', 'CADCHF', 'cad_chf'),
]


def ou_halflife(resid):
    """OU 半減期(日)。AR(1): dz_t = a + b*z_{t-1}; hl = -ln2/b。"""
    z = pd.Series(resid).reset_index(drop=True)
    zlag = z.shift(1)
    dz = z - zlag
    d = pd.concat([dz, zlag], axis=1).dropna()
    d.columns = ['dz', 'zlag']
    X = sm.add_constant(d['zlag'])
    res = sm.OLS(d['dz'], X).fit()
    b = res.params['zlag']
    if b >= 0:
        return np.inf
    return -np.log(2) / b


def fit_beta_resid(la, lb):
    """OLS: la = c + beta*lb + resid。beta と resid を返す。"""
    X = sm.add_constant(lb)
    res = sm.OLS(la, X).fit()
    beta = res.params.iloc[1]
    const = res.params.iloc[0]
    resid = la - (const + beta * lb)
    return beta, const, resid


def main():
    rows = []
    for a, b, tag in CANDIDATES:
        try:
            sa, sb = load_d1(a), load_d1(b)
        except FileNotFoundError as e:
            print(f'SKIP {a}/{b}: missing {e}')
            continue
        df = pd.concat([sa.rename('a'), sb.rename('b')], axis=1).dropna()
        la, lb = np.log(df['a']), np.log(df['b'])

        is_m = (df.index >= IS_START) & (df.index <= IS_END)
        oos_m = (df.index >= OOS_START) & (df.index <= OOS_END)
        if is_m.sum() < 500 or oos_m.sum() < 250:
            print(f'SKIP {a}/{b}: thin bars IS={is_m.sum()} OOS={oos_m.sum()}')
            continue

        # --- IS 共和分(Engle-Granger) ---
        t_stat, pval, _ = coint(la[is_m], lb[is_m])
        beta_is, const_is, resid_is = fit_beta_resid(la[is_m], lb[is_m])
        adf_p_is = adfuller(resid_is, autolag='AIC')[1]
        hl_is = ou_halflife(resid_is)
        z_is = (resid_is - resid_is.mean()) / resid_is.std()

        # --- OOS で beta 安定性(IS の beta/const で OOS residを作り直す = 凍結) ---
        resid_oos_frozen = la[oos_m] - (const_is + beta_is * lb[oos_m])
        # OOS で独立推定した beta(安定性チェック用)
        beta_oos, const_oos, resid_oos_re = fit_beta_resid(la[oos_m], lb[oos_m])
        adf_p_oos_frozen = adfuller(resid_oos_frozen, autolag='AIC')[1]
        hl_oos = ou_halflife(resid_oos_re)

        # 凍結residの IS統計で z 化したときの分布(これが Stage B の z)
        mu, sd = resid_is.mean(), resid_is.std()
        z_oos_frozen = (resid_oos_frozen - mu) / sd
        # 凍結スプレッドが OOS で平均回帰の中心からドリフトしていないか
        z_oos_mean = z_oos_frozen.mean()
        z_oos_std = z_oos_frozen.std()

        beta_drift = abs(beta_oos - beta_is) / (abs(beta_is) + 1e-9)
        collapse = abs(abs(beta_is) - 1.0) < 0.25  # beta が ±1 近傍 = 単一クロスへ縮約の疑い

        rows.append(dict(
            pair=f'{a}/{b}', tag=tag, n_is=int(is_m.sum()), n_oos=int(oos_m.sum()),
            eg_t=round(t_stat, 3), eg_p=round(pval, 4),
            adf_p_is=round(adf_p_is, 4), adf_p_oos=round(adf_p_oos_frozen, 4),
            beta_is=round(beta_is, 3), beta_oos=round(beta_oos, 3),
            beta_drift=round(beta_drift, 3), collapse_to_cross=collapse,
            hl_is=round(hl_is, 1), hl_oos=round(hl_oos, 1),
            z_oos_mean=round(z_oos_mean, 2), z_oos_std=round(z_oos_std, 2),
        ))
        print(f'{a}/{b:8s} [{tag:10s}] EGp={pval:.3f} ADFis={adf_p_is:.3f} ADFoos={adf_p_oos_frozen:.3f} '
              f'beta {beta_is:+.2f}->{beta_oos:+.2f} (drift {beta_drift:.2f}) hl {hl_is:.0f}/{hl_oos:.0f}d '
              f'zoos m={z_oos_mean:+.2f} s={z_oos_std:.2f} {"COLLAPSE" if collapse else ""}')

    res = pd.DataFrame(rows)
    res.to_csv(OUT, index=False)
    print(f'\nwrote {OUT}')

    # Stage B 合格基準(事前登録):
    #  共和分: EGp<0.10 ∧ ADF_is<0.10 (IS で共和分)
    #  IS->OOS 安定: ADF_oos(凍結)<0.10 ∧ beta_drift<0.30 ∧ 0.5<hl<60d ∧ |z_oos_mean|<1.0 ∧ z_oos_std in[0.6,1.8]
    #  独立性: not collapse_to_cross (beta が ±1 から離れている)。collapse でも参考で B に通すが「既存縮約」と明記。
    if len(res):
        passA = res[(res.eg_p < 0.10) & (res.adf_p_is < 0.10) & (res.adf_p_oos < 0.10)
                    & (res.beta_drift < 0.30) & (res.hl_is > 0.5) & (res.hl_is < 60)
                    & (res.z_oos_mean.abs() < 1.0) & (res.z_oos_std.between(0.6, 1.8))]
        print('\n=== Stage A 合格(共和分∧IS->OOS安定) ===')
        if len(passA):
            for _, r in passA.iterrows():
                print(f"  {r['pair']:14s} {r['tag']:10s} EGp={r.eg_p} drift={r.beta_drift} "
                      f"hl_is={r.hl_is} {'[COLLAPSE->既存クロス縮約]' if r.collapse_to_cross else '[独立2脚]'}")
        else:
            print('  なし')


if __name__ == '__main__':
    main()
