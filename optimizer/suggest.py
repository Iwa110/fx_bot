"""
suggest.py - Phase2: パラメータ候補生成
入力: optimizer\metrics.json
出力: optimizer\suggestions.json [{param, current, candidates, reason}]
"""

import json
import os
import sys
from datetime import datetime

METRICS_FILE     = r'C:\Users\Administrator\fx_bot\optimizer\metrics.json'
SUGGESTIONS_FILE = r'C:\Users\Administrator\fx_bot\optimizer\suggestions.json'
LOG_FILE         = r'C:\Users\Administrator\fx_bot\optimizer\suggest_log.txt'

def log(msg):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_metrics():
    if not os.path.exists(METRICS_FILE):
        raise FileNotFoundError('metrics.json not found: ' + METRICS_FILE)
    with open(METRICS_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_suggestions(suggestions):
    with open(SUGGESTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)
    log('suggestions.json 保存完了: ' + str(len(suggestions)) + '件')


# ══════════════════════════════════════════
# ルールベース候補生成
# ══════════════════════════════════════════

def rule_rr_too_low(metrics, suggestions):
    """rr_actual < 0.8 → tp_dist拡張候補"""
    rr = metrics.get('rr_actual', 1.0)
    if rr >= 0.8:
        return
    current_tp = metrics.get('current_params', {}).get('tp_multiplier', 1.0)
    candidates = [1.5, 1.75, 2.0]
    candidates = [c for c in candidates if c > current_tp]
    if not candidates:
        candidates = [current_tp * 1.25, current_tp * 1.5]
        candidates = [round(c, 2) for c in candidates]
    suggestions.append({
        'param':      'tp_multiplier',
        'current':    current_tp,
        'candidates': candidates,
        'reason':     'rr_actual=' + str(round(rr, 3)) + ' < 0.8 → TP距離を拡張してRR改善',
        'priority':   1,
    })
    log('rule_rr_too_low: rr=' + str(round(rr, 3)) + ' candidates=' + str(candidates))


def rule_winrate_below_breakeven(metrics, suggestions):
    """win_rate < breakeven_winrate → filterパラメータ強化候補"""
    win_rate         = metrics.get('win_rate', 0.5)
    breakeven        = metrics.get('breakeven_winrate', 0.5)
    current_params   = metrics.get('current_params', {})
    filter_type      = current_params.get('filter_type')
    f1_param         = current_params.get('f1_param', 5)
    f2_param         = current_params.get('f2_param', 10.0)
    rsi_buy_max      = current_params.get('rsi_buy_max', 40)
    rsi_sell_min     = current_params.get('rsi_sell_min', 60)

    if win_rate >= breakeven:
        return

    gap = round(breakeven - win_rate, 4)
    log('rule_winrate_below_breakeven: gap=' + str(gap))

    # F1モメンタムパラメータ強化（ルックバック延長）
    if filter_type in ('F1', 'F1andF2', 'F2andF1', 'F2orF1'):
        cands = [f1_param + 2, f1_param + 5, f1_param + 10]
        suggestions.append({
            'param':      'f1_param',
            'current':    f1_param,
            'candidates': cands,
            'reason':     ('win_rate=' + str(round(win_rate, 3)) +
                           ' < breakeven=' + str(round(breakeven, 3)) +
                           ' (gap=' + str(gap) + ') → F1モメンタム強化'),
            'priority':   2,
        })

    # F2乖離閾値強化（閾値を上げて高乖離時のみ通過）
    if filter_type in ('F2', 'F1andF2', 'F2andF1', 'F2orF1', 'F3orF2'):
        cands = [round(f2_param * 1.25, 1), round(f2_param * 1.5, 1), round(f2_param * 2.0, 1)]
        suggestions.append({
            'param':      'f2_param',
            'current':    f2_param,
            'candidates': cands,
            'reason':     ('win_rate=' + str(round(win_rate, 3)) +
                           ' < breakeven=' + str(round(breakeven, 3)) +
                           ' → F2乖離閾値強化'),
            'priority':   2,
        })

    # RSI閾値強化（より過熱域のみ通過）
    suggestions.append({
        'param':      'rsi_buy_max',
        'current':    rsi_buy_max,
        'candidates': [max(20, rsi_buy_max - 10), max(20, rsi_buy_max - 5)],
        'reason':     ('win_rate < breakeven → RSI buy_max引き下げで売られすぎ条件強化'),
        'priority':   2,
    })
    suggestions.append({
        'param':      'rsi_sell_min',
        'current':    rsi_sell_min,
        'candidates': [min(80, rsi_sell_min + 5), min(80, rsi_sell_min + 10)],
        'reason':     ('win_rate < breakeven → RSI sell_min引き上げで買われすぎ条件強化'),
        'priority':   2,
    })


def rule_tp_reach_too_low(metrics, suggestions):
    """tp_reach_rate < 0.15 → tp_dist縮小候補"""
    tp_reach    = metrics.get('tp_reach_rate', 0.2)
    current_tp  = metrics.get('current_params', {}).get('tp_multiplier', 1.0)
    if tp_reach >= 0.15:
        return
    candidates = [round(current_tp * r, 2) for r in (0.6, 0.75, 0.9)]
    candidates = [c for c in candidates if c < current_tp]
    if not candidates:
        return
    suggestions.append({
        'param':      'tp_multiplier',
        'current':    current_tp,
        'candidates': candidates,
        'reason':     'tp_reach_rate=' + str(round(tp_reach, 3)) + ' < 0.15 → TP距離縮小でTP到達率改善',
        'priority':   2,
    })
    log('rule_tp_reach_too_low: tp_reach=' + str(round(tp_reach, 3)))


def rule_good_pf_lot_scale(metrics, suggestions):
    """pf > 1.2 かつ max_dd < 0.15 → lot段階的拡大候補"""
    pf          = metrics.get('pf', 1.0)
    max_dd      = metrics.get('max_dd', 1.0)
    current_lot = metrics.get('current_params', {}).get('lot', 0.1)
    if pf <= 1.2 or max_dd >= 0.15:
        return
    candidates = [round(current_lot * m, 2) for m in (1.25, 1.5, 2.0)]
    suggestions.append({
        'param':      'lot',
        'current':    current_lot,
        'candidates': candidates,
        'reason':     ('pf=' + str(round(pf, 3)) + ' > 1.2 かつ max_dd=' +
                       str(round(max_dd, 3)) + ' < 0.15 → lot拡大で利益最大化'),
        'priority':   3,
    })
    log('rule_good_pf_lot_scale: pf=' + str(round(pf, 3)) + ' dd=' + str(round(max_dd, 3)))


def rule_htf_sigma_tighten(metrics, suggestions):
    """pf < 1.0 かつ win_rate < 0.55 → HTFレンジフィルター強化候補"""
    pf       = metrics.get('pf', 1.0)
    win_rate = metrics.get('win_rate', 0.5)
    current  = metrics.get('current_params', {}).get('htf_range_sigma', 1.0)
    if pf >= 1.0 or win_rate >= 0.55:
        return
    candidates = [round(max(0.3, current - 0.3), 2),
                  round(max(0.3, current - 0.5), 2)]
    candidates = sorted(set(c for c in candidates if c < current))
    if not candidates:
        return
    suggestions.append({
        'param':      'htf_range_sigma',
        'current':    current,
        'candidates': candidates,
        'reason':     ('pf=' + str(round(pf, 3)) + ' < 1.0, win_rate=' +
                       str(round(win_rate, 3)) + ' < 0.55 → HTFフィルター強化でレンジ精度向上'),
        'priority':   2,
    })
    log('rule_htf_sigma_tighten: pf=' + str(round(pf, 3)))


def rule_conflict_tp_rr(suggestions):
    """
    tp_multiplier候補に拡張・縮小両方ある場合、優先度の高い方を残す
    （rr_too_low と tp_reach_too_low が同時発火した場合の整合性担保）
    """
    tp_entries = [s for s in suggestions if s['param'] == 'tp_multiplier']
    if len(tp_entries) <= 1:
        return
    # priorityが最も低い数値（高優先度）を残す
    best = min(tp_entries, key=lambda x: x['priority'])
    for e in tp_entries:
        if e is not best:
            suggestions.remove(e)
            log('rule_conflict_tp_rr: 競合除去 candidates=' + str(e['candidates']))


# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════

def main():
    log('suggest.py 開始')

    try:
        metrics = load_metrics()
    except FileNotFoundError as e:
        log('ERROR: ' + str(e))
        sys.exit(1)

    log('metrics読込: pf=' + str(round(metrics.get('pf', 0), 3)) +
        ' win_rate=' + str(round(metrics.get('win_rate', 0), 3)) +
        ' rr_actual=' + str(round(metrics.get('rr_actual', 0), 3)) +
        ' tp_reach=' + str(round(metrics.get('tp_reach_rate', 0), 3)) +
        ' max_dd=' + str(round(metrics.get('max_dd', 0), 3)))

    suggestions = []

    rule_rr_too_low(metrics, suggestions)
    rule_winrate_below_breakeven(metrics, suggestions)
    rule_tp_reach_too_low(metrics, suggestions)
    rule_good_pf_lot_scale(metrics, suggestions)
    rule_htf_sigma_tighten(metrics, suggestions)

    # 競合解決
    rule_conflict_tp_rr(suggestions)

    # priorityでソート
    suggestions.sort(key=lambda x: x.get('priority', 9))

    if not suggestions:
        log('候補なし: 現状パラメータが全条件を満たしています')
        suggestions.append({
            'param':      'none',
            'current':    None,
            'candidates': [],
            'reason':     '全評価基準クリア済み。変更不要。',
            'priority':   9,
        })

    save_suggestions(suggestions)
    log('suggest.py 完了: ' + str(len(suggestions)) + '件の候補を生成')

    for s in suggestions:
        log('  [P' + str(s.get('priority', '?')) + '] ' + s['param'] +
            ' current=' + str(s['current']) +
            ' candidates=' + str(s['candidates']))


if __name__ == '__main__':
    main()
