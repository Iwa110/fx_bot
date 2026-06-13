"""
grid_go_nogo.py - Step C: ペア別 Go/No-Go スコアカード (数値ゲート)。

Step 0(真値=Dukascopy) / Step A(11年WFO・感応度) / Step B(資本・破産確率) の成果CSVを集約し、
事前登録した数値ゲートで Go/No-Go を機械判定する。

数値ゲート (全て満たせば Go):
  G1. WFO OOS PF 中央値 > 1.20
  G2. WFO OOS 適格fold全てで PF > 1.0 (=OOS最小PF>1.0, 単一年度依存でない)
  G3. WFO OOS 累積 net > 0
  G4. WFO 選定パラメータが概ね安定 (atr_mult/max_levels が2値以内に収束)
  G5. 感応度に崖なし (v7近傍 ±1段で PF が全て >1.0)
  G6. 破産確率 < 1% を満たす必要資本が現実的 (req_cap_99 <= 500万円/lot1.0)

実行: python3 optimizer/grid_go_nogo.py   出力: grid_go_nogo_scorecard.csv + console
"""

import pandas as pd
from pathlib import Path

OUT = Path(__file__).resolve().parent
PAIRS = ['AUDCAD', 'NZDJPY', 'CHFJPY', 'GBPJPY']

wfo = pd.read_csv(OUT / 'grid_dukas_wfo_summary.csv')
wfo_full = pd.read_csv(OUT / 'grid_dukas_wfo.csv')
sens = pd.read_csv(OUT / 'grid_dukas_sensitivity.csv')
sizing = pd.read_csv(OUT / 'grid_sizing_ruin_result.csv')

REQ_CAP_CEIL = 5_000_000.0


def evaluate(pair):
    w = wfo[wfo.pair == pair]
    s = sizing[sizing.pair == pair]
    sv = sens[(sens.pair == pair)]
    if len(w) == 0:
        return None
    w = w.iloc[0]; s = s.iloc[0]
    g1 = w['oos_pf_med'] > 1.20
    g2 = w['oos_pf_min'] > 1.0
    g3 = w['oos_net_sum'] > 0
    g4 = (len(str(w['sel_atr_set']).split('/')) <= 2) and (len(str(w['sel_lv_set']).split('/')) <= 2)
    g5 = bool((sv['pf'] > 1.0).all())
    g6 = s['req_cap_99(lot1)'] <= REQ_CAP_CEIL
    gates = {'G1_oosPFmed>1.2': g1, 'G2_oosPFmin>1.0': g2, 'G3_oosNet>0': g3,
             'G4_paramStable': g4, 'G5_noCliff': g5, 'G6_reqCap<5M': g6}
    decision = 'GO' if all(gates.values()) else 'NO-GO'
    return {
        'pair': pair, 'decision': decision,
        'oos_pf_med': w['oos_pf_med'], 'oos_pf_min': w['oos_pf_min'],
        'oos_net_sum': round(w['oos_net_sum'], 0), 'param_atr': w['sel_atr_set'], 'param_lv': w['sel_lv_set'],
        'sens_minPF': round(sv['pf'].min(), 2), 'req_cap_99': s['req_cap_99(lot1)'],
        'mc_dd99': s['mc_dd99'], 'worst_single_gap': s['worst_single_gap'],
        **gates,
    }


def main():
    rows = [r for p in PAIRS if (r := evaluate(p))]
    df = pd.DataFrame(rows)
    df.to_csv(OUT / 'grid_go_nogo_scorecard.csv', index=False)

    print('=== Step C: Grid 実マネー Go/No-Go スコアカード (Dukascopy 11年 真値ベース) ===\n')
    gate_cols = ['G1_oosPFmed>1.2', 'G2_oosPFmin>1.0', 'G3_oosNet>0',
                 'G4_paramStable', 'G5_noCliff', 'G6_reqCap<5M']
    print(f'{"pair":7s} {"decision":7s} {"oosPFmed":>8s} {"oosPFmin":>8s} {"oosNetSum":>12s} '
          f'{"sensMinPF":>9s} {"reqCap99":>11s}  gates')
    for r in rows:
        gflags = ''.join('o' if r[g] else 'x' for g in gate_cols)
        print(f'{r["pair"]:7s} {r["decision"]:7s} {r["oos_pf_med"]:8.2f} {r["oos_pf_min"]:8.2f} '
              f'{r["oos_net_sum"]:12,.0f} {r["sens_minPF"]:9.2f} {r["req_cap_99"]:11,.0f}  {gflags}')
    print('\n  gates順: ' + ' '.join(gate_cols) + '  (o=pass / x=fail)')

    go = [r['pair'] for r in rows if r['decision'] == 'GO']
    print(f'\n  >>> GO: {go if go else "なし"}')
    print('\nsaved grid_go_nogo_scorecard.csv')


if __name__ == '__main__':
    main()
