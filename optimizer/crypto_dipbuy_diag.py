"""
crypto_dipbuy_diag.py - 補助診断 D: 清算後 snapback が取引可能な形で残っているか (安価な Stage0 却下判定)。

問い (task):
    「4h 足で清算後 snapback が残っているか = daily dip-buy が buy&hold を税引後で超えるか」だけを
    安価に診断。snapback が同時点で織り込まれ取引可能なリード/ラグが無ければ本BT不要で Close
    (コモディティ→FX の lag 診断と同型の Stage0 却下)。

手法 (本BTは組まない・純・条件付き統計):
    - 日足 close-to-close リターンを算出。
    - 「大きな下落日」(下位 q 分位, 例 下位20%) の *翌日* リターンを条件付きで集計。
    - 取引可能性 = 翌日リターン平均が往復コスト(例0.6%)を差し引いても正か。
      (同時点で織り込まれるなら翌日期待値≈0 → 取引不能 → Close)
    - lag0(同日) vs lag+1(翌日=取引可能) を対比し、相関/期待値が lag0 のみなら却下。

実行:
    .venv_crypto/bin/python optimizer/crypto_dipbuy_diag.py
"""
import argparse
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')
ASSETS = ['BTCUSDT', 'ETHUSDT']


def load_close(sym):
    p = os.path.join(DATA, f'{sym}_1d.csv')
    return pd.read_csv(p, parse_dates=['datetime']).set_index('datetime')['close']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--q', type=float, default=0.20, help='「大下落日」の下位分位')
    ap.add_argument('--cost-frac', type=float, default=0.006)
    args = ap.parse_args()

    print('=' * 96)
    print(f'補助診断 D: 大下落日(下位{args.q*100:.0f}%)の翌日 snapback は取引可能か  '
          f'往復コスト={args.cost_frac*100:.2f}%')
    print('  lag+1 期待値がコスト差引後も有意に正でなければ = 織り込み済で取引不能 → Close 材料')
    print('=' * 96)

    rows = []
    for sym in ASSETS:
        c = load_close(sym)
        r = c.pct_change().dropna()                      # 日次リターン
        thr = r.quantile(args.q)
        big_drop = r <= thr                              # 大下落日 (当日)
        nxt = r.shift(-1)                                # 翌日リターン (lag+1 = 取引可能)
        cond = nxt[big_drop].dropna()
        uncond = r
        # lag0 = 同日の下落自体(定義上負), lag+1 = 翌日
        mean_next = cond.mean()
        # t 統計 (平均が0と異なるか)
        se = cond.std() / np.sqrt(len(cond))
        tstat = mean_next / se if se > 0 else float('nan')
        net_next = mean_next - args.cost_frac            # 往復コスト差引 (dip-buy 1回転)
        # 1日先 自己相関 (snapback= 負の autocorr なら平均回帰の名残)
        ac1 = r.autocorr(lag=1)
        rows.append(dict(asset=sym, drop_thr=thr, n_drop=int(big_drop.sum()),
                         mean_next=mean_next, tstat=tstat, net_next=net_next,
                         uncond_mean=uncond.mean(), autocorr1=ac1))
        print(f'\n{sym}:')
        print(f'  大下落日しきい値 = {thr*100:6.2f}%  該当日数 = {int(big_drop.sum())}')
        print(f'  翌日リターン平均 (lag+1, 取引可能) = {mean_next*100:+6.3f}%  '
              f't={tstat:+.2f}  (無条件平均 {uncond.mean()*100:+.3f}%)')
        print(f'  コスト差引後 dip-buy 期待値 = {net_next*100:+6.3f}%  '
              f'{"→ 正(要精査)" if net_next > 0 else "→ 負 = 取引不能"}')
        print(f'  日次 lag1 自己相関 = {ac1:+.4f}  '
              f'{"(負=平均回帰の名残)" if ac1 < 0 else "(非負=snapback無)"}')

    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'crypto_dipbuy_diag_result.csv'), index=False)
    print('\n' + '=' * 96)
    print('判定: 翌日期待値がコスト差引後に負 or t<2 なら snapback は取引可能な形で残っていない')
    print('  = 同時点で織り込み済 → daily dip-buy 本BT不要で Close (Stage0 型却下)。')
    print('=' * 96)


if __name__ == '__main__':
    main()
