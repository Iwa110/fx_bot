"""
update_claude_md.py - CLAUDE.md自動更新スクリプト v1
VPS(Windows)でTask Schedulerにより毎日 07:10 JST に実行。
history.csvからパフォーマンス統計を計算し、CLAUDE.mdのAUTO_STATSセクションを更新してgit push。

Task Scheduler設定:
  トリガー: 毎日 07:10 JST（daily_report.py 07:00実行の10分後）
  操作: python C:\Users\Administrator\fx_bot\vps\update_claude_md.py
"""

import os
import csv
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE_DIR     = r'C:\Users\Administrator\fx_bot'
HISTORY_CSV  = os.path.join(BASE_DIR, 'optimizer', 'history.csv')
CLAUDE_MD    = os.path.join(BASE_DIR, 'CLAUDE.md')
JST          = timezone(timedelta(hours=9))

MARKER_BEGIN = '<!-- AUTO_STATS_BEGIN -->'
MARKER_END   = '<!-- AUTO_STATS_END -->'

MAGIC_MAP = {
    20250001: 'BB',
    20260001: 'stat_arb',
    20260010: 'SMA_SQ',
    20240101: 'MOM_JPY',
    20240102: 'MOM_GBJ',
    20240104: 'STR',
    20240107: 'MOM_GBU',
}


def _pf(profits):
    wins   = sum(p for p in profits if p > 0)
    losses = abs(sum(p for p in profits if p < 0))
    if losses == 0:
        return float('inf')
    return round(wins / losses, 3)


def _wr(profits):
    n = len(profits)
    return round(len([p for p in profits if p > 0]) / n * 100, 1) if n > 0 else 0.0


def _pf_str(profits):
    v = _pf(profits)
    return 'inf' if v == float('inf') else f'{v:.3f}'


def load_history():
    rows = []
    with open(HISTORY_CSV, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            row['profit'] = float(row['profit'])
            row['magic']  = int(row.get('magic', 0))
            try:
                ct     = datetime.strptime(row['close_time'], '%Y.%m.%d %H:%M:%S')
                ct_utc = ct.replace(tzinfo=timezone.utc)
                row['date_jst'] = ct_utc.astimezone(JST).date()
            except Exception:
                row['date_jst'] = None
            rows.append(row)
    return [r for r in rows if r['date_jst'] is not None]


def build_stats_section(rows):
    now_jst       = datetime.now(JST)
    today         = now_jst.date()
    last7_start   = today - timedelta(days=6)

    today_rows = [r for r in rows if r['date_jst'] == today]
    last7_rows = [r for r in rows if r['date_jst'] >= last7_start]
    bb_rows    = [r for r in rows if r['magic'] == 20250001]

    def base_profits(row_list):
        return [r['profit'] for r in row_list if not r['symbol'].endswith('m')]

    lines = []
    lines.append(f'更新日時: {now_jst.strftime("%Y-%m-%d %H:%M")} JST')
    lines.append('')

    # ── 本日 ────────────────────────────────
    today_p = base_profits(today_rows)
    lines.append(f'### 本日 {today}')
    if today_p:
        lines.append(
            f'損益: {sum(today_p):+,.0f}円  '
            f'PF={_pf_str(today_p)}  '
            f'WR={_wr(today_p):.1f}%  '
            f'n={len(today_p)}'
        )
    else:
        lines.append('取引なし')
    lines.append('')

    # ── 直近7日サマリー ─────────────────────
    d7_p = base_profits(last7_rows)
    lines.append(f'### 直近7日（{last7_start}〜{today}）')
    lines.append(
        f'総損益: {sum(d7_p):+,.0f}円  '
        f'PF={_pf_str(d7_p)}  '
        f'WR={_wr(d7_p):.1f}%  '
        f'n={len(d7_p)}'
    )
    lines.append('')

    # ペア別
    pair_g = defaultdict(list)
    for r in last7_rows:
        if not r['symbol'].endswith('m'):
            pair_g[r['symbol']].append(r['profit'])

    lines.append('| ペア | 損益 | PF | WR | n | |')
    lines.append('|------|------|----|----|---|---|')
    for sym in sorted(pair_g.keys()):
        ps   = pair_g[sym]
        pv   = _pf(ps)
        flag = '⚠️' if pv < 0.5 else ('✅' if pv >= 1.2 else '')
        lines.append(
            f'| {sym} | {sum(ps):+,.0f}円 | {_pf_str(ps)} | {_wr(ps):.1f}% | {len(ps)} | {flag} |'
        )
    lines.append('')

    # ── 日次推移 ────────────────────────────
    day_g = defaultdict(list)
    for r in last7_rows:
        day_g[r['date_jst']].append(r['profit'])
    lines.append('### 日次推移（直近7日）')
    for d in sorted(day_g.keys()):
        ps   = day_g[d]
        sign = '+' if sum(ps) >= 0 else ''
        lines.append(f'- {d}: {sum(ps):+,.0f}円  n={len(ps)}')
    lines.append('')

    # ── 戦略別（直近7日） ───────────────────
    strat_g = defaultdict(list)
    for r in last7_rows:
        name = MAGIC_MAP.get(r['magic'], f'magic={r["magic"]}')
        if not r['symbol'].endswith('m'):
            strat_g[name].append(r['profit'])
    lines.append('### 戦略別（直近7日）')
    lines.append('| 戦略 | 損益 | PF | WR | n |')
    lines.append('|------|------|----|----|---|')
    for s in sorted(strat_g.keys()):
        ps = strat_g[s]
        lines.append(f'| {s} | {sum(ps):+,.0f}円 | {_pf_str(ps)} | {_wr(ps):.1f}% | {len(ps)} |')
    lines.append('')

    # ── BB Phase1進捗（全期間） ─────────────
    bb_pair_g = defaultdict(list)
    for r in bb_rows:
        if not r['symbol'].endswith('m'):
            bb_pair_g[r['symbol']].append(r['profit'])
    lines.append('### BB戦略 Phase1進捗（全期間）')
    lines.append('判定基準: PF>1.2 / WR>50%')
    lines.append('| ペア | PF | WR | n | 判定 |')
    lines.append('|------|----|----|---|------|')
    for sym in sorted(bb_pair_g.keys()):
        ps   = bb_pair_g[sym]
        pv   = _pf(ps)
        wv   = _wr(ps)
        ok   = pv >= 1.2 and wv >= 50.0
        lines.append(
            f'| {sym} | {_pf_str(ps)} | {wv:.1f}% | {len(ps)} | {"✅ OK" if ok else "❌ NG"} |'
        )
    lines.append('')

    # ── 自動アラート ────────────────────────
    alerts = []
    for sym in sorted(pair_g.keys()):
        ps = pair_g[sym]
        pv = _pf(ps)
        if pv < 0.5 and len(ps) >= 3:
            alerts.append(f'- ⚠️ {sym}: PF={_pf_str(ps)}（直近7日 n={len(ps)}）→ パラメータ見直し要')
    if len(d7_p) > 0 and sum(d7_p) < -30000:
        alerts.append(f'- ⚠️ 直近7日損益 {sum(d7_p):+,.0f}円 → 大幅マイナス継続中')
    if alerts:
        lines.append('### 自動アラート')
        lines.extend(alerts)
        lines.append('')

    return '\n'.join(lines)


def update_claude_md(stats_text):
    with open(CLAUDE_MD, encoding='utf-8') as f:
        content = f.read()

    bi = content.find(MARKER_BEGIN)
    ei = content.find(MARKER_END)
    if bi == -1 or ei == -1:
        print('[ERROR] CLAUDE.mdにAUTO_STATSマーカーが見つかりません')
        return False

    new_block   = f'{MARKER_BEGIN}\n{stats_text}\n{MARKER_END}'
    new_content = content[:bi] + new_block + content[ei + len(MARKER_END):]

    with open(CLAUDE_MD, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print('[INFO] CLAUDE.md 更新完了')
    return True


def git_push():
    now_str = datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')
    cmds = [
        ['git', 'add', 'CLAUDE.md'],
        ['git', 'commit', '-m', f'auto: stats update {now_str}'],
        ['git', 'push', 'origin', 'main'],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR, shell=True)
        label  = ' '.join(cmd[:2])
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # "nothing to commit" は正常
            if 'nothing to commit' in stderr or 'nothing to commit' in result.stdout:
                print(f'[INFO] {label}: nothing to commit（変更なし）')
            else:
                print(f'[WARN] {label}: {stderr}')
        else:
            print(f'[INFO] {label}: OK')


def main():
    print(f'[INFO] update_claude_md.py 開始 {datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")} JST')

    try:
        rows = load_history()
        print(f'[INFO] history.csv 読み込み: {len(rows)}件')
    except Exception as e:
        print(f'[ERROR] history.csv読み込み失敗: {e}')
        return

    stats_text = build_stats_section(rows)

    if update_claude_md(stats_text):
        git_push()

    print('[INFO] 完了')


if __name__ == '__main__':
    main()
