"""
第1段階SL修正
SLをentryの損益ゼロライン（完全ブレークイーブン）に設定
"""
import os, subprocess

BASE  = r'C:\Users\Administrator\fx_bot\vps'
path  = os.path.join(BASE, 'trail_monitor.py')
f     = open(path, encoding='utf-8').read()

old = """        # ── 第1段階：スプレッド+手数料回収でブレークイーブンSL ──
        if cfg['stage1']:
            breakeven_dist = (spread + commission) * 2  # 往復コスト
            if profit_dist >= breakeven_dist:
                # SL = entry - ATR×0.3（ブレークイーブンより少し下）
                be_sl = entry - atr * 0.3 * direction
                if direction == 1 and p.sl < be_sl:
                    new_sl        = round(be_sl, 5)
                    update_reason = f"第1段階（BEスプレッド={spread/pip:.1f}pips回収）"
                elif direction == -1 and (p.sl == 0 or p.sl > be_sl):
                    new_sl        = round(be_sl, 5)
                    update_reason = f"第1段階（BEスプレッド={spread/pip:.1f}pips回収）\""""

new = """        # ── 第1段階：ATR×0.5以上の利益でブレークイーブンSL ──
        # SL = entry（完全ブレークイーブン）に設定
        # 決済されても損益ゼロを保証
        if cfg['stage1']:
            breakeven_dist = atr * 0.5  # ATR×0.5以上の利益で発動
            if profit_dist >= breakeven_dist:
                # SL = entry（損益ゼロライン）
                be_sl = round(entry, 5)
                sl_improvement = (be_sl - p.sl) * direction
                if sl_improvement > min_update:
                    new_sl        = be_sl
                    update_reason = (f"第1段階（利益={profit_dist/pip:.1f}pips"
                                    f"≥ATR×0.5 → BEライン設定）\")\""""

if old in f:
    f = f.replace(old, new)
    open(path, 'w', encoding='utf-8').write(f)
    print('trail_monitor.py 第1段階SL修正完了')
    print('  変更前：SL = entry - ATR×0.3（損失リスク残存）')
    print('  変更後：SL = entry（完全ブレークイーブン）')
    print('  発動条件：スプレッド回収 → ATR×0.5以上の利益')
else:
    print('パターンが見つかりません。現在の第1段階コードを確認します:')
    idx = f.find('第1段階')
    print(repr(f[idx:idx+400]))

r = subprocess.run(['python', '-m', 'py_compile', path],
                   capture_output=True, text=True)
print('構文チェック: ' + ('OK' if r.returncode == 0 else r.stderr))
