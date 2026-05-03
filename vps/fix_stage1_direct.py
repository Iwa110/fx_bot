"""trail_monitor.pyの第1段階SLを直接書き直す"""
import os, re, subprocess

BASE = r'C:\Users\Administrator\fx_bot\vps'
path = os.path.join(BASE, 'trail_monitor.py')
f    = open(path, encoding='utf-8').read()

# 第1段階ブロックを正規表現で丸ごと置換
old = re.search(
    r'        # ── 第1段階.*?update_reason.*?\n',
    f, re.DOTALL
)
if old:
    print(f"既存の第1段階コード発見: {old.start()}〜{old.end()}")
else:
    print("正規表現でも見つかりません。手動確認します:")
    idx = f.find('stage1')
    print(repr(f[idx-50:idx+400]))

new_stage1 = '''        # ── 第1段階：ATR×0.5以上の利益で完全BEラインにSL設定 ──
        if cfg['stage1']:
            if profit_dist >= atr * 0.5:
                be_sl = round(entry, 5)
                sl_improvement = (be_sl - p.sl) * direction
                if sl_improvement > min_update:
                    new_sl        = be_sl
                    update_reason = "第1段階(BEライン)"
'''

if old:
    f = f[:old.start()] + new_stage1 + f[old.end():]
    open(path, 'w', encoding='utf-8').write(f)
    print('修正完了')
    r = subprocess.run(['python', '-m', 'py_compile', path],
                       capture_output=True, text=True)
    print('構文チェック: ' + ('OK' if r.returncode == 0 else r.stderr))
