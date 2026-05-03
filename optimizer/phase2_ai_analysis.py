"""
phase2_ai_analysis.py - Phase2: ルールベース複合パラメータ候補生成
入力: metrics.json, backtest_results.json(前回蓄積・任意)
出力: candidates.json
API不要・即実行可能
"""

import json
from pathlib import Path
from datetime import datetime

OPTIMIZER_DIR = Path(r'C:\Users\Administrator\fx_bot\optimizer')


def load_prev_approved(br_path: Path) -> list:
    """前回BTのAPPROVED結果を返す"""
    if not br_path.exists():
        return []
    data = json.loads(br_path.read_text(encoding='utf-8'))
    rows = data if isinstance(data, list) else data.get('results', [])
    return [r for r in rows if r.get('result') == 'APPROVED']


def run_phase2():
    # --- 入力読み込み ---
    metrics = json.loads((OPTIMIZER_DIR / 'metrics.json').read_text(encoding='utf-8'))
    m = metrics.get('metrics', {})

    pf        = m.get('pf',            0.0)
    win_rate  = m.get('win_rate',       0.0)  # 小数想定 (0.0〜1.0)
    rr_actual = m.get('rr_actual',      0.0)
    tp_reach  = m.get('tp_reach_rate',  0.0)

    approved_prev = load_prev_approved(OPTIMIZER_DIR / 'backtest_results.json')

    candidates = []
    i = 1

    # ============================================================
    # ルール1: 勝率低い(< 50%) → RSIフィルター強化
    # ============================================================
    if win_rate < 0.50:
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'rsi_buy_max': 30, 'rsi_sell_min': 70},
            'description': f'勝率低下対策(WR={win_rate:.1%}): RSIフィルター強化',
            'priority':    1,
        })
        i += 1
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'rsi_buy_max': 25, 'rsi_sell_min': 75, 'htf_range_sigma': 0.7},
            'description': f'勝率低下対策: RSI極値+HTF絞り込み',
            'priority':    1,
        })
        i += 1

    # ============================================================
    # ルール2: RR低い(< 0.8) → TP拡大 or SL縮小
    # ============================================================
    if rr_actual < 0.80:
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'tp_sl_ratio': 1.5, 'sl_atr_mult': 1.2},
            'description': f'RR改善(RR={rr_actual:.2f}): TP拡大+SL縮小',
            'priority':    1,
        })
        i += 1
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'tp_sl_ratio': 2.0, 'sl_atr_mult': 1.0},
            'description': f'RR改善: TP2倍+SL最小化',
            'priority':    2,
        })
        i += 1

    # ============================================================
    # ルール3: PF低い(< 1.0) → HTF+RSI複合フィルター
    # ============================================================
    if pf < 1.0:
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'htf_range_sigma': 0.5, 'rsi_buy_max': 35, 'rsi_sell_min': 65},
            'description': f'PF改善(PF={pf:.2f}): HTF絞り込み+RSI中間値',
            'priority':    1,
        })
        i += 1

    # ============================================================
    # ルール4: TP到達率低い(< 20%) → SL拡大でノイズ耐性向上
    # ============================================================
    if tp_reach < 0.20:
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      {'sl_atr_mult': 2.0, 'tp_sl_ratio': 1.5},
            'description': f'TP到達率改善(到達率={tp_reach:.1%}): SL拡大でノイズ回避',
            'priority':    2,
        })
        i += 1

    # ============================================================
    # ルール5: 前回APPROVEDの組み合わせ候補
    # ============================================================
    if len(approved_prev) >= 2:
        merged_params = {}
        for r in approved_prev[:3]:
            merged_params.update(r.get('params', {}))
        if merged_params:
            candidates.append({
                'id':          f'cand_{i:03d}',
                'params':      merged_params,
                'description': f'前回APPROVED統合({len(approved_prev)}件の最良パラメータ)',
                'priority':    1,
            })
            i += 1

    elif len(approved_prev) == 1:
        base = approved_prev[0].get('params', {})
        extended = {**base, 'tp_sl_ratio': base.get('tp_sl_ratio', 1.0) + 0.5}
        candidates.append({
            'id':          f'cand_{i:03d}',
            'params':      extended,
            'description': '前回APPROVED + RR改善',
            'priority':    2,
        })
        i += 1

    # ============================================================
    # ルール6: 候補0件のフォールバック
    # ============================================================
    if not candidates:
        candidates = [
            {
                'id':          'cand_001',
                'params':      {'rsi_buy_max': 35, 'rsi_sell_min': 65, 'htf_range_sigma': 0.7},
                'description': 'デフォルト探索: RSI+HTF標準強化',
                'priority':    1,
            },
            {
                'id':          'cand_002',
                'params':      {'tp_sl_ratio': 1.5, 'sl_atr_mult': 1.5},
                'description': 'デフォルト探索: RR1.5固定',
                'priority':    2,
            },
            {
                'id':          'cand_003',
                'params':      {'rsi_buy_max': 30, 'tp_sl_ratio': 1.5, 'htf_range_sigma': 0.5},
                'description': 'デフォルト探索: 全フィルター強化版',
                'priority':    3,
            },
        ]

    # --- 出力 ---
    output = {
        'candidates':     candidates,
        'generated_at':   datetime.now().isoformat(),
        'source_metrics': {
            'pf':       pf,
            'win_rate': win_rate,
            'rr':       rr_actual,
            'tp_reach': tp_reach,
        },
    }

    out_path = OPTIMIZER_DIR / 'candidates.json'
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )

    print(f'[Phase2] {len(candidates)}件の候補を生成 -> candidates.json')
    for c in candidates:
        print(f'  [{c["id"]}] P{c["priority"]} {c["params"]}  {c["description"]}')

    return len(candidates)