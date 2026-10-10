"""
daily_gate.py - 日次評価ルーティンの軽量ゲート

daily_step1.py / grid_gate_review.py / mr_forward_review.py を順に実行し、
「人間(Claude)が分析すべき異常があるか」だけを判定して JSON を標準出力する。
flags が空なら summary をそのまま通知して終了してよい(prompts/daily_analysis.md)。

flags 条件:
  - script_error   : 3スクリプトのいずれかが非0終了 / JSON解析失敗
  - fs_slippage    : LIVE Grid の float-stop(_FS) 実損 > 設定(FLOAT_STOP×LIVE_LOT)×1.3
  - unexpected_trade: LIVE で想定外の magic/symbol(既知の手動取引は除外)
  - margin         : LIVE でストップアウト([so ...])発生 / account_snapshot.csv の維持率<閾値
  - mr_kill        : MR_AC / MR_CC キルスイッチ(12moPF<1.0 or maxDD>MC95)
  - grid_promote   : LIVE Grid ペアが昇格条件(3ヶ月∧TP≥30∧FS発火∧PF>1.2)を達成

Usage:
    python optimizer/daily_gate.py               # JSON (flags + summary)
    python optimizer/daily_gate.py --days 3      # LIVE逸脱の検査窓(日, JST)
    python optimizer/daily_gate.py --verbose     # 各スクリプトの生出力も同梱
"""

import argparse
import ast
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

JST = timezone(timedelta(hours=9))
OPT_DIR = Path(__file__).resolve().parent
REPO = OPT_DIR.parent
HISTORY_CSV = OPT_DIR / 'history.csv'
SNAPSHOT_CSV = OPT_DIR / 'account_snapshot.csv'   # vps/account_snapshot.py が1時間毎に追記
GRID_MONITOR = REPO / 'vps' / 'grid_monitor.py'

LIVE_BROKER = 'oanda_live'
FS_SLIP_MULT = 1.3
MARGIN_LEVEL_MIN = 150.0        # grid_monitor LIVE_MARGIN_LEVEL_MIN と同値
PROMO = {'days': 90, 'tp': 30, 'fs': 1, 'pf': 1.2}   # grid_forward_test_plan.md
MR_PAIRS = {'AUDCAD': 'MR', 'CADCHF': 'MR_CC'}      # mr_forward_review --pair / summary表示名

# 確定Grid 4本(LIVE で想定する magic/symbol)
LIVE_GRID = {20260034: 'AUDCAD', 20260038: 'CADCHF', 20260036: 'AUDNZD', 20260035: 'EURGBP'}

# 自動売買と無関係と確認済みの手動取引(CLAUDE.md「既知の手動取引」) (open or close time, magic, symbol)
KNOWN_MANUAL = {
    ('2026-09-07 11:07:45', 0, 'USDJPY'),
    ('2026-10-08 02:14:14', 0, 'USDJPY'),
}


def is_known_manual(r) -> bool:
    for col in ('open_time', 'close_time'):
        t = r[col]
        if pd.notna(t) and (t.strftime('%Y-%m-%d %H:%M:%S'), int(r['magic']), r['symbol']) in KNOWN_MANUAL:
            return True
    return False


def load_grid_constants() -> tuple[dict, dict]:
    """grid_monitor.py(MT5依存でimport不可)から FLOAT_STOP/LIVE_LOT 辞書を静的に読む。"""
    tree = ast.parse(GRID_MONITOR.read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], 'id', None)
            if name in ('FLOAT_STOP_PER_PAIR', 'LIVE_LOT_PER_PAIR'):
                found[name] = ast.literal_eval(node.value)
    return found['FLOAT_STOP_PER_PAIR'], found['LIVE_LOT_PER_PAIR']


def run_script(args: list) -> tuple[int, str, str]:
    try:
        r = subprocess.run([sys.executable] + args, cwd=REPO, capture_output=True,
                           text=True, timeout=600)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # timeout 等
        return -1, '', repr(e)


def pf_of(s: pd.Series) -> float:
    w = s[s > 0].sum()
    l = -s[s < 0].sum()
    return w / l if l > 0 else (float('inf') if w > 0 else float('nan'))


def load_history() -> pd.DataFrame:
    df = pd.read_csv(HISTORY_CSV)
    if 'broker' not in df.columns:
        df['broker'] = 'legacy_demo'
    df['broker'] = df['broker'].fillna('legacy_demo')
    df['open_time'] = pd.to_datetime(df['open_time'], format='mixed')
    df['close_time'] = pd.to_datetime(df['close_time'], format='mixed')
    df['date_jst'] = df['close_time'].dt.tz_localize('UTC').dt.tz_convert(JST).dt.date
    df['comment'] = df['comment'].fillna('').astype(str)
    df['live'] = df['broker'] == LIVE_BROKER
    return df


def check_live(df: pd.DataFrame, since, flags: list):
    live = df[df['live']]
    recent = live[live['date_jst'] >= since]

    # 想定外 magic/symbol
    for _, r in recent.iterrows():
        key = (r['close_time'].strftime('%Y-%m-%d %H:%M:%S'), int(r['magic']), r['symbol'])
        if is_known_manual(r):
            continue
        if LIVE_GRID.get(int(r['magic'])) != r['symbol']:
            flags.append({'type': 'unexpected_trade',
                          'detail': f"{key[0]} magic={key[1]} {key[2]} {r['type']} "
                                    f"{r['lots']}lot {r['profit']:+,.0f}円 [{r['comment']}]"})

    # ストップアウト(維持率割れ)
    for _, r in recent[recent['comment'].str.contains(r'\[so ', regex=True)].iterrows():
        key = (r['close_time'].strftime('%Y-%m-%d %H:%M:%S'), int(r['magic']), r['symbol'])
        if is_known_manual(r):
            continue
        flags.append({'type': 'margin',
                      'detail': f"stop-out {key[0]} {key[2]} {r['profit']:+,.0f}円 [{r['comment']}]"})

    # FS スリッページ: 同一(symbol, side, close分)の _FS 決済を1バスケットとして合算
    fs_stop, live_lot = load_grid_constants()
    fs = recent[recent['comment'].str.endswith('_FS')].copy()
    if not fs.empty:
        fs['ckey'] = fs['close_time'].dt.floor('min')
        for (sym, side, t), g in fs.groupby(['symbol', 'type', 'ckey']):
            setting = abs(fs_stop.get(sym, 0.0) * live_lot.get(sym, 0.0))
            loss = -g['profit'].sum()
            if setting > 0 and loss > setting * FS_SLIP_MULT:
                flags.append({'type': 'fs_slippage',
                              'detail': f"{sym} {side} {t} FS実損{loss:,.0f}円 > "
                                        f"設定{setting:,.0f}×{FS_SLIP_MULT}"})


def check_margin_snapshot(since, flags: list) -> str:
    """account_snapshot.csv から LIVE 維持率を検査(検査窓内の最小値)。無ければ 'na'。
    margin_level 空欄 = ノーポジ(margin=0)なので対象外。"""
    if not SNAPSHOT_CSV.exists():
        return 'na'
    try:
        s = pd.read_csv(SNAPSHOT_CSV)
        s = s[s['broker'] == LIVE_BROKER].dropna(subset=['margin_level'])
        s = s[pd.to_datetime(s['date_jst']).dt.date >= since]
        if s.empty:
            return 'na'
        r = s.loc[s['margin_level'].astype(float).idxmin()]
        lv = float(r['margin_level'])
        if lv < MARGIN_LEVEL_MIN:
            flags.append({'type': 'margin',
                          'detail': f"維持率{lv:.0f}% < {MARGIN_LEVEL_MIN:.0f}% ({r['date_jst']})"})
        return f'min{lv:.0f}%'
    except Exception as e:
        flags.append({'type': 'script_error', 'detail': f'account_snapshot.csv: {e!r}'})
        return 'err'


def check_grid_promotion(df: pd.DataFrame, today, flags: list) -> list:
    """LIVE 確定Grid ペア別の昇格進捗。達成ペアは flag。進捗文字列を返す。"""
    prog = []
    live = df[df['live']]
    for magic, sym in LIVE_GRID.items():
        g = live[(live['magic'] == magic) & (live['symbol'] == sym)]
        if g.empty:
            continue
        days = (today - g['date_jst'].min()).days
        n_tp = int((g['profit'] > 0).sum())
        fsr = g[g['comment'].str.endswith('_FS')]
        n_fs = int(fsr['close_time'].dt.floor('min').nunique())   # バスケット単位
        pf = pf_of(g['profit'])
        ok = (days >= PROMO['days'] and n_tp >= PROMO['tp'] and n_fs >= PROMO['fs']
              and pf > PROMO['pf'])
        prog.append(f"{sym} {days}d/TP{n_tp}/FS{n_fs}/PF{pf:.2f}")
        if ok:
            flags.append({'type': 'grid_promote',
                          'detail': f"{sym} LIVE昇格条件達成 {days}d TP{n_tp} FS{n_fs} PF{pf:.2f}"})
    return prog


def main():
    ap = argparse.ArgumentParser(description='日次評価ゲート(JSON出力)')
    ap.add_argument('--days', type=int, default=2,
                    help='LIVE逸脱(想定外取引/FS/stop-out)の検査窓 日数(JST, 当日含む)')
    ap.add_argument('--verbose', action='store_true', help='各スクリプトの生出力を同梱')
    args = ap.parse_args()

    flags, raw = [], {}
    today = datetime.now(JST).date()
    since = today - timedelta(days=args.days - 1)

    # ── 1) 3スクリプトを順に実行 ──
    rc, out, err = run_script(['optimizer/daily_step1.py'])
    raw['daily_step1'] = out
    if rc != 0:
        flags.append({'type': 'script_error', 'detail': f'daily_step1 rc={rc}: {err.strip()[-300:]}'})

    grid = None
    rc, out, err = run_script(['optimizer/grid_gate_review.py', '--json'])
    raw['grid_gate_review'] = out
    try:
        if rc != 0:
            raise RuntimeError(f'rc={rc}: {err.strip()[-300:]}')
        grid = json.loads(out)
    except Exception as e:
        flags.append({'type': 'script_error', 'detail': f'grid_gate_review {e}'})

    mrs = {}
    for pair in MR_PAIRS:
        rc, out, err = run_script(['optimizer/mr_forward_review.py', '--json', '--pair', pair])
        raw[f'mr_forward_review_{pair}'] = out
        try:
            if rc != 0:
                raise RuntimeError(f'rc={rc}: {err.strip()[-300:]}')
            mrs[pair] = json.loads(out)
        except Exception as e:
            mrs[pair] = None
            flags.append({'type': 'script_error', 'detail': f'mr_forward_review {pair} {e}'})

    # ── 2) history.csv から LIVE 逸脱 / 昇格 / 当日損益 ──
    live_today = demo_today = 0.0
    n_live_today = 0
    grid_prog = []
    try:
        df = load_history()
        check_live(df, since, flags)
        grid_prog = check_grid_promotion(df, today, flags)
        t = df[df['date_jst'] == today]
        live_today = float(t[t['live']]['profit'].sum())
        n_live_today = int(t['live'].sum())
        demo_today = float(t[~t['live']]['profit'].sum())
    except Exception as e:
        flags.append({'type': 'script_error', 'detail': f'history.csv解析: {e!r}'})
    margin = check_margin_snapshot(since, flags)

    # ── 3) MR_AC / MR_CC キルスイッチ ──
    mr_txts = []
    for pair, label in MR_PAIRS.items():
        mr = mrs.get(pair)
        if mr is None:
            mr_txts.append(f'{label}:NA')
            continue
        if mr.get('n', 0) == 0:
            mr_txts.append(f'{label}:待機')
            continue
        mr_txts.append(f"{label}:n{mr['n']} PF{float(mr['pf']):.2f} DD{float(mr['max_dd'])/1e4:.1f}万")
        kill = mr.get('kill', {})
        if kill.get('TRIGGER'):
            why = [k for k in ('pf12_lt_1', 'dd_gt_mc95') if kill.get(k)]
            flags.append({'type': 'mr_kill',
                          'detail': f"{label} キルスイッチ {','.join(why)} "
                                    f"12moPF={float(mr['pf_12mo']):.2f} "
                                    f"maxDD={float(mr['max_dd']):,.0f}/MC95={float(mr['mc95_jpy']):,.0f}円"})

    grid_txt = grid['summary'].replace('Grid ', '') if grid else 'NA'
    tail = f"要確認{len(flags)}件:" + ','.join(sorted({f['type'] for f in flags})) \
        if flags else '異常なし・監視継続'
    summary = (f"FX {today:%m-%d} 実{live_today:+,.0f}円({n_live_today}) / "
               f"demo{demo_today:+,.0f}円 / Grid:{grid_txt} / {' '.join(mr_txts)} / {tail}")

    result = {
        'date': str(today),
        'flags': flags,
        'summary': summary,
        'context': {'live_window_since': str(since), 'margin_level': margin,
                    'grid_live_progress': grid_prog},
    }
    if args.verbose:
        result['raw'] = raw
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
