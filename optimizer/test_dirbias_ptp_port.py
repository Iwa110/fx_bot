"""
test_dirbias_ptp_port.py - grid_dirbias_improve_bt.run_bt への ptp(部分利確)移植の回帰ガード。

[1] ptp_frac=None(既定) の出力が移植前(commit 2d0aacd)の run_bt と完全一致
    (regime_short / cull / combo / short_lot_mult / collect=True の月次・イベント列まで)。
[2] ptp_frac 設定 + 他オプション全OFF で grid_floatstop_bt.run_backtest(ptp 本家) と一致
    (pf / total_pnl / n_tp / n_ptp / n_fstop / n_b48 / max_dd / monthly)。
[3] 不正 ptp_frac は ValueError。

実行: python optimizer/test_dirbias_ptp_port.py  (CADCHF Dukascopy 11年 全期間)
"""
import importlib.util
import math
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grid_floatstop_bt as G          # noqa: E402
import grid_dd_reduction_bt as D       # noqa: E402
import grid_dirbias_improve_bt as DB   # noqa: E402

PRE_PORT_COMMIT = '2d0aacd'
CFG = {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
       'max_levels': 5, 'float_stop': -943000.0, 'quote_jpy': 170.0}


def load_pre_port():
    src = subprocess.run(['git', 'show', f'{PRE_PORT_COMMIT}:optimizer/grid_dirbias_improve_bt.py'],
                         cwd=HERE, capture_output=True, text=True, check=True).stdout
    f = Path(tempfile.mkdtemp()) / 'dirbias_pre_port.py'
    f.write_text(src)
    spec = importlib.util.spec_from_file_location('dirbias_pre_port', f)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def eq(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isinf(a) and math.isinf(b):
        return True
    return a == b


def main():
    old = load_pre_port()
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr); reg = DB.sma_regime(df, 1200)
    cases = [
        ('plain', {}),
        ('regime', {'short_block_up': reg}),
        ('regime+cull0.6', {'short_block_up': reg, 'cull_frac': 0.6}),
        ('combo', {'ret24': r24, 'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}),
        ('short_lot0.5+lv', {'short_lot_mult': 0.5, 'short_ml': 3, 'long_ml': 4}),
    ]
    fails = 0; n = 0
    # [1]
    for name, kw in cases:
        a = DB.run_bt(CFG, df, atr, ci, collect=True, **kw)
        b = old.run_bt(CFG, df, atr, ci, collect=True, **kw)
        n += 1
        ok = a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
        fails += not ok
        print(f"[1] {'PASS' if ok else 'FAIL'} ptp=None == pre-port  {name}")
    # [2]
    for frac, mult in [(0.35, 0.5), (0.25, 0.5), (0.45, 0.7)]:
        c = dict(CFG, ptp_frac=frac, ptp_mult=mult)
        g = G.run_backtest('CADCHF', c, df, atr, ci)
        a = DB.run_bt(CFG, df, atr, ci, collect=True, ptp_frac=frac, ptp_mult=mult)
        keys = [('pf', 'pf'), ('total_pnl', 'total_pnl'), ('n_tp', 'n_tp'), ('n_ptp', 'n_ptp'),
                ('n_fstop', 'n_fstop'), ('n_b48', 'n_b48'), ('max_dd', 'max_dd'), ('monthly', 'monthly')]
        n += 1
        bad = [k for k, j in keys if not (abs(a[k] - g[j]) < 1e-6 if k != 'monthly'
                                          else all(abs(a[k][m] - g[j][m]) < 1e-3 for m in g[j])
                                          and a[k].keys() == g[j].keys())]
        fails += bool(bad)
        print(f"[2] {'PASS' if not bad else 'FAIL ' + str(bad)} ptp({frac},{mult}) == grid_floatstop_bt")
    # [3]
    n += 1
    try:
        DB.run_bt(CFG, df.iloc[:500], atr, ci, ptp_frac=1.0); ok = False
    except ValueError:
        ok = True
    fails += not ok
    print(f"[3] {'PASS' if ok else 'FAIL'} invalid ptp_frac raises")
    print(f'{n - fails}/{n} PASS')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
