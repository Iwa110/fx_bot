"""
commodity_fx_mom_diag.py - コモディティ・モメンタム -> 資源通貨 予測力診断 (2026-06-15)

lag診断(commodity_fx_lag_diag.py)で単日リターンのリード/ラグはゼロ(同時点relation)と判明。
早すぎるCloseを避けるため「複数日モメンタム」仮説を点検:
    コモディティの直近 W日リターン(t-1で確定) が、資源通貨の今後 H日リターンを予測するか。
    W,H in {1,5,20}。全て t-1 視点(ルックアヘッド無し)。IS=2015-2021。
これもゼロなら「コモディティ->資源通貨は同時点でのみ結合・取引可能な予測力無し」を確定。
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'
IS_START, IS_END = '2015-01-01', '2021-12-31'


def d1(name):
    df = pd.read_csv(DATA / f'{name}_D1_dukas.csv', parse_dates=['datetime'])
    df['date'] = df['datetime'].dt.normalize()
    return df.groupby('date')['close'].last()


def main():
    commod = {k: d1(k) for k in ['XAUUSD', 'WTI', 'COPPER']}
    fx = {'AUD': d1('AUDUSD'), 'NZD': d1('NZDUSD'),
          'CAD': 1.0 / d1('USDCAD'), 'CHF': 1.0 / d1('USDCHF')}

    pairs = [('XAUUSD', 'AUD'), ('XAUUSD', 'CHF'), ('COPPER', 'AUD'),
             ('COPPER', 'NZD'), ('WTI', 'CAD')]
    windows = [1, 5, 20]

    print(f'=== コモディティ W日モメンタム(t-1) -> 資源通貨 H日先リターン 相関 (IS) ===')
    print('全て t-1 確定特徴。|corr|<~0.05 はノイズ(n~2000, 2sigma≈0.045)\n')
    rows = []
    for c, f in pairs:
        cs = np.log(commod[c])
        fs = np.log(fx[f])
        df = pd.concat({'c': cs, 'f': fs}, axis=1, sort=True).dropna()
        df = df[(df.index >= IS_START) & (df.index <= IS_END)]
        for W in windows:
            mom = df['c'].diff(W).shift(1)  # t-1で確定したW日コモディティ・モメンタム
            for H in windows:
                fwd = df['f'].shift(-H) - df['f']  # t..t+H の資源通貨先リターン
                c_ = mom.corr(fwd)
                rows.append({'pair': f'{c}->{f}', 'W': W, 'H': H, 'corr': round(c_, 3)})
    out = pd.DataFrame(rows)
    piv = out.pivot_table(index='pair', columns=['W', 'H'], values='corr')
    pd.set_option('display.width', 250)
    print(piv.to_string())
    print(f'\n全|corr|最大 = {out["corr"].abs().max():.3f}')
    out.to_csv(Path(__file__).resolve().parent / 'commodity_fx_mom_diag_result.csv', index=False)
    print('saved -> commodity_fx_mom_diag_result.csv')


if __name__ == '__main__':
    main()
