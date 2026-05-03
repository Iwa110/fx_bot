"""
loop_runner.py - FX最適化ループ オーケストレーター
C:\\Users\\Administrator\\fx_bot\\optimizer\\loop_runner.py
"""

import json
import logging
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# ============================================================
# 設定
# ============================================================
BASE_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')
VPS_DIR  = Path(r'C:\Users\Administrator\fx_bot\vps')
DATA_DIR = Path(r'C:\Users\Administrator\fx_bot\data')

RUN_DIR  = BASE_DIR / 'runs'
LOG_DIR  = BASE_DIR / 'logs'

APPROVAL_CRITERIA = {
    'pf_min':        1.00,
    'dd_max':       -0.15,
    'win_rate_min':  0.45,
    'tp_reach_min':  0.20,
}

TARGET_STRATEGIES = [
    {'strategy': 'BB', 'symbol': 'USDCAD'},
    {'strategy': 'BB', 'symbol': 'GBPJPY'},
]

# ============================================================
# ロギング
# ============================================================
def setup_logger(run_id: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(run_id)
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    fh = logging.FileHandler(LOG_DIR / f'{run_id}.log', encoding='utf-8')
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger

# ============================================================
# ユーティリティ
# ============================================================
def now_str() -> str:
    return datetime.now().isoformat(timespec='seconds')


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# Phase 1: 評価・集計
# ============================================================
# ============================================================
# Phase 1: 評価・集計（evaluate.py連携版）
# ============================================================
def phase1_evaluate(run_dir: Path, target: dict, logger: logging.Logger) -> Path:
    logger.info('[Phase1] 評価開始: %s %s', target['strategy'], target['symbol'])

    evaluate_script = BASE_DIR / 'evaluate.py'

    # MAGICマップ（戦略+シンボル → magic番号）
    MAGIC_MAP = {
        ('BB', 'USDCAD'): 20250001,
        ('BB', 'GBPJPY'): 20250001,
        # 必要に応じて追加
    }
    magic = MAGIC_MAP.get((target['strategy'], target['symbol']))

    cmd = [sys.executable, str(evaluate_script)]
    if magic:
        cmd += ['--magic', str(magic)]
    else:
        logger.warning('[Phase1] MAGICマップ未定義: %s %s → --symbolのみで実行',
                       target['strategy'], target['symbol'])
        pass

    out_path = run_dir / 'metrics.json'
    cmd += ['--out', str(out_path)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=120,
        )
        if proc.returncode != 0:
            logger.error('[Phase1] evaluate.pyエラー: %s', proc.stderr[:300])
            raise RuntimeError(f'evaluate.py failed: {proc.stderr[:300]}')
        if proc.stdout:
            logger.info('[Phase1] stdout: %s', proc.stdout[-300:])

    except subprocess.TimeoutExpired:
        logger.error('[Phase1] タイムアウト(120s)')
        raise

    if not out_path.exists():
        raise FileNotFoundError(f'[Phase1] metrics.json未生成: {out_path}')

    # 生成内容をログに残す
    metrics_data = load_json(out_path)
    m = metrics_data.get('metrics', {})
    logger.info('[Phase1] 完了 -> PF=%.2f WR=%.1f%% trades=%d',
                m.get('pf', 0),
                m.get('win_rate', 0) * 100,
                metrics_data.get('trades_count', 0))
    return out_path

# ============================================================
# Phase 2: AI分析 (Claude API)
# ============================================================
def phase2_ai_analysis(run_dir: Path, metrics_path: Path,
                       logger: logging.Logger) -> Path:
    """
    metrics.json + backtest_results.json(前回蓄積) を読み込み
    Claude APIで複合パラメータ候補を生成してcandidates.jsonを出力する。
    戻り値: candidates.jsonのPath
    """
    logger.info('[Phase2] AI分析開始')

    # phase2_ai_analysis.pyのrun_phase2()を呼び出す
    # run_phase2はBASE_DIRのmetrics.jsonとbacktest_results.jsonを参照するため
    # run_dirのmetrics.jsonをBASE_DIRにコピーしてから実行
    import shutil
    shutil.copy(metrics_path, BASE_DIR / 'metrics.json')

    sys.path.insert(0, str(BASE_DIR))
    from phase2_ai_analysis import run_phase2  # type: ignore
    count = run_phase2()

    candidates_path = BASE_DIR / 'candidates.json'
    if count == 0 or not candidates_path.exists():
        logger.warning('[Phase2] 候補0件 → 空candidatesで継続')
        save_json(candidates_path, {'candidates': []})

    # run_dirにもコピー（実行記録として保存）
    run_candidates = run_dir / 'candidates.json'
    shutil.copy(candidates_path, run_candidates)

    logger.info('[Phase2] 完了 -> %d件 -> %s', count, run_candidates)
    return run_candidates


# ============================================================
# Phase 3: BT自動実行（backtest.py連携版）
# ============================================================
def phase3_backtest(run_dir: Path, candidates_path: Path,
                    target: dict, logger: logging.Logger) -> Path:
    """
    backtest.pyをsubprocess実行し、出力をbt_results.json形式に変換する。

    backtest.py出力: backtest_results.json (フラット配列)
      candidates形式: [{id, params, description, priority, result, pf, win_rate, ...}]
      suggestions形式(後方互換): [{param, candidate, pf, win_rate, ...}]

    bt_results.json: {results: [{candidate_id, status, metrics, raw}]}
    """
    logger.info('[Phase3] BT実行開始')

    bt_script = BASE_DIR / 'backtest.py'
    bt_output = run_dir / 'backtest_results.json'

    # candidates.jsonをBASE_DIRに配置（backtest.pyがカレントから読む）
    import shutil
    shutil.copy(candidates_path, BASE_DIR / 'candidates.json')

    try:
        proc = subprocess.run(
            [sys.executable, str(bt_script),
             '--symbol', target['symbol'],
             '--output', str(bt_output),
             '--mode',   'candidates'],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=600,
        )
        if proc.returncode != 0:
            logger.error('[Phase3] backtest.pyエラー: %s', proc.stderr[:300])
            raise RuntimeError(f'backtest.py failed: {proc.stderr[:300]}')
        if proc.stdout:
            logger.info('[Phase3] stdout: %s', proc.stdout[-500:])

    except subprocess.TimeoutExpired:
        logger.error('[Phase3] タイムアウト(600s)')
        raise

    # --- スキーマ変換 ---
    raw_list: list = load_json(bt_output)
    results = []

    for row in raw_list:
        # candidates形式: idフィールドあり
        # suggestions形式(後方互換): param/candidateフィールドあり
        if 'id' in row:
            cand_id = row['id']
        else:
            cand_id = f"{row.get('param', 'unknown')}_{row.get('candidate', 'unknown')}"

        # win_rate: backtest.pyは%値で返す → 小数に変換
        win_rate_raw      = row.get('win_rate', 0.0)
        tp_reach_raw      = row.get('tp_reach_rate', 0.0)
        win_rate_decimal  = win_rate_raw / 100 if win_rate_raw > 1.0 else win_rate_raw
        tp_reach_decimal  = tp_reach_raw / 100 if tp_reach_raw > 1.0 else tp_reach_raw

        results.append({
            'candidate_id': cand_id,
            'status':       'success',
            'metrics': {
                'pf':            row.get('pf', 0.0),
                'win_rate':      win_rate_decimal,
                'rr_actual':     row.get('rr_actual', 0.0),
                'tp_reach_rate': tp_reach_decimal,
                'max_dd':        row.get('max_dd', 0.0),
                'total_profit':  row.get('total_profit_pips', 0.0),
                'trades':        row.get('trades', 0),
            },
            'raw': row,
        })

    out_data = {'generated_at': now_str(), 'results': results}
    out_path = run_dir / 'bt_results.json'
    save_json(out_path, out_data)
    logger.info('[Phase3] 完了 -> %s  件数: %d', out_path, len(results))
    return out_path


# ============================================================
# Phase 4: 採否判断
# ============================================================
def phase4_verdict(run_dir: Path, bt_results_path: Path,
                   logger: logging.Logger) -> Path:
    logger.info('[Phase4] 採否判断開始')

    bt_data  = load_json(bt_results_path)
    criteria = APPROVAL_CRITERIA
    verdicts = []

    for result in bt_data.get('results', []):
        cand_id = result['candidate_id']

        # baselineはスキップ
        if cand_id == 'baseline':
            continue

        if result['status'] != 'success':
            verdicts.append({'candidate_id': cand_id, 'approved': False,
                             'reason': f"BT status={result['status']}"})
            continue

        m = result['metrics']
        fails = []

        if m.get('pf', 0) < criteria['pf_min']:
            fails.append(f"PF={m.get('pf', 0):.2f} < {criteria['pf_min']}")
        if m.get('max_dd', 0) < criteria['dd_max']:
            fails.append(f"DD={m.get('max_dd', 0):.2%} < {criteria['dd_max']:.2%}")
        if m.get('win_rate', 0) < criteria['win_rate_min']:
            fails.append(f"WR={m.get('win_rate', 0):.2%} < {criteria['win_rate_min']:.2%}")
        if m.get('tp_reach_rate', 0) < criteria['tp_reach_min']:
            fails.append(f"TP到達={m.get('tp_reach_rate', 0):.2%} < {criteria['tp_reach_min']:.2%}")

        approved = len(fails) == 0
        verdicts.append({
            'candidate_id':    cand_id,
            'approved':        approved,
            'reason':          'OK' if approved else ' / '.join(fails),
            'metrics_snapshot': m,
        })
        logger.info('[Phase4] %s -> %s  %s', cand_id,
                    'APPROVED' if approved else 'REJECTED',
                    '' if approved else '|'.join(fails))

    out_data = {
        'generated_at':   now_str(),
        'criteria':       criteria,
        'verdicts':       verdicts,
        'approved_count': sum(1 for v in verdicts if v['approved']),
    }
    out_path = run_dir / 'verdict.json'
    save_json(out_path, out_data)
    logger.info('[Phase4] 完了 -> 採用: %d/%d', out_data['approved_count'], len(verdicts))
    return out_path


# ============================================================
# Phase 5: 採用パラメータ保存
# ============================================================
def phase5_apply(run_dir: Path, verdict_path: Path, candidates_path: Path,
                 target: dict, logger: logging.Logger) -> Path:
    logger.info('[Phase5] 採用パラメータ保存開始')

    verdict_data    = load_json(verdict_path)
    candidates_data = load_json(candidates_path)

    # candidates.jsonのidをキーにしたマップ
    cand_map = {c['id']: c for c in candidates_data.get('candidates', [])}

    approved = []
    for v in verdict_data.get('verdicts', []):
        if not v['approved']:
            continue
        cand_id = v['candidate_id']
        cand    = cand_map.get(cand_id, {})
        params  = cand.get('params', {})

        approved.append({
            'candidate_id': cand_id,
            'strategy':     target['strategy'],
            'symbol':       target['symbol'],
            'params':       params,
            'description':  cand.get('description', ''),
            'metrics':      v['metrics_snapshot'],
            'apply_status': 'pending_human_review',
        })

    out_data = {'generated_at': now_str(), 'approved_params': approved}
    out_path = run_dir / 'approved.json'
    save_json(out_path, out_data)

    latest_path = BASE_DIR / 'approved_latest.json'
    save_json(latest_path, out_data)

    if approved:
        logger.info('[Phase5] 採用パラメータ %d件 -> %s', len(approved), latest_path)
        for p in approved:
            logger.info('  >> %s: %s', p['candidate_id'], p['params'])
    else:
        logger.info('[Phase5] 採用パラメータなし (全候補が基準未達)')

    return out_path


# ============================================================
# メインループ
# ============================================================
def run_loop(target: dict) -> None:
    run_id  = f"{target['strategy']}_{target['symbol']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = RUN_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(run_id)

    PHASE_LABELS = [
        'Phase1 評価・集計',
        'Phase2 AI分析',
        'Phase3 BT実行',
        'Phase4 採否判断',
        'Phase5 パラメータ保存',
    ]
    total = len(PHASE_LABELS)

    def progress(idx: int, status: str = '') -> None:
        bar   = '█' * idx + '░' * (total - idx)
        pct   = int(idx / total * 100)
        label = PHASE_LABELS[idx] if idx < total else '完了'
        print(f'\r[{bar}] {pct:3d}%  {label:<20} {status}    ', end='', flush=True)

    logger.info('=== ループ開始: %s ===', run_id)
    print(f'\n=== {run_id} ===')

    try:
        progress(0, '実行中...')
        metrics_path = phase1_evaluate(run_dir, target, logger)

        progress(1, '実行中...')
        candidates_path = phase2_ai_analysis(run_dir, metrics_path, logger)

        # Phase2が0件なら以降スキップ
        candidates_data = load_json(candidates_path)
        if not candidates_data.get('candidates'):
            print(f'\r[██░░░]  40%  Phase2候補0件 → スキップ{" ":40}')
            logger.info('=== Phase2候補0件のためループ終了 ===')
            return

        progress(2, '実行中...')
        bt_results_path = phase3_backtest(run_dir, candidates_path, target, logger)

        bt_data  = load_json(bt_results_path)
        bt_count = len([r for r in bt_data.get('results', []) if r['candidate_id'] != 'baseline'])

        progress(3, f'実行中...  (BT:{bt_count}件)')
        verdict_path = phase4_verdict(run_dir, bt_results_path, logger)

        v_data   = load_json(verdict_path)
        approved = v_data.get('approved_count', 0)

        progress(4, f'実行中...  (採用:{approved}/{bt_count}件)')
        approved_path = phase5_apply(run_dir, verdict_path, candidates_path, target, logger)

        print(f'\r[█████] 100%  完了 ✓  採用:{approved}/{bt_count}件{" ":40}')
        print(f'出力: {run_dir}')
        logger.info('=== ループ完了: %s ===', run_id)

    except Exception:
        print(f'\r[ERROR] 失敗{"":40}')
        logger.error('[FATAL] 予期せぬエラー:\n%s', traceback.format_exc())
        save_json(run_dir / 'error.json',
                  {'run_id': run_id, 'error': traceback.format_exc(), 'generated_at': now_str()})
        raise


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='FX最適化ループ')
    parser.add_argument('--strategy', default='BB')
    parser.add_argument('--symbol',   default='USDCAD')
    parser.add_argument('--all',      action='store_true', help='全対象を順次実行')
    args = parser.parse_args()

    if args.all:
        for t in TARGET_STRATEGIES:
            run_loop(t)
    else:
        run_loop({'strategy': args.strategy, 'symbol': args.symbol})