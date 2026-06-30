"""
mr_forward_review.py - AUDCAD(4h)平均回帰(MR_AC, magic=20260050) フォワード監視・集計

demo フォワードテストの実約定を history.csv から集計し、BT期待値/実運用計画と突き合わせる。
ネットワーク不要(history.csv のみ)。VPS で sync_history.py 実行 → git push → ここで pull して実行。

集計内容:
  - 実現パフォーマンス(クラスタ単位): PF / 勝率 / net / maxDD / payoff / 平均勝敗(JPY)
  - 決済理由内訳(TP / ZSTOP / TIME)・段数(tier)分布・高ボラスロットル発火・保有時間
  - BT/計画リファレンス比較(full PF1.61 / OOS2.57 / WR~71% / MC95 DD)
  - 昇格ゲート判定(計画§5: 3ヶ月 ∧ 30約定 ∧ SL最低1回発火 ∧ 実現PF>1.2)
  - キルスイッチ判定(計画§4: ローリング12ヶ月PF<1.0 / 実現maxDD>MC95)

クラスタ復元: MR は1シグナルを最大3段(Tier1/2/3)で建て、決済は全段一括(close_cluster)。
  同一(broker, symbol, side)で close_time が近接(<=10分)するレッグ群を1クラスタとして合算。

Usage:
    python optimizer/mr_forward_review.py
    python optimizer/mr_forward_review.py --json        # 機械可読(ルーティン通知用)
    python optimizer/mr_forward_review.py --lot-scale 0.46   # live想定スケールで閾値換算
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HISTORY_CSV = Path(__file__).parent / 'history.csv'
JST = timezone(timedelta(hours=9))

MR_MAGIC = 20260050
MR_TAG   = 'MR_AC'

# BT/計画リファレンス(optimizer/audcad_mr_deployment_plan.md / audcad_stress_test.py)
REF = {
    'full_pf': 1.61, 'oos_pf': 2.57, 'wr': 0.71,
    'mc95_lotpip': 398.0,            # throttle後 MC maxDD 95%ile (lot-pip)
    'pip_value_jpy': 1080.0,         # AUDCAD 1.0lot の 1pip ≒ 10CAD × CADJPY108
}
# 昇格ゲート(計画§5)
PROMO = {'days': 90, 'trades': 30, 'sl_fires': 1, 'pf': 1.2}


def load_mr() -> pd.DataFrame:
    if not HISTORY_CSV.exists():
        print(f'[ERROR] {HISTORY_CSV} が無い。VPSで sync_history.py 実行 → git pull。')
        sys.exit(1)
    df = pd.read_csv(HISTORY_CSV)
    df = df[df['magic'] == MR_MAGIC].copy()
    if df.empty:
        return df
    df['open_time']  = pd.to_datetime(df['open_time'])
    df['close_time'] = pd.to_datetime(df['close_time'])
    return df


def exit_reason(comment: str) -> str:
    c = str(comment).upper()
    if 'ZSTOP' in c:
        return 'zstop'
    if 'TIME' in c:
        return 'time'
    if 'TP' in c:
        return 'tp'
    return 'other'


def build_clusters(df: pd.DataFrame) -> pd.DataFrame:
    """レッグ行をクラスタ(=1トレード)へ集約。"""
    df = df.sort_values('close_time').copy()
    df['side']   = df['type'].map({'buy': 'long', 'sell': 'short'})
    df['reason'] = df['comment'].map(exit_reason)
    # close_time を10分床で量子化してクラスタキーに
    df['ckey'] = (df['broker'].astype(str) + '|' + df['symbol'].astype(str) + '|' +
                  df['side'].astype(str) + '|' +
                  df['close_time'].dt.floor('10min').astype(str))
    rows = []
    for _, g in df.groupby('ckey'):
        g = g.sort_values('open_time')
        tier1_lot = float(g['lots'].iloc[0])           # 最初に建てた段(Tier1)
        rows.append({
            'broker':   g['broker'].iloc[0],
            'side':     g['side'].iloc[0],
            'open_time':  g['open_time'].min(),
            'close_time': g['close_time'].max(),
            'n_legs':   len(g),
            'lots_sum': float(g['lots'].sum()),
            'tier1_lot': tier1_lot,
            'throttled': int(round(tier1_lot, 3) <= 0.12),   # 0.2*0.5=0.1 -> throttle
            'net':      float(g['profit'].sum()),
            'reason':   g['reason'].mode().iloc[0] if not g['reason'].mode().empty else 'other',
        })
    cl = pd.DataFrame(rows).sort_values('close_time').reset_index(drop=True)
    cl['hold_h'] = (cl['close_time'] - cl['open_time']).dt.total_seconds() / 3600.0
    cl['hold_bars'] = (cl['hold_h'] / 4.0).round().astype(int)
    cl['close_jst'] = cl['close_time'].dt.tz_localize('UTC').dt.tz_convert(JST)
    cl['win'] = cl['net'] > 0
    return cl


def pf_of(nets: np.ndarray) -> float:
    gw = nets[nets > 0].sum()
    gl = -nets[nets <= 0].sum()
    return gw / gl if gl > 0 else (float('inf') if gw > 0 else float('nan'))


def max_dd(nets: np.ndarray) -> float:
    if len(nets) == 0:
        return 0.0
    eq = np.cumsum(nets)
    return float((np.maximum.accumulate(eq) - eq).max())


def summarize(cl: pd.DataFrame, lot_scale: float) -> dict:
    nets = cl['net'].to_numpy()
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    span_days = (cl['close_time'].max() - cl['close_time'].min()).days if len(cl) > 1 else 0
    sl_fires = int(cl['reason'].isin(['zstop', 'time']).sum())
    pf = pf_of(nets)
    realized_dd = max_dd(nets)
    # MC95 を JPY 換算(demo lot_scale=1.0 なら REF通り)
    mc95_jpy = REF['mc95_lotpip'] * REF['pip_value_jpy'] * lot_scale
    # ローリング12ヶ月PF
    cutoff = cl['close_time'].max() - pd.Timedelta(days=365) if len(cl) else None
    r12 = cl[cl['close_time'] >= cutoff] if cutoff is not None else cl
    pf_12mo = pf_of(r12['net'].to_numpy()) if len(r12) else float('nan')
    return {
        'n': len(cl), 'span_days': span_days,
        'first': str(cl['close_time'].min().date()) if len(cl) else '-',
        'last':  str(cl['close_time'].max().date()) if len(cl) else '-',
        'pf': pf, 'wr': float(cl['win'].mean()) if len(cl) else float('nan'),
        'net': float(nets.sum()), 'expectancy': float(nets.mean()) if len(cl) else 0.0,
        'avg_win': float(wins.mean()) if len(wins) else 0.0,
        'avg_loss': float(losses.mean()) if len(losses) else 0.0,
        'payoff': float(abs(wins.mean() / losses.mean())) if len(losses) and losses.mean() != 0 else float('nan'),
        'max_dd': realized_dd, 'mc95_jpy': mc95_jpy, 'pf_12mo': pf_12mo,
        'sl_fires': sl_fires, 'lot_scale': lot_scale,
        'reasons': cl['reason'].value_counts().to_dict(),
        'tier_dist': cl['n_legs'].value_counts().sort_index().to_dict(),
        'throttled': int(cl['throttled'].sum()),
        'hold_med': float(cl['hold_bars'].median()) if len(cl) else float('nan'),
        'hold_p90': float(cl['hold_bars'].quantile(0.9)) if len(cl) else float('nan'),
        'brokers': cl['broker'].value_counts().to_dict(),
    }


def promo_check(s: dict) -> dict:
    c = {
        'span_3mo':   (s['span_days'] >= PROMO['days'], f"{s['span_days']}/{PROMO['days']}d"),
        'n_30':       (s['n'] >= PROMO['trades'], f"{s['n']}/{PROMO['trades']}"),
        'sl_fired':   (s['sl_fires'] >= PROMO['sl_fires'], f"{s['sl_fires']}/{PROMO['sl_fires']}"),
        'pf_gt_1_2':  (not np.isnan(s['pf']) and s['pf'] > PROMO['pf'], f"{s['pf']:.2f}/>{PROMO['pf']}"),
    }
    c['ALL'] = (all(v[0] for v in c.values()), '')
    return c


def kill_check(s: dict) -> dict:
    pf12 = s['pf_12mo']
    dd_breach = s['max_dd'] > s['mc95_jpy'] and s['mc95_jpy'] > 0
    pf12_breach = (not np.isnan(pf12)) and pf12 < 1.0 and s['n'] >= 10
    return {
        'pf12_lt_1': (pf12_breach, f"12moPF={pf12:.2f}" if not np.isnan(pf12) else "12moPF=na(<10n)"),
        'dd_gt_mc95': (dd_breach, f"DD={s['max_dd']:,.0f}/MC95={s['mc95_jpy']:,.0f}円"),
        'TRIGGER': (pf12_breach or dd_breach, ''),
    }


def render(cl: pd.DataFrame, s: dict):
    print('=' * 78)
    print(f'MR_AC フォワード監視  AUDCAD(4h) magic={MR_MAGIC}  lot_scale={s["lot_scale"]}')
    print('=' * 78)
    if s['n'] == 0:
        print('まだ約定クラスタなし(監視待機中)。demo起動後、初エントリーを待つ。')
        print(f'昇格目標(計画§5): {PROMO["days"]}日 ∧ {PROMO["trades"]}約定 ∧ SL最低1発火 ∧ 実現PF>{PROMO["pf"]}')
        return
    print(f'期間 {s["first"]}~{s["last"]} ({s["span_days"]}日)  '
          f'クラスタ={s["n"]}  broker={s["brokers"]}')
    print('-- 実現パフォーマンス(クラスタ=1トレード) --')
    print(f'  PF={s["pf"]:.2f}  WR={s["wr"]*100:.1f}%  net={s["net"]:,.0f}円  '
          f'expectancy={s["expectancy"]:,.0f}円')
    print(f'  avgWin={s["avg_win"]:,.0f}  avgLoss={s["avg_loss"]:,.0f}  '
          f'payoff={s["payoff"]:.2f}  maxDD={s["max_dd"]:,.0f}円')
    print(f'  決済理由 {s["reasons"]}  段数分布 {s["tier_dist"]}  '
          f'高ボラ縮小 {s["throttled"]}件')
    print(f'  保有(H4本) 中央={s["hold_med"]:.0f} p90={s["hold_p90"]:.0f}  '
          f'(BTタイムストップ=48本)')
    print('-- BT/計画リファレンス比較 --')
    print(f'  実現PF {s["pf"]:.2f}  vs  full期待1.61 / OOS2.57 (順風) ; '
          f'実現WR {s["wr"]*100:.0f}% vs ~71%')
    print(f'  実現maxDD {s["max_dd"]:,.0f}円  vs  MC95 {s["mc95_jpy"]:,.0f}円 '
          f'(lot_scale={s["lot_scale"]})')

    print('-- 昇格ゲート判定(計画§5) --')
    pc = promo_check(s)
    for k in ['span_3mo', 'n_30', 'sl_fired', 'pf_gt_1_2']:
        ok, det = pc[k]
        print(f'  [{"PASS" if ok else "....": <4}] {k:12s} {det}')
    print(f'  => 総合: {"★昇格条件クリア(実マネー検討へ)" if pc["ALL"][0] else "監視継続(未達項目あり)"}')

    print('-- キルスイッチ判定(計画§4) --')
    kc = kill_check(s)
    for k in ['pf12_lt_1', 'dd_gt_mc95']:
        trig, det = kc[k]
        print(f'  [{"TRIGGER" if trig else "ok": <7}] {k:12s} {det}')
    print(f'  => {"⚠️停止&前提再検証" if kc["TRIGGER"][0] else "継続OK"}')
    print('注: 実現maxDDは「決済済みクラスタ」基準。建玉中の含み損は別途MT5で監視(本集計はオフライン)。')
    print('    スリッページは vps/mr_log_*.txt の entry価格とBT想定を別途突合(計画§6)。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true', help='機械可読出力(ルーティン通知用)')
    ap.add_argument('--lot-scale', type=float, default=1.0,
                    help='閾値(MC95)のJPY換算スケール。demo=1.0 / live=採用スケール')
    args = ap.parse_args()

    df = load_mr()
    cl = build_clusters(df) if not df.empty else pd.DataFrame()
    s = summarize(cl, args.lot_scale) if len(cl) else summarize(
        pd.DataFrame(columns=['net', 'win', 'reason', 'n_legs', 'throttled',
                              'close_time', 'open_time', 'hold_bars', 'broker']), args.lot_scale)

    if args.json:
        out = dict(s)
        out['promo'] = {k: v[0] for k, v in promo_check(s).items()} if s['n'] else {}
        out['kill'] = {k: v[0] for k, v in kill_check(s).items()} if s['n'] else {}
        print(json.dumps(out, ensure_ascii=False, default=str))
        return
    render(cl, s)


if __name__ == '__main__':
    main()
