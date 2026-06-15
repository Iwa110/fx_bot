"""
commodity_fx_lag_diag.py - コモディティ -> 資源通貨 リード/ラグ診断 (2026-06-15)

目的(本BT前の関門):
    コモディティ(金/原油/銅)が資源通貨(AUD/CAD/NZD/CHF)をD1で
    「同時点で完全に織り込む(ラグ0=取引不能)」のか、
    「翌日以降に予測力を残す(ラグ>=1=取引可能)」のかを判定する。
    ラグ0が支配的でラグ>=1の相関がゼロなら、本戦略は構造的に取引不能 -> 即Close。

診断:
    1. 各 (commodity, fx) ペアで lag k = -3..+3 の日次リターン相互相関。
       k>0 = commodity(t-k) -> fx(t) = 取引可能(commodity が先行)。
       k=0 = 同時点。
       検定は IS=2015-2021 のみで実施(OOSは本BTに温存)。
    2. ★主軸: 金属-原油スプレッド -> AUDCAD 方向。
       AUD=金属(金/銅), CAD=原油 -> 両者乖離時に AUDCAD がトレンド化(=Grid出血窓)。
       spread_ret = metal_ret - oil_ret が AUDCAD(t) を先行予測するか。

規律: 全特徴 t-1 視点。相関は係数でなく「ラグ0 vs ラグ>=1 の予測力配分」で読む。
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'
IS_START, IS_END = '2015-01-01', '2021-12-31'


def load_d1_close(name):
    df = pd.read_csv(DATA / f'{name}_D1_dukas.csv', parse_dates=['datetime'])
    df['date'] = df['datetime'].dt.normalize()
    s = df.groupby('date')['close'].last()
    return s


def load_1h_d1_close(name):
    df = pd.read_csv(DATA / f'{name}_1h_dukas.csv', parse_dates=['datetime'])
    df['date'] = df['datetime'].dt.normalize()
    return df.groupby('date')['close'].last()


def main():
    # --- コモディティ(USD建て) ---
    commod = {k: load_d1_close(k) for k in ['XAUUSD', 'WTI', 'BRENT', 'COPPER', 'XAGUSD']}
    # --- 資源通貨の対USD強さ ---
    audusd = load_d1_close('AUDUSD')
    nzdusd = load_d1_close('NZDUSD')
    usdcad = load_d1_close('USDCAD')
    usdchf = load_d1_close('USDCHF')
    fx = {
        'AUD': audusd,
        'NZD': nzdusd,
        'CAD': 1.0 / usdcad,   # CAD per USD を反転 -> CADの対USD強さ
        'CHF': 1.0 / usdchf,
    }
    # クロス(Grid対象)
    audcad = load_1h_d1_close('AUDCAD')
    cadchf = load_1h_d1_close('CADCHF')

    def logret(s):
        return np.log(s).diff()

    cret = {k: logret(v) for k, v in commod.items()}
    fret = {k: logret(v) for k, v in fx.items()}
    audcad_r = logret(audcad)
    cadchf_r = logret(cadchf)

    lags = range(-3, 4)

    def xcorr(drv, tgt, label):
        df = pd.concat({'d': drv, 't': tgt}, axis=1).dropna()
        df = df[(df.index >= IS_START) & (df.index <= IS_END)]
        n = len(df)
        row = {'pair': label, 'n': n}
        for k in lags:
            # k>0: driver(t-k) vs target(t) -> driverが先行
            c = df['d'].shift(k).corr(df['t'])
            row[f'lag{k:+d}'] = round(c, 3)
        return row

    print(f'=== コモディティ -> 資源通貨 ラグ相関 (IS {IS_START}..{IS_END}) ===')
    print('lag>0 = commodity先行(取引可能) / lag0 = 同時点 / 単位: 日次logret相関\n')

    econ_pairs = [
        ('XAUUSD', 'AUD'), ('XAUUSD', 'CHF'), ('COPPER', 'AUD'), ('COPPER', 'NZD'),
        ('WTI', 'CAD'), ('BRENT', 'CAD'), ('XAGUSD', 'AUD'),
        ('XAUUSD', 'NZD'), ('WTI', 'AUD'),  # 対照(弱い経済関係)
    ]
    rows = [xcorr(cret[c], fret[f], f'{c}->{f}') for c, f in econ_pairs]

    # ★主軸: 金属-原油スプレッド -> AUDCAD / CADCHF
    metal = cret['XAUUSD']
    oil = cret['WTI']
    spread = metal - oil  # AUD寄り - CAD寄り
    rows.append(xcorr(spread, audcad_r, 'METAL-OIL->AUDCAD'))
    rows.append(xcorr(cret['COPPER'] - oil, audcad_r, 'COPPER-OIL->AUDCAD'))
    rows.append(xcorr(oil - metal, cadchf_r, 'OIL-METAL->CADCHF'))

    out = pd.DataFrame(rows)
    cols = ['pair', 'n'] + [f'lag{k:+d}' for k in lags]
    out = out[cols]
    pd.set_option('display.width', 200)
    print(out.to_string(index=False))

    # 判定サマリ: lag0 と lag+1 の絶対値比較
    print('\n=== 判定: |lag0| vs |lag+1| (取引可能性) ===')
    for _, r in out.iterrows():
        l0, l1 = abs(r['lag+0']), abs(r['lag+1'])
        verdict = 'TRADEABLE?' if l1 >= 0.05 else 'contemporaneous(取引不能寄り)'
        print(f"{r['pair']:22s} |lag0|={l0:.3f} |lag+1|={l1:.3f}  {verdict}")

    out.to_csv(Path(__file__).resolve().parent / 'commodity_fx_lag_diag_result.csv', index=False)
    print('\nsaved -> commodity_fx_lag_diag_result.csv')


if __name__ == '__main__':
    main()
