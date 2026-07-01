"""
intervention_playbook_bt.py - 案D(介入後の押し目買い)プレイブック検証 + 撤退基準比較

vps/intervention_monitor.py の案D状態機械(検出->flush窓->反発でラダー買い)を USDJPY 5m 10y
で再生する。本版は「無レバなら塩漬けできる」前提を受け、撤退ポリシーを比較する:

  ABORT3    : 凍結trough-3円 で撤退(損失確定, 資本回転重視)。
  HOLD_PH   : abort無し・pre_high(元水準)まで塩漬け保有(回復まで待つ, 無レバ)。
  HOLD_BE   : abort無し・avg_entry(建値=ブレークイーブン)復帰で決済(資本回転しつつ塩漬け容認)。
  各ポリシーで long は max_hold_days 超で強制決済(None=無期限。データ末で未決済は unrealized)。

計測(無レバ・塩漬けの是非を判断する核心指標):
  maxDD_yen / maxDD_jpy : avg_entry からの最深含み損(=塩漬け中に耐える必要のある評価損)。
  days_to_BE            : 最終建てから 建値復帰 までの営業日(資本拘束期間)。
  days_to_PH            : 元水準(pre_high)復帰までの営業日。
  recovered             : データ内で pre_high 回復したか(未回復=構造転換テールの疑い)。

P&L: (exit-entry)*lot*100000 JPY + carry(long正スワップ 2.5%/年 概算, 常に追い風)。
各検出は独立評価(flat制約なし)= "この介入を取ったらどうなるか" の分布を見る。

実行: python optimizer/intervention_playbook_bt.py
出力: コンソール比較表 + optimizer/intervention_playbook_bt_result.csv (per-event x policy)
"""

from pathlib import Path

import numpy as np
import pandas as pd

from intervention_event_study import load_5m, add_atr_1h, detect_spikes, nearest_known

# monitor と一致させる検出/エントリ較正
SPIKE_WIN_MIN   = 30
SPIKE_ATR_MULT  = 3.5
SPIKE_MIN_YEN   = 1.5
CLUSTER_H       = 18
PRE_HIGH_MIN    = 120
FLUSH_WIN_H     = 6.0
D_BOUNCE_YEN    = 0.4
D_TIERS         = 3
D_TIER_LOT      = 0.10
D_ADD_GAP_YEN   = 0.5
D_ARM_TIMEOUT_H = 12

CONTRACT        = 100_000
CARRY_ANNUAL    = 0.025

# 撤退ポリシー: (name, abort_yen(None=無), tp('pre_high'|'breakeven'), max_hold_days(None=無期限))
POLICIES = [
    ('ABORT3',  3.0,  'pre_high',  30),
    ('HOLD_PH', None, 'pre_high',  None),
    ('HOLD_BE', None, 'breakeven', None),
]


def build_entries(df, i_start):
    """検出->flush窓->反発arm->ラダー追加。約定リスト entries[(price,time)] と
    pre_high, frozen trough, arm時刻 を返す。arm しなければ entries=[]。"""
    high, low, close, t = (df['high'].values, df['low'].values,
                           df['close'].values, df['datetime'].values)
    n = len(df)
    w_pre = PRE_HIGH_MIN // 5
    pre_high = float(high[max(0, i_start - w_pre):i_start + 1].max())
    t0 = pd.Timestamp(t[i_start])
    flush_end = t0 + pd.Timedelta(hours=FLUSH_WIN_H)
    arm_timeout = t0 + pd.Timedelta(hours=D_ARM_TIMEOUT_H)

    armed = False
    trough = float(low[i_start])
    entries, last_add, t_arm, arm_idx = [], None, None, None
    j = i_start
    while j < n:
        tj = pd.Timestamp(t[j])
        if not armed:
            trough = min(trough, float(low[j]))
            if tj > arm_timeout:
                break
            if tj >= flush_end and float(high[j]) >= trough + D_BOUNCE_YEN:
                fill = float(close[j])
                armed, t_arm, arm_idx = True, tj, j
                entries.append((fill, tj)); last_add = fill
        else:
            # 追加tierだけ先に置く(撤退判定はポリシー別に後段で)。abort手前制約は最深(-3)基準で緩め置き。
            if len(entries) < D_TIERS:
                lvl = last_add - D_ADD_GAP_YEN
                if float(low[j]) <= lvl and lvl > trough - 3.0:
                    entries.append((lvl, tj)); last_add = lvl
            # ラダー完成後は探索終了(以降はポリシー別simで評価)
            if len(entries) >= D_TIERS or (tj > t_arm + pd.Timedelta(days=60)):
                break
        j += 1
    return pre_high, trough, t_arm, arm_idx, entries


def run_policy(df, arm_idx, entries, pre_high, trough, t_arm,
               abort_yen, tp, max_hold_days):
    """ラダー約定後をポリシーで前進評価。dict を返す。"""
    high, low, close, t = (df['high'].values, df['low'].values,
                           df['close'].values, df['datetime'].values)
    n = len(df)
    lots = D_TIER_LOT * len(entries)
    avg_entry = float(np.mean([e[0] for e in entries]))
    abort_lvl = (trough - abort_yen) if abort_yen is not None else -1e9
    be_lvl = avg_entry
    # 最終建て時刻から計測(資本拘束/含み損は全建て完了後で評価)
    t_last = entries[-1][1]
    start_j = arm_idx
    # start_j を最終建てバーへ進める
    while start_j < n and pd.Timestamp(t[start_j]) < t_last:
        start_j += 1

    run_min = avg_entry
    days_to_be = np.nan
    days_to_ph = np.nan
    exit_price, reason, t_exit = None, None, None
    for j in range(start_j, n):
        tj = pd.Timestamp(t[j])
        run_min = min(run_min, float(low[j]))
        if np.isnan(days_to_be) and float(high[j]) >= be_lvl:
            days_to_be = (tj - t_last).total_seconds() / 86400.0 * 5.0 / 7.0
        if np.isnan(days_to_ph) and float(high[j]) >= pre_high:
            days_to_ph = (tj - t_last).total_seconds() / 86400.0 * 5.0 / 7.0
        # 撤退判定
        if abort_yen is not None and float(low[j]) <= abort_lvl:
            exit_price, reason, t_exit = abort_lvl, 'ABORT', tj; break
        tgt = pre_high if tp == 'pre_high' else be_lvl
        if float(high[j]) >= tgt:
            exit_price, reason, t_exit = tgt, ('TP' if tp == 'pre_high' else 'BE'), tj; break
        if max_hold_days is not None and tj > t_last + pd.Timedelta(days=max_hold_days * 7.0 / 5.0):
            exit_price, reason, t_exit = float(close[j]), 'TIME', tj; break
    if exit_price is None:                       # データ末まで未決済(=塩漬け継続)
        exit_price, reason, t_exit = float(close[-1]), 'OPEN', pd.Timestamp(t[-1])

    maxdd_yen = round(avg_entry - run_min, 3)
    pnl = (exit_price - avg_entry) * lots * CONTRACT
    held_days = (t_exit - t_last).total_seconds() / 86400.0
    carry = avg_entry * lots * CONTRACT * CARRY_ANNUAL * max(0.0, held_days) / 365.0
    return {
        'reason': reason, 'tiers': len(entries), 'avg_entry': round(avg_entry, 3),
        'exit': round(exit_price, 3), 'pre_high': round(pre_high, 3),
        'maxDD_yen': maxdd_yen, 'maxDD_jpy': round(maxdd_yen * lots * CONTRACT, 0),
        'days_to_BE': round(days_to_be, 1) if not np.isnan(days_to_be) else np.nan,
        'days_to_PH': round(days_to_ph, 1) if not np.isnan(days_to_ph) else np.nan,
        'recovered': int(not np.isnan(days_to_ph)),
        'pnl_yen': round(pnl, 0), 'carry_yen': round(carry, 0),
        'held_days': round(held_days, 1),
    }


def main():
    df = load_5m()
    df = add_atr_1h(df)
    reps = detect_spikes(df, SPIKE_WIN_MIN, SPIKE_ATR_MULT, SPIKE_MIN_YEN, CLUSTER_H)

    events = []
    for i in reps:
        pre_high, trough, t_arm, arm_idx, entries = build_entries(df, i)
        if not entries:
            continue
        ts = df['datetime'].iloc[i]
        events.append({'i': i, 'datetime': ts, 'known': nearest_known(ts),
                       'pre_high': pre_high, 'trough': trough, 't_arm': t_arm,
                       'arm_idx': arm_idx, 'entries': entries})
    print('detections=%d  armed episodes=%d' % (len(reps), len(events)))

    rows = []
    for ev in events:
        for (name, ab, tp, mh) in POLICIES:
            r = run_policy(df, ev['arm_idx'], ev['entries'], ev['pre_high'],
                           ev['trough'], ev['t_arm'], ab, tp, mh)
            r.update({'policy': name, 'datetime': ev['datetime'], 'known': ev['known']})
            rows.append(r)
    res = pd.DataFrame(rows)
    out_csv = Path(__file__).resolve().parent / 'intervention_playbook_bt_result.csv'
    keep = ['datetime', 'known', 'policy', 'reason', 'tiers', 'avg_entry', 'exit',
            'pre_high', 'maxDD_yen', 'maxDD_jpy', 'days_to_BE', 'days_to_PH',
            'recovered', 'held_days', 'pnl_yen', 'carry_yen']
    res[keep].to_csv(out_csv, index=False)
    pd.set_option('display.width', 260); pd.set_option('display.max_columns', 40)

    def summarize(name, d):
        price, carry = d['pnl_yen'].sum(), d['carry_yen'].sum()
        wins = (d['pnl_yen'] > 0).sum()
        openn = (d['reason'] == 'OPEN').sum()
        print('  %-8s n=%-3d price=%11s +carry=%9s =tot=%11s win=%d/%d maxDD_yen(med/max)=%.1f/%.1f openTail=%d'
              % (name, len(d), f'{price:,.0f}', f'{carry:,.0f}', f'{price+carry:,.0f}',
                 wins, len(d), d['maxDD_yen'].median(), d['maxDD_yen'].max(), openn))

    for scope, mask in [('ALL FIRINGS(真の期待値)', res['known'].notna()),
                        ('KNOWN介入のみ', res['known'] != ''),
                        ('非介入ディップのみ', res['known'] == '')]:
        sub = res[mask] if scope != 'ALL FIRINGS(真の期待値)' else res
        print('\n=== %s ===  (lot %.2f/tier x%d)' % (scope, D_TIER_LOT, D_TIERS))
        for (name, _, _, _) in POLICIES:
            summarize(name, sub[sub['policy'] == name])

    # 既知介入の per-event 深さ・期間(撤退基準の再検討材料)
    print('\n--- KNOWN interventions: drawdown & recovery by policy ---')
    k = res[res['known'] != ''].copy()
    show = ['datetime', 'known', 'policy', 'reason', 'maxDD_yen', 'maxDD_jpy',
            'days_to_BE', 'days_to_PH', 'recovered', 'held_days', 'pnl_yen']
    print(k[show].sort_values(['datetime', 'policy']).to_string(index=False))
    print('\nwrote %s' % out_csv)
    print('注: maxDD_jpy は demo lot(0.10/tier)基準。実lotに比例。塩漬けはこの評価損を'
          '無レバで耐えられる資本が前提。recovered=0 は構造転換テール(データ内未回復)。')


if __name__ == '__main__':
    main()
