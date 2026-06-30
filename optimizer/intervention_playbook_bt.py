"""
intervention_playbook_bt.py - 案D(介入後の押し目買い)プレイブックのオフライン検証

vps/intervention_monitor.py の案D状態機械(検出->安定確認->ラダー買い->abort/TP/time)を
USDJPY 5m 10y で忠実に再生し、実行ルール(特に abort ストップ)込みでエッジが残るかを実測する。

重要な検証点:
  monitor は live で "本物の介入" か "ただのリスクオフ急落(誤検出)" を判別できない。
  よって ALL FIRINGS(介入+誤検出) のネット期待値が真の期待値。
  KNOWN介入サブセット だけでなく全発火を集計する。

ロジック(monitor と一致):
  検出      = 直近 SPIKE_WIN_MIN 分の高値から現値が max(ATR1h*MULT, MIN_YEN) 下落。
  trough    = arm 前の running-min(flush 安値)。arm で凍結。
  arm/tier1 = trough から D_BOUNCE_YEN 反発で安定 -> tier1 買い。
  追加tier  = 前回約定から D_ADD_GAP_YEN 下、かつ abort 手前。最大 D_TIERS 段。
  abort     = 価格が 凍結trough - D_ABORT_YEN 下抜け -> 全決済(マクロ転換限定)。
  TP        = 価格が pre_high 到達 -> 全決済(完全リトレース)。
  time      = D_MAX_DAYS 営業日 超過 -> 全決済。
  arm無し   = D_ARM_TIMEOUT_H 内に反発しなければエピソード放棄(建てない)。

P&L: 各 tier (exit-entry)*lot*100000 JPY。carry(long正スワップ)は別途概算で加算(保守的に下限)。

実行: python optimizer/intervention_playbook_bt.py
出力: コンソールサマリ + optimizer/intervention_playbook_bt_result.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from intervention_event_study import (load_5m, add_atr_1h, detect_spikes, nearest_known,
                                       PER_HOUR)

# monitor と一致させる較正パラメータ
SPIKE_WIN_MIN   = 30
SPIKE_ATR_MULT  = 3.5
SPIKE_MIN_YEN   = 1.5
CLUSTER_H       = 18
PRE_HIGH_MIN    = 120

FLUSH_WIN_H     = 6.0        # 検出後この時間 flush 安値を追跡してから arm(早すぎる凍結=偽abort回避)
D_BOUNCE_YEN    = 0.4
D_TIERS         = 3
D_TIER_LOT      = 0.10
D_ADD_GAP_YEN   = 0.5
D_ABORT_YEN     = 3.0
D_MAX_DAYS      = 30          # 営業日
D_ARM_TIMEOUT_H = 12

CONTRACT        = 100_000     # USDJPY 1.0 lot = 100k USD
CARRY_ANNUAL    = 0.025       # long USDJPY 正キャリー概算(保守 2.5%/年)。常に追い風(下限)。


def simulate_episode(df, i_start):
    """1エピソードを再生。dict(結果) を返す。建てなければ reason='abandon'/'no_arm'。"""
    high = df['high'].values
    low  = df['low'].values
    close = df['close'].values
    t = df['datetime'].values
    n = len(df)

    w_pre = PRE_HIGH_MIN // 5
    pre_high = float(high[max(0, i_start - w_pre):i_start + 1].max())
    t0 = pd.Timestamp(t[i_start])

    armed = False
    trough = float(low[i_start])
    entries = []           # list of (fill_price, fill_time)
    last_add = None
    t_arm = None
    flush_end = t0 + pd.Timedelta(hours=FLUSH_WIN_H)
    arm_timeout = t0 + pd.Timedelta(hours=D_ARM_TIMEOUT_H)
    abort_lvl = None

    j = i_start
    exit_price = None
    reason = None
    while j < n:
        tj = pd.Timestamp(t[j])
        if not armed:
            trough = min(trough, float(low[j]))
            if tj > arm_timeout:
                reason = 'abandon'
                break
            # flush 窓が終わるまで安値追跡のみ(真の谷で凍結)。窓後に反発で arm。
            if tj >= flush_end and float(high[j]) >= trough + D_BOUNCE_YEN:
                fill = float(close[j])                 # 現実的: arm 時の市場価格で約定
                armed = True
                t_arm = tj
                abort_lvl = trough - D_ABORT_YEN
                entries.append((fill, tj))
                last_add = fill
        else:
            # exits(優先)
            if float(low[j]) <= abort_lvl:
                exit_price, reason = abort_lvl, 'ABORT'
                break
            if float(high[j]) >= pre_high:
                exit_price, reason = pre_high, 'TP'
                break
            if tj > t_arm + pd.Timedelta(days=D_MAX_DAYS * 7.0 / 5.0):
                exit_price, reason = float(close[j]), 'TIME'
                break
            # 追加 tier(押し目買い)
            if len(entries) < D_TIERS:
                lvl = last_add - D_ADD_GAP_YEN
                if float(low[j]) <= lvl and lvl > abort_lvl:
                    entries.append((lvl, tj))
                    last_add = lvl
        j += 1

    if armed and exit_price is None:           # データ末尾まで到達
        exit_price, reason = float(close[-1]), 'EOD'
        j = n - 1
    if not armed:
        return {'reason': reason or 'no_arm', 'tiers': 0, 'pnl_yen': 0.0,
                'carry_yen': 0.0, 'held_days': 0.0, 'pre_high': round(pre_high, 3),
                'trough': round(trough, 3)}

    t_exit = pd.Timestamp(t[j])
    pnl = 0.0
    carry = 0.0
    for (fp, ft) in entries:
        pnl += (exit_price - fp) * D_TIER_LOT * CONTRACT
        held_d = max(0.0, (t_exit - ft).total_seconds() / 86400.0)
        notional_jpy = fp * D_TIER_LOT * CONTRACT
        carry += notional_jpy * CARRY_ANNUAL * held_d / 365.0
    held_days = (t_exit - t_arm).total_seconds() / 86400.0
    return {'reason': reason, 'tiers': len(entries), 'avg_entry': round(np.mean([e[0] for e in entries]), 3),
            'exit': round(exit_price, 3), 'pnl_yen': round(pnl, 0), 'carry_yen': round(carry, 0),
            'held_days': round(held_days, 1), 'pre_high': round(pre_high, 3),
            'trough': round(trough, 3), 't_exit': t_exit}


def main():
    global D_ABORT_YEN
    ap = argparse.ArgumentParser()
    ap.add_argument('--abort-yen', type=float, default=D_ABORT_YEN)
    ap.add_argument('--no-abort', action='store_true', help='abort 無しで死因テールを観察')
    args = ap.parse_args()
    D_ABORT_YEN = 999.0 if args.no_abort else args.abort_yen

    df = load_5m()
    df = add_atr_1h(df)
    reps = detect_spikes(df, SPIKE_WIN_MIN, SPIKE_ATR_MULT, SPIKE_MIN_YEN, CLUSTER_H)
    print('detections=%d (win=%dm atr_mult=%.1f min_yen=%.1f) abort=%.1f' %
          (len(reps), SPIKE_WIN_MIN, SPIKE_ATR_MULT, SPIKE_MIN_YEN, D_ABORT_YEN))

    rows = []
    last_exit_time = None
    for i in reps:
        ts = df['datetime'].iloc[i]
        # "flat時のみ検出" を模倣: 前エピソードの保有窓内の発火はスキップ
        if last_exit_time is not None and ts <= last_exit_time:
            continue
        r = simulate_episode(df, i)
        r['datetime'] = ts
        r['known'] = nearest_known(ts)
        rows.append(r)
        if r.get('t_exit') is not None:
            last_exit_time = r['t_exit']

    res = pd.DataFrame(rows)
    traded = res[res['tiers'] > 0].copy()
    out_csv = Path(__file__).resolve().parent / 'intervention_playbook_bt_result.csv'
    keep = ['datetime', 'known', 'reason', 'tiers', 'avg_entry', 'exit', 'pre_high',
            'trough', 'held_days', 'pnl_yen', 'carry_yen']
    traded[keep].to_csv(out_csv, index=False)

    pd.set_option('display.width', 240); pd.set_option('display.max_columns', 40)

    def summarize(name, d):
        if len(d) == 0:
            print('\n=== %s: n=0 ===' % name); return
        price = d['pnl_yen'].sum()
        carry = d['carry_yen'].sum()
        wins = (d['pnl_yen'] > 0).sum()
        print('\n=== %s (n=%d) ===' % (name, len(d)))
        print('  price PnL  : %12s JPY (win %d/%d = %.0f%%)' %
              (f'{price:,.0f}', wins, len(d), 100.0 * wins / len(d)))
        print('  + carry    : %12s JPY (long正スワップ概算, 追い風)' % f'{carry:,.0f}')
        print('  = total    : %12s JPY' % f'{price + carry:,.0f}')
        print('  per-episode: price %s / total %s JPY' %
              (f'{price/len(d):,.0f}', f'{(price+carry)/len(d):,.0f}'))
        by = d.groupby('reason')['pnl_yen'].agg(['count', 'sum'])
        print('  by reason  :')
        for r, row in by.iterrows():
            print('     %-8s n=%-3d price=%12s JPY' % (r, int(row['count']), f"{row['sum']:,.0f}"))

    print('\n注: lot=%.2f/tier(最大%d段), 全エピソードは demo lot_scale=1.0 基準。'
          % (D_TIER_LOT, D_TIERS))
    summarize('ALL FIRINGS (介入+誤検出=真の期待値)', traded)
    summarize('KNOWN INTERVENTIONS のみ', traded[traded['known'] != ''])
    summarize('NON-INTERVENTION (誤検出)のみ', traded[traded['known'] == ''])

    # 既知介入の個別行
    k = traded[traded['known'] != '']
    if len(k):
        print('\n--- KNOWN intervention episodes ---')
        print(k[keep].to_string(index=False))

    abandoned = res[(res['tiers'] == 0)]
    print('\n放棄/未arm エピソード: %d (うち known=%d) = grind下落で建てず回避'
          % (len(abandoned), (abandoned['known'] != '').sum()))
    print('wrote %s' % out_csv)


if __name__ == '__main__':
    main()
