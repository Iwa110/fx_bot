"""
crypto_basis_research_bt.py - 候補B(研究のみ): デルタ中立ファンディング・ベーシス収穫。

背景 (memory: project_crypto_extension_plan_20260711):
    現物ロング + 無期限ショート(デルタ中立)でファンディングを収穫する carry。
    構造的理由=レバレッジ需要プレミアム(強気相場ではロングが混み funding>0 → ショート側が収穫)。
    市場中立で、carry long-only Grid(USDJPY/NZDJPY)の crypto 版に相当。
    無期限先物が前提=海外取引所。★国内現物のみでは今すぐ執行不可。本BTは
    「税55%+繰越なし+取引所カウンターパーティ・リスクを上回るエッジか」を数字で見て
    将来の海外拡張の是非(C3)を判断する研究目的。

モデル(第一次近似・honest):
    - デルタ中立(spot long N + perp short N)ゆえ価格P&L≈0(funding が perp≈spot に収束を強制)。
      → 建玉リターン ≒ Σ funding − コスト。basis ノイズは funding に対し小さいので無視し、
        代わりに保守的な rebalance ドラッグと counterparty ヘアカットで安全側に寄せる。
    - 収穫符号: funding>0 で perp short が受取、funding<0 で支払い(実 funding 履歴をそのまま使用)。
    - フルコスト: 建て/解消の往復手数料(spot+perp) 一括 + 連続 rebalance ドラッグ(年率)。
    - ★税ハードル: 国内 crypto 雑所得55%・損失繰越なし(海外執行でも居住地課税は同じ)。
      年次収益に55%課税・負の年は救済なしで税引後利回りを算定。
    - テール: 負ファンディング局面(弱気相場=2022等)/ 最大DD / 清算ストレス。

判定(海外拡張の是非): 税引後・コスト後・カウンターパーティヘアカット後の年率が、
    海外取引所リスク(破綻/出金停止=FXより現実的)を負ってなお十分な水準か。
    参考ハードル: 国内で得られる無リスク相当を大きく超え、かつ全年プラス圏で頑健か。

実行 (専用venv):
    .venv_crypto/bin/python optimizer/crypto_basis_research_bt.py
    .venv_crypto/bin/python optimizer/crypto_basis_research_bt.py --fee-rt 0.003 --rebal-drag 0.008
"""
import argparse
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / 'data'
JP_CRYPTO_TAX = 0.55
INTERVALS_PER_YEAR = 3 * 365          # 8h ファンディング = 1日3回
SYMBOLS = ['BTCUSDT', 'ETHUSDT']


def load_funding(sym, exchange='binance'):
    p = DATA_DIR / f'FUNDING_{sym}_{exchange}.csv'
    if not p.exists():
        raise SystemExit(f'[fatal] {p} が無い。先に fetch_funding_rates.py を実行。')
    df = pd.read_csv(p, parse_dates=['datetime'])
    df = df.sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
    df['year'] = df['datetime'].dt.year
    return df


def analyze(sym, fee_rt, rebal_drag, haircut, exchange='binance'):
    df = load_funding(sym, exchange)
    fr = df['funding_rate'].to_numpy(dtype=float)
    # 連続 rebalance ドラッグを各インターバルに配賦(年率 → per-interval)
    drag_pi = rebal_drag / INTERVALS_PER_YEAR
    net_pi = fr - drag_pi                          # per-interval 収穫(コスト後, 建て/解消手数料は別途)

    # --- 年次集計 ---
    g = df.assign(fr=fr, net=net_pi).groupby('year')
    yearly = g.agg(n=('fr', 'size'), fund_sum=('fr', 'sum'),
                   fund_mean=('fr', 'mean'), neg_share=('fr', lambda s: (s < 0).mean())).reset_index()
    # 年率(その年の実測インターバル数でスケールせず、実収穫合計 − ドラッグ配賦)
    yearly['gross_yr'] = yearly['fund_sum']                       # その年の funding 合計(≒年利回り)
    yearly['drag_yr'] = yearly['n'] * drag_pi
    yearly['net_yr'] = yearly['gross_yr'] - yearly['drag_yr']     # コスト後(建て解消手数料は初年度のみ)
    # 建て/解消手数料は運用初年度に一括計上(継続保有=1回だけ)
    first_year = yearly['year'].iloc[0]
    yearly['net_yr'] = yearly['net_yr'] - np.where(yearly['year'] == first_year, fee_rt, 0.0)
    # counterparty ヘアカット(取引所リスクの期待コストを年率で控除)
    yearly['net_yr_hc'] = yearly['net_yr'] - haircut
    # 税引後(年次・55%・負の年は救済なし・繰越なし)
    yearly['tax'] = np.where(yearly['net_yr_hc'] > 0, yearly['net_yr_hc'] * JP_CRYPTO_TAX, 0.0)
    yearly['aftertax_yr'] = yearly['net_yr_hc'] - yearly['tax']

    # --- 全期間 maxDD(累積収穫曲線, コスト後・税引前) ---
    eq = np.cumsum(net_pi)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
    max_dd = float((peak[1:] - eq).max())
    # 負ファンディング最長連続(支払いが続く弱気ストレス, 日数換算)
    neg = fr < 0
    longest = cur = 0
    for v in neg:
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    return {'sym': sym, 'yearly': yearly, 'max_dd': max_dd,
            'neg_share_all': float(neg.mean()), 'longest_neg_streak_days': longest / 3.0,
            'fr_p01': float(np.percentile(fr, 1)), 'fr_p99': float(np.percentile(fr, 99)),
            'n': len(fr)}


def summarize(res, is_end_year=2022):
    """IS(<=is_end_year) / OOS(>is_end_year) の税引後年率中央値等を要約。"""
    y = res['yearly']
    full = y[y['n'] > 100]                       # 部分年(<~100本)を除外
    is_y = full[full['year'] <= is_end_year]
    oos_y = full[full['year'] > is_end_year]
    def med(s): return float(s.median()) if len(s) else float('nan')
    return {
        'gross_yr_med_full': med(full['gross_yr']),
        'net_yr_med_full': med(full['net_yr']),
        'aftertax_med_full': med(full['aftertax_yr']),
        'aftertax_med_is': med(is_y['aftertax_yr']),
        'aftertax_med_oos': med(oos_y['aftertax_yr']),
        'aftertax_min_full': float(full['aftertax_yr'].min()) if len(full) else float('nan'),
        'neg_years': int((full['net_yr_hc'] <= 0).sum()),
        'n_years': int(len(full)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fee-rt', type=float, default=0.003,
                    help='建て+解消 往復手数料(spot+perp, 一括)。既定0.3%%')
    ap.add_argument('--rebal-drag', type=float, default=0.008,
                    help='連続 rebalance ドラッグ(年率)。既定0.8%%')
    ap.add_argument('--haircut', type=float, default=0.0,
                    help='counterparty リスクの年率ヘアカット(既定0=総括で別途議論)')
    ap.add_argument('--exchange', default='binance')
    args = ap.parse_args()

    print('#' * 96)
    print('# 候補B(研究のみ)  デルタ中立ファンディング・ベーシス収穫  '
          f'exchange={args.exchange}')
    print(f'# fee_rt={args.fee_rt*100:.2f}%(一括) rebal_drag={args.rebal_drag*100:.2f}%/yr '
          f'haircut={args.haircut*100:.2f}%/yr  税率={JP_CRYPTO_TAX*100:.0f}%(繰越なし)')
    print('# ★国内現物では執行不可。海外拡張の是非を数字で判断する研究BT。')
    print('#' * 96)

    all_summ = {}
    out_rows = []
    for sym in SYMBOLS:
        res = analyze(sym, args.fee_rt, args.rebal_drag, args.haircut, args.exchange)
        summ = summarize(res)
        all_summ[sym] = summ
        print('\n' + '=' * 96)
        print(f'[{sym}]  funding {res["n"]}本  負funding比率={res["neg_share_all"]*100:.1f}%  '
              f'最長連続負funding={res["longest_neg_streak_days"]:.1f}日  '
              f'funding p01/p99={res["fr_p01"]*100:.3f}%/{res["fr_p99"]*100:.3f}% (8h)')
        print('=' * 96)
        print(f"  {'year':>6s}{'n':>5s}{'gross%':>8s}{'net%':>8s}{'net_hc%':>8s}"
              f"{'aftTax%':>9s}{'neg_f%':>8s}   区分")
        y = res['yearly']
        for _, r in y.iterrows():
            if r['n'] <= 100:
                continue
            tag = 'IS ' if r['year'] <= 2022 else 'OOS'
            print(f"  {int(r['year']):>6d}{int(r['n']):>5d}{r['gross_yr']*100:>8.1f}"
                  f"{r['net_yr']*100:>8.1f}{r['net_yr_hc']*100:>8.1f}"
                  f"{r['aftertax_yr']*100:>9.1f}{r['neg_share']*100:>7.0f}%   {tag}")
            out_rows.append({'sym': sym, **{k: r[k] for k in
                            ['year', 'n', 'gross_yr', 'net_yr', 'net_yr_hc', 'aftertax_yr',
                             'neg_share']}})
        print(f"\n  要約: gross年率中央={summ['gross_yr_med_full']*100:.1f}%  "
              f"net(コスト後)={summ['net_yr_med_full']*100:.1f}%  "
              f"税引後中央={summ['aftertax_med_full']*100:.1f}%  "
              f"(IS={summ['aftertax_med_is']*100:.1f}% / OOS={summ['aftertax_med_oos']*100:.1f}%)")
        print(f"        最悪年 税引後={summ['aftertax_min_full']*100:.1f}%  "
              f"損失年={summ['neg_years']}/{summ['n_years']}  "
              f"累積収穫maxDD={res['max_dd']*100:.1f}%(名目)")

    pd.DataFrame(out_rows).to_csv(HERE / 'crypto_basis_research_bt_result.csv', index=False)

    # --- 海外拡張の是非(C3向け素材) ---
    print('\n' + '#' * 96)
    print('# 海外拡張の是非(判断素材)')
    print('#' * 96)
    btc = all_summ['BTCUSDT']
    print(f"  BTC 税引後年率(中央/最悪/OOS) = "
          f"{btc['aftertax_med_full']*100:.1f}% / {btc['aftertax_min_full']*100:.1f}% / "
          f"{btc['aftertax_med_oos']*100:.1f}%   損失年 {btc['neg_years']}/{btc['n_years']}")
    print("  ※ この年率に対し海外取引所リスク(破綻/出金停止/規制)を負う。")
    print("     counterparty ヘアカット(--haircut)を数%課すと税引後が更に目減りする点に注意。")


if __name__ == '__main__':
    main()
