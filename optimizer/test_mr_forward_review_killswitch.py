"""
test_mr_forward_review_killswitch.py - mr_forward_review.py のキルスイッチ/昇格ゲート発火テスト。

目的: 計画§4(キルスイッチ)/§5(昇格ゲート)のロジックが「発火すべき時に発火し、
発火してはいけない時に発火しない」ことを合成データで確認する。実データ(history.csv)は
2026-10-10時点で両ペアともキルスイッチ未発火のため、発火側の分岐は実データでは確認できない
(=回帰テストで境界を担保する)。

対象: kill_check() / promo_check() (純関数, dictを受けて判定dictを返す) と、
      summarize() の pf_12mo 計算(時系列クラスタ -> ローリング12ヶ月PF)の結合経路、
      および --basket バックストップ(mc95_jpy_override / load_mr の複数magic合算)。
pytest不要(本プロジェクトの既存規約: test_grid_floatstop_static.py と同じ plain-assert 形式)。

Usage: python3 optimizer/test_mr_forward_review_killswitch.py   (exit 0 = 全件PASS)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import mr_forward_review as R

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def base_s(**over):
    s = {'n': 40, 'pf': 1.5, 'wr': 0.7, 'net': 100_000.0, 'expectancy': 2500.0,
         'max_dd': 50_000.0, 'mc95_jpy': 400_000.0, 'pf_12mo': 1.5,
         'span_days': 100, 'sl_fires': 2}
    s.update(over)
    return s


# ─────────────────────────────────────────────────────────────────────────
print('=' * 78)
print('[1] kill_check() 単体 - pf12_lt_1')
print('=' * 78)
# 1a: ローリング12ヶ月PF<1.0 かつ n>=10 -> TRIGGER
s = base_s(pf_12mo=0.75, n=20)
kc = R.kill_check(s)
check('pf12<1.0 かつ n>=10 で発火', kc['pf12_lt_1'][0] and kc['TRIGGER'][0], kc['pf12_lt_1'][1])

# 1b: ローリング12ヶ月PF<1.0 だが n<10(薄標本) -> 発火しない(誤トリガ防止ガード)
s = base_s(pf_12mo=0.5, n=5)
kc = R.kill_check(s)
check('pf12<1.0 だが n<10 なら誤発火しない', not kc['pf12_lt_1'][0] and not kc['TRIGGER'][0], kc['pf12_lt_1'][1])

# 1c: pf12>=1.0 -> 発火しない
s = base_s(pf_12mo=1.3, n=30)
kc = R.kill_check(s)
check('pf12>=1.0 なら発火しない', not kc['pf12_lt_1'][0] and not kc['TRIGGER'][0], kc['pf12_lt_1'][1])

# 1d: pf12=nan(n不足等で算出不能) -> 発火しない(NaN比較でFalseになることを確認)
s = base_s(pf_12mo=float('nan'), n=30)
kc = R.kill_check(s)
check('pf12=nan は例外にならず発火しない', not kc['pf12_lt_1'][0] and not kc['TRIGGER'][0], kc['pf12_lt_1'][1])

print('\n' + '=' * 78)
print('[2] kill_check() 単体 - dd_gt_mc95')
print('=' * 78)
# 2a: 実現maxDD > MC95 -> TRIGGER
s = base_s(max_dd=500_000.0, mc95_jpy=400_000.0, pf_12mo=1.5, n=30)
kc = R.kill_check(s)
check('maxDD>MC95 で発火', kc['dd_gt_mc95'][0] and kc['TRIGGER'][0], kc['dd_gt_mc95'][1])

# 2b: 実現maxDD == MC95(境界, 超過でない) -> 発火しない
s = base_s(max_dd=400_000.0, mc95_jpy=400_000.0, pf_12mo=1.5, n=30)
kc = R.kill_check(s)
check('maxDD==MC95(境界) は発火しない(> のみ)', not kc['dd_gt_mc95'][0], kc['dd_gt_mc95'][1])

# 2c: maxDD < MC95 -> 発火しない
s = base_s(max_dd=100_000.0, mc95_jpy=400_000.0, pf_12mo=1.5, n=30)
kc = R.kill_check(s)
check('maxDD<MC95 は発火しない', not kc['dd_gt_mc95'][0], kc['dd_gt_mc95'][1])

# 2d: mc95_jpy=0(未算出/ペア未対応) -> 0除算/誤発火しないガード
s = base_s(max_dd=0.0, mc95_jpy=0.0, pf_12mo=1.5, n=30)
kc = R.kill_check(s)
check('mc95_jpy=0 でも誤発火しない(ガード)', not kc['dd_gt_mc95'][0], kc['dd_gt_mc95'][1])

print('\n' + '=' * 78)
print('[3] kill_check() - 両条件同時発火 / TRIGGER集約')
print('=' * 78)
s = base_s(pf_12mo=0.6, n=20, max_dd=900_000.0, mc95_jpy=400_000.0)
kc = R.kill_check(s)
check('pf12/ddの両方発火でTRIGGER=True', kc['pf12_lt_1'][0] and kc['dd_gt_mc95'][0] and kc['TRIGGER'][0])

print('\n' + '=' * 78)
print('[4] promo_check() - 昇格ゲート(計画§5: 90日 ∧ 30約定 ∧ SL1回 ∧ PF>1.2)')
print('=' * 78)
# 4a: 全条件クリア -> ALL
s = base_s(span_days=96, n=30, sl_fires=12, pf=2.29)
pc = R.promo_check(s)
check('全条件クリアでALL=True', pc['ALL'][0])

# 4b: PFだけ未達(CADCHF実データ相当の形だが pf<1.2に変更) -> ALL=False
s = base_s(span_days=96, n=30, sl_fires=12, pf=1.1)
pc = R.promo_check(s)
check('PF未達1項目だけでもALL=False', not pc['ALL'][0] and not pc['pf_gt_1_2'][0])

# 4c: SL/タイムストップが一度も発火していない(生存者バイアス警戒) -> ALL=False
s = base_s(span_days=96, n=30, sl_fires=0, pf=2.0)
pc = R.promo_check(s)
check('SL未発火(生存者バイアス)ならALL=False', not pc['ALL'][0] and not pc['sl_fired'][0])

# 4d: 実データ(2026-10-10時点)相当の回帰確認 - CADCHFは全クリア、AUDCADは未達のはず
s = base_s(span_days=86, n=26, sl_fires=2, pf=2.88)   # AUDCAD実データ相当
pc = R.promo_check(s)
check('AUDCAD実データ相当(span86/n26)はn_30未達でALL=False',
      not pc['ALL'][0] and not pc['n_30'][0] and not pc['span_3mo'][0])

print('\n' + '=' * 78)
print('[5] 結合経路: summarize()のpf_12mo計算 -> kill_check発火(合成クラスタ系列)')
print('=' * 78)
# 健全な9ヶ月(PF>1) + 直近3ヶ月で急激な負け(ローリング12ヶ月PFを1.0未満に落とす)クラスタを合成
rng = np.random.default_rng(7)
now = pd.Timestamp('2026-10-10', tz=None)
rows = []
# t-18mo ~ t-3mo: 健全(勝率70%、小さい勝ちち中心)
for i in range(60):
    t = now - pd.Timedelta(days=(18 * 30 - i * 7))
    net = 3000.0 if rng.random() < 0.7 else -2500.0
    rows.append({'close_time': t, 'open_time': t - pd.Timedelta(hours=8), 'net': net,
                 'win': net > 0, 'reason': 'tp' if net > 0 else 'zstop', 'n_legs': 2,
                 'throttled': False, 'broker': 'axiory'})
# 直近3ヶ月: 連敗(ローリング12moPFを1.0未満に落とす大きめの負け)
for i in range(8):
    t = now - pd.Timedelta(days=(3 * 30 - i * 10))
    rows.append({'close_time': t, 'open_time': t - pd.Timedelta(hours=8), 'net': -40_000.0,
                 'win': False, 'reason': 'time', 'n_legs': 2, 'throttled': False, 'broker': 'axiory'})
cl = pd.DataFrame(rows).sort_values('close_time').reset_index(drop=True)
cl['hold_h'] = (cl['close_time'] - cl['open_time']).dt.total_seconds() / 3600.0
cl['hold_bars'] = (cl['hold_h'] / 4.0).round()

s = R.summarize(cl, lot_scale=1.0)
# 全期間PFは直近の大きな連敗8件(-40,000円×8)が健全期60件のgross winを超え<1.0に沈む
# のが正しい挙動(全期間PFは「薄い通算」, pf_12moが「直近エッジ消失」を分離検知する対比が目的)。
check('合成系列: 全期間PFは計算できる(NaN/例外なし)', np.isfinite(s['pf']), f"pf={s['pf']:.2f}")
check('合成系列: ローリング12moPFは直近連敗で1.0未満に転落', s['pf_12mo'] < 1.0, f"pf_12mo={s['pf_12mo']:.2f}")
check('合成系列: pf_12mo は全期間pfより更に悪化(直近集中の連敗を反映)',
      s['pf_12mo'] < s['pf'], f"pf_12mo={s['pf_12mo']:.2f} < pf={s['pf']:.2f}")

# mc95を実現DD以下に固定してdd_gt_mc95も同時発火させる(基準値はAUDCAD REFから借用)
s_strict = dict(s)
s_strict['mc95_jpy'] = s['max_dd'] * 0.5
kc = R.kill_check(s_strict)
check('結合経路: pf_12mo<1.0 でキルスイッチTRIGGER', kc['pf12_lt_1'][0] and kc['TRIGGER'][0],
      f"pf_12mo={s['pf_12mo']:.2f}")
check('結合経路: maxDD>mc95(縮小)でdd_gt_mc95もTRIGGER', kc['dd_gt_mc95'][0],
      f"maxDD={s['max_dd']:,.0f}/mc95={s_strict['mc95_jpy']:,.0f}")

# 直近連敗を除いた「健全期のみ」の対照系列では発火しないことも確認(偽陽性がないことの確認)
cl_healthy = cl[cl['reason'] != 'time'].copy()
s_h = R.summarize(cl_healthy, lot_scale=1.0)
kc_h = R.kill_check(s_h)
check('対照(連敗を除いた健全系列)はTRIGGERしない', not kc_h['TRIGGER'][0],
      f"pf_12mo={s_h['pf_12mo']:.2f} maxDD={s_h['max_dd']:,.0f}")

print('\n' + '=' * 78)
print('[6] --basket バックストップ: mc95_jpy_override / load_mr の複数magic合算')
print('=' * 78)
# summarize の mc95_jpy_override: override無しなら従来通り REF(AUDCAD) 基準で計算される
s_ref = R.summarize(cl_healthy, lot_scale=1.0)
expected_ref = R.REF['mc95_lotpip'] * R.REF['pip_value_jpy'] * 1.0
check('override無しはREF(現PAIR)基準のmc95_jpyになる', abs(s_ref['mc95_jpy'] - expected_ref) < 1e-6,
      f"mc95_jpy={s_ref['mc95_jpy']:,.0f} expected={expected_ref:,.0f}")

# mc95_jpy_override指定時はREFを無視してその値(xlot_scale)を使う
s_ov = R.summarize(cl_healthy, lot_scale=1.0, mc95_jpy_override=R.BASKET_REQ_CAP_99_JPY)
check('override指定時はbasket_req_cap_99を使う(lot_scale=1.0)',
      abs(s_ov['mc95_jpy'] - R.BASKET_REQ_CAP_99_JPY) < 1e-6, f"mc95_jpy={s_ov['mc95_jpy']:,.0f}")
s_ov2 = R.summarize(cl_healthy, lot_scale=0.5, mc95_jpy_override=R.BASKET_REQ_CAP_99_JPY)
check('overrideもlot_scaleで比例スケールされる',
      abs(s_ov2['mc95_jpy'] - R.BASKET_REQ_CAP_99_JPY * 0.5) < 1e-6, f"mc95_jpy={s_ov2['mc95_jpy']:,.0f}")

# load_mr(magics=[...]) で複数ペアの生レッグが正しく連結される(=n行が単純合算と一致)
if R.HISTORY_CSV.exists():
    magic_ac, magic_cc = R.PAIRS['AUDCAD']['magic'], R.PAIRS['CADCHF']['magic']
    df_ac = R.load_mr([magic_ac])
    df_cc = R.load_mr([magic_cc])
    df_both = R.load_mr([magic_ac, magic_cc])
    check('load_mr(複数magic)は単独読み込みの合計行数と一致',
          len(df_both) == len(df_ac) + len(df_cc),
          f"both={len(df_both)} ac+cc={len(df_ac)}+{len(df_cc)}")
    # build_clusters は symbol がキーに入るため両ペアを混線させず別クラスタに分離するはず
    cl_both = R.build_clusters(df_both) if not df_both.empty else pd.DataFrame()
    cl_ac = R.build_clusters(df_ac) if not df_ac.empty else pd.DataFrame()
    cl_cc = R.build_clusters(df_cc) if not df_cc.empty else pd.DataFrame()
    check('build_clusters(両magic)のクラスタ数は単独2本の合計と一致(symbolキーで混線しない)',
          len(cl_both) == len(cl_ac) + len(cl_cc),
          f"both={len(cl_both)} ac+cc={len(cl_ac)}+{len(cl_cc)}")
else:
    print('  [skip] history.csv 無し(実データ連結テストはskip)')

print('\n' + '=' * 78)
print(f"結果: PASS={len(PASS)}  FAIL={len(FAIL)}")
print('=' * 78)
if FAIL:
    print('FAILしたケース:')
    for name, detail in FAIL:
        print(f"  - {name}  {detail}")
    sys.exit(1)
print('全ケースPASS。')
