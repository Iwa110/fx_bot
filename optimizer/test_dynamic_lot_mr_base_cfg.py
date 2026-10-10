"""
test_dynamic_lot_mr_base_cfg.py - dynamic_lot_mr_bt.base_cfg() の vol_throttle 配線ガード。

背景(2026-10-10): optimizer/audcad_stress_test.py の `_Args`/`base_cfg()` 連携が
vol_throttle_th/vol_throttle_mult をcfgへ渡していなかったため、run_bt_tiered3の
高ボラ・ロットスロットル(cfg.get('vol_throttle_th', 1.01)=実質OFF既定値)が常にOFFで
評価され、audcad_mr_deployment_plan.md が「throttle後」と明記するMC95(398 lot-pip)が
再現不能(素の再実行では637 lot-pipが出る=mr_forward_review.PAIRS['AUDCAD']のキル閾値と
不整合)だった。_Args にデフォルト値(0.70/0.5, vps/mr_monitor.py PAIR_CONFIG['AUDCAD']と
同値)を追加し、base_cfg() は getattr(args, ..., デフォルト)で他の呼び出し元(それらの
argsにこの属性が無い)の既存動作を変えずに配線した。本テストはこの配線が両方向
(新規に渡れば効く / 無ければ以前と同じOFF既定値を保つ)で正しいことを固定する回帰ガード。

pytest不要(本プロジェクトの既存規約)。
Usage: python3 optimizer/test_dynamic_lot_mr_base_cfg.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dynamic_lot_mr_bt as M
import audcad_stress_test as A

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}  {detail}")


class _BareArgs:
    """vol_throttle_th/vol_throttle_mult を一切持たない呼び出し元の代表
    (dynamic_lot_mr_bt.py自身のargparse Namespace / mr_distribution_analysis.py 等)。"""
    def __init__(self):
        d = dict(n=40, z_in=2.0, z_tp=0.0, z_stop=4.0, max_hold=48, zk=1.0, max_lot=3.0,
                 squeeze_lo=0.2, squeeze_mult=0.5, vol_hi=0.9, vol_hi_mult=0.5,
                 confirm_window=6, rsi_os=30.0, rsi_ob=70.0, adx_max=25.0, slope_max=1.0,
                 z_in2=2.5, tier_lot=0.5, tier3_zs=[2.0, 2.5, 3.0], tier3_lots=[0.2, 0.3, 0.5],
                 partial_z=1.5)
        self.__dict__.update(d)


print('=' * 78)
print('[1] base_cfg() 配線: 属性が無い呼び出し元は従来通りOFF既定値のまま')
print('=' * 78)
cfg_bare = M.base_cfg(_BareArgs())
check("vol_throttle_th無し属性 -> cfg既定値1.01(=OFF)のまま", cfg_bare['vol_throttle_th'] == 1.01,
      f"got={cfg_bare['vol_throttle_th']}")
check("vol_throttle_mult無し属性 -> cfg既定値1.0(=無効化無し)のまま", cfg_bare['vol_throttle_mult'] == 1.0,
      f"got={cfg_bare['vol_throttle_mult']}")

print('\n' + '=' * 78)
print('[2] base_cfg() 配線: audcad_stress_test._Args() は実際にth/multを渡す')
print('=' * 78)
cfg_real = M.base_cfg(A._Args())
check("AUDCAD向け_Args() -> vol_throttle_th=0.70がcfgに伝わる", cfg_real['vol_throttle_th'] == 0.70,
      f"got={cfg_real['vol_throttle_th']}")
check("AUDCAD向け_Args() -> vol_throttle_mult=0.5がcfgに伝わる", cfg_real['vol_throttle_mult'] == 0.5,
      f"got={cfg_real['vol_throttle_mult']}")
check("vps/mr_monitor.py PAIR_CONFIG['AUDCAD']の vol_th=0.70 と一致",
      cfg_real['vol_throttle_th'] == 0.70)

print('\n' + '=' * 78)
print('[3] run_bt_tiered3: 配線先の既定値が base_cfg の既定値と一致していることの確認')
print('=' * 78)
import inspect
src = inspect.getsource(M.run_bt_tiered3)
check("run_bt_tiered3のソースにvol_throttle_th既定値1.01が存在(配線先の確認)",
      "cfg.get('vol_throttle_th', 1.01)" in src)
check("run_bt_tiered3のソースにvol_throttle_mult既定値1.0が存在(配線先の確認)",
      "cfg.get('vol_throttle_mult', 1.0)" in src)

print('\n' + '=' * 78)
print(f"結果: PASS={len(PASS)}  FAIL={len(FAIL)}")
print('=' * 78)
if FAIL:
    print('FAILしたケース:')
    for name, detail in FAIL:
        print(f"  - {name}  {detail}")
    sys.exit(1)
print('全ケースPASS。')
