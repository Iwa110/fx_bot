"""
intervention_sizing.py - 案D long塩漬け(HOLD_PH) 実口座サイジング & 利益試算

前提(ユーザー): 実口座で実施 / 現行2.75%(縮小前提)でサイジング /
  金利差が消える兆候(BOJ連続利上げ)を撤退トリガ。

入力: intervention_playbook_bt_result.csv (HOLD_PH, ALL FIRINGS 20エピソード, 2016-2026)。
  price P&L はレジーム非依存(demo 0.30lot 実測) -> 1.0 total lot へ線形スケール。
  carry は本スクリプトで「現行フラット金利差」で再計算(2022-24の高金利差windfallを除く)。

サイジング基準(無レバ塩漬けの束縛 = 耐える必要のある最大含み損):
  実測 maxDD 最大 21.2円(2022-10)。ストレス緩衝込み STRESS_DD_YEN=30円 で逆算。
  safe_total_lot = 許容DD率 f * 自己資本 / (STRESS_DD_YEN * 100000)。

年間試算の2基準(正直な上下限):
  ALL   = 全発火を取れる理想(上限, ~2.0回/年)。
  SEQ   = 単一ポジ逐次(monitor実挙動 = 塩漬け中は後続をスキップ)= 現実的下限。

BOJ連続利上げ撤退: 金利差 <= RATE_EXIT_TH で塩漬け強制手仕舞い。歴史的に介入期は
  差 >=2.75%で一度もbindせず -> 過去試算は不変。将来のcarry消滅/逆転テールの保険。
  ただし諸刃 = 発火時にprice未回復なら含み損を実現する(下記 caveat)。

実行: python optimizer/intervention_sizing.py
"""

from pathlib import Path
import pandas as pd

RESULT = Path(__file__).resolve().parent / 'intervention_playbook_bt_result.csv'
CONTRACT      = 100_000
DEMO_LOT      = 0.30          # 3 tier x 0.10
HAIRCUT       = 0.85
STRESS_DD_YEN = 30.0          # サイジング用ストレスDD(実測max21.2に緩衝)
HIST_MAX_DD   = 21.2

CARRY_NOW     = 0.0275        # 現行 米日金利差(2026-06, 縮小前提の基準)
CARRY_DOWN    = 0.0150        # 縮小シナリオ(BOJ利上げ/Fed利下げ進行)

EQUITIES      = [1_000_000, 2_000_000, 3_000_000, 5_000_000, 10_000_000]
DD_FRACS      = [0.30, 0.50]


def carry_per_lot(avg_entry, held_days, rate):
    """1.0 total lot・フラット金利差 rate での塩漬けスワップ(JPY)。"""
    notional = avg_entry * 1.0 * CONTRACT
    return notional * rate * held_days / 365.0 * HAIRCUT


def main():
    d = pd.read_csv(RESULT, parse_dates=['datetime'])
    ph = d[d['policy'] == 'HOLD_PH'].copy().sort_values('datetime').reset_index(drop=True)
    span_years = (ph['datetime'].max() - ph['datetime'].min()).days / 365.25
    n = len(ph)
    print('HOLD_PH episodes=%d over %.1f years (%.2f/yr)  demo_lot=%.2f haircut=%.2f'
          % (n, span_years, n / span_years, DEMO_LOT, HAIRCUT))

    # price は lot 線形 -> 1.0 total lot 基準
    ph['price_per_lot'] = ph['pnl_yen'] / DEMO_LOT

    # 単一ポジ逐次(SEQ): 直前エピソードの保有終了までに始まる発火はスキップ
    seq_mask = []
    busy_until = None
    for _, r in ph.iterrows():
        start = r['datetime']
        if busy_until is not None and start < busy_until:
            seq_mask.append(False); continue
        seq_mask.append(True)
        busy_until = start + pd.Timedelta(days=float(r['held_days']))
    ph['seq'] = seq_mask
    print('  SEQ(単一ポジ逐次)で実際に取れる発火: %d/%d (長期塩漬けが後続を占有)'
          % (int(ph['seq'].sum()), n))

    def aggregate(rate, subset):
        s = ph[subset] if subset is not None else ph
        price = s['price_per_lot'].sum()
        carry = sum(carry_per_lot(r['avg_entry'], r['held_days'], rate)
                    for _, r in s.iterrows())
        return price, carry, len(s)

    print('\n=== 1.0 total lot あたり 10年累計 & 年率 (price + carry) ===')
    for label, rate in [('現行2.75%', CARRY_NOW), ('縮小1.5%', CARRY_DOWN)]:
        for basis, mask in [('ALL(上限)', None), ('SEQ(現実)', ph['seq'])]:
            price, carry, cnt = aggregate(rate, mask)
            tot = price + carry
            print('  carry=%-9s %-9s n=%2d  price=%s + carry=%s = tot=%s / 年率 %s (price %s + carry %s)'
                  % (label, basis, cnt, f'{price:,.0f}', f'{carry:,.0f}', f'{tot:,.0f}',
                     f'{tot/span_years:,.0f}', f'{price/span_years:,.0f}', f'{carry/span_years:,.0f}'))

    # 年率(1.0 lot)を確定(現行2.75%)
    def annual(rate, mask):
        price, carry, _ = aggregate(rate, mask)
        return (price + carry) / span_years
    ann_all_now = annual(CARRY_NOW, None)
    ann_seq_now = annual(CARRY_NOW, ph['seq'])
    ann_seq_down = annual(CARRY_DOWN, ph['seq'])

    print('\n=== 実口座サイジング (STRESS_DD=%.0f円/lot=%s JPY, 実測maxDD=%.1f円) ==='
          % (STRESS_DD_YEN, f'{STRESS_DD_YEN*CONTRACT:,.0f}', HIST_MAX_DD))
    print('  自己資本   許容DD  safe_lot  worst含み損   年間利益(SEQ現実/現行2.75%)  (SEQ/縮小1.5%)  (ALL上限/2.75%)')
    for E in EQUITIES:
        for f in DD_FRACS:
            lot = f * E / (STRESS_DD_YEN * CONTRACT)
            worst = STRESS_DD_YEN * CONTRACT * lot
            p_seq_now  = ann_seq_now * lot
            p_seq_down = ann_seq_down * lot
            p_all_now  = ann_all_now * lot
            print('  %9s  %4.0f%%  %7.2f  %11s   %20s      %13s   %13s'
                  % (f'{E:,.0f}', f*100, lot, f'{worst:,.0f} JPY',
                     f'{p_seq_now:,.0f} JPY/年', f'{p_seq_down:,.0f}', f'{p_all_now:,.0f}'))

    # 月利30万(年360万)に必要な資本(SEQ現実・現行2.75%基準)
    if ann_seq_now > 0:
        need_lot = 3_600_000 / ann_seq_now
        need_eq_50 = need_lot * STRESS_DD_YEN * CONTRACT / 0.50
        print('\n月利30万(年360万)の目安[SEQ/2.75%%]: 必要 total lot=%.1f / 自己資本(DD50%%基準)=%s JPY'
              % (need_lot, f'{need_eq_50:,.0f}'))
    print('\n★caveat: ①n=9介入(+11ディップ)=極小標本, 試算は方向性。②利益源はキャリー(既存carry-gridと'
          '同種)で介入固有でない。③現行2.75%は縮小方向=年々逓減。④BOJ利上げ撤退は諸刃(未回復で実現損)。'
          '⑤単発発火の頻度は不規則(campaignは1-2年毎)=年率は平準化した見かけ。demo forward先行を強く推奨。')


if __name__ == '__main__':
    main()
