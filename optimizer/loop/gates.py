"""gates.py - the 6 machine-judged overfitting gates (design doc sec 7.2).

All gate functions are pure: they take metrics dicts / plain values plus the
gate_config dict and return {'pass': bool, ...detail...}. No I/O except
gate4 (family budget) and gate5 (graveyard), which read the ledger /
graveyard registry that are passed in explicitly by the caller - still no
hidden global state.

Gate numbering matches grid_loop_engineering_design.md sec 7.2, with gate6
(wfo_min_pf) added 2026-07-18 per user request (in addition to the original
5).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for flat `import ledger` below


def n_trades(bt_result):
    """Closed-trade count from a grid_floatstop_bt.run_backtest() result dict."""
    return (bt_result.get('n_tp', 0) + bt_result.get('n_ptp', 0)
            + bt_result.get('n_b48', 0) + bt_result.get('n_fstop', 0))


def gate1_is_oos(is_pf, oos_pf, cfg):
    th = cfg['gate1_is_oos']
    if is_pf <= 0 or oos_pf is None:
        return {'pass': False, 'reason': 'pf_is<=0 or pf_oos missing',
                'pf_is': is_pf, 'pf_oos': oos_pf}
    sign_ok = (is_pf >= th['is_pf_min']) and (oos_pf >= th['oos_pf_min'])
    decay = 1.0 - (oos_pf / is_pf) if is_pf > 0 else float('inf')
    decay_ok = decay <= th['decay_max']
    passed = bool(sign_ok and decay_ok)
    return {'pass': passed, 'pf_is': is_pf, 'pf_oos': oos_pf, 'decay': round(decay, 4),
            'sign_ok': sign_ok, 'decay_ok': decay_ok,
            'thresholds': th}


def gate2_sample_size(n_is, n_oos, n_years_is, n_years_oos, cfg):
    th = cfg['gate2_sample_size']
    per_yr_is = n_is / n_years_is if n_years_is > 0 else 0.0
    per_yr_oos = n_oos / n_years_oos if n_years_oos > 0 else 0.0
    passed = (per_yr_is >= th['n_trades_per_year_min']) and (per_yr_oos >= th['n_trades_per_year_min'])
    return {'pass': bool(passed), 'n_per_yr_is': round(per_yr_is, 2),
            'n_per_yr_oos': round(per_yr_oos, 2), 'threshold': th['n_trades_per_year_min']}


def gate3_plateau(neighbor_pfs, center_pf, cfg):
    """neighbor_pfs: PF values of each +-1 step neighbor in the swept param
    grid (IS window). Flags cliff-spikes (grid_ci_optimize.py ci67.5 style)."""
    th = cfg['gate3_plateau']
    if not neighbor_pfs or center_pf is None or center_pf == 0:
        return {'pass': False, 'reason': 'no neighbors or center_pf=0'}
    variations = [abs(p - center_pf) / abs(center_pf) for p in neighbor_pfs]
    max_variation = max(variations)
    sign_flip = any((p <= 0) != (center_pf <= 0) for p in neighbor_pfs)
    passed = (max_variation <= th['neighbor_variation_max_pct']) and not sign_flip
    return {'pass': bool(passed), 'max_variation_pct': round(max_variation, 4),
            'sign_flip': sign_flip, 'threshold': th['neighbor_variation_max_pct']}


def gate4_family_budget(family_tag, month, ledger_path, cfg):
    import ledger as L
    th = cfg['gate4_family_budget']
    used = L.oos_budget_used(month, ledger_path)
    passed = used < th['oos_evaluations_per_month_max']
    return {'pass': bool(passed), 'used_this_month': used,
            'cap': th['oos_evaluations_per_month_max'], 'month': month}


def gate5_graveyard(family_tag, pair, structural_reason, graveyard, ledger_path, cfg):
    """The static graveyard.json registry blocks a family_tag unconditionally
    (human-curated, cross-pair conclusion). The ledger's own closed records
    only block the specific (family_tag, pair) that actually closed, so a
    close on one pair does not pre-empt testing the same family on a
    different pair (2026-07-19, see ledger.get_closed_family_pair_tags)."""
    th = cfg['gate5_graveyard']
    import ledger as L

    reason = structural_reason or ''
    if len(reason) < th['structural_reason_min_len']:
        return {'pass': False, 'reason': 'structural_reason too short/empty',
                'min_len': th['structural_reason_min_len']}

    lowered = reason.lower()
    for pattern in th['banned_reason_patterns']:
        if pattern.lower() in lowered:
            return {'pass': False, 'reason': f'banned pattern matched: "{pattern}"'}

    static_closed_tags = {e['family_tag'] for e in graveyard.get('closed_families', [])}
    if family_tag in static_closed_tags:
        return {'pass': False, 'reason': f'family_tag "{family_tag}" already closed in graveyard.json'}

    if (family_tag, pair) in L.get_closed_family_pair_tags(ledger_path):
        return {'pass': False,
                'reason': f'family_tag "{family_tag}" already closed for pair "{pair}" in ledger'}

    return {'pass': True}


def gate6_wfo(wfo_pf_list, cfg):
    th = cfg['gate6_wfo']
    if not wfo_pf_list:
        return {'pass': False, 'reason': 'no wfo folds'}
    wfo_min = min(wfo_pf_list)
    passed = wfo_min >= th['wfo_min_pf']
    return {'pass': bool(passed), 'wfo_min_pf': round(wfo_min, 4),
            'wfo_folds': [round(p, 4) for p in wfo_pf_list], 'threshold': th['wfo_min_pf']}


def run_all_gates(*, is_pf, oos_pf, n_is, n_oos, n_years_is, n_years_oos,
                   neighbor_pfs, center_pf, family_tag, pair, month, ledger_path,
                   structural_reason, graveyard, wfo_pf_list, cfg):
    """Runs all 6 gates and returns {'pass': bool, 'gates': {...}}. All gates
    must pass for the overall candidate to be gate_passed."""
    gates = {
        'gate1_is_oos': gate1_is_oos(is_pf, oos_pf, cfg),
        'gate2_sample_size': gate2_sample_size(n_is, n_oos, n_years_is, n_years_oos, cfg),
        'gate3_plateau': gate3_plateau(neighbor_pfs, center_pf, cfg),
        'gate4_family_budget': gate4_family_budget(family_tag, month, ledger_path, cfg),
        'gate5_graveyard': gate5_graveyard(family_tag, pair, structural_reason, graveyard, ledger_path, cfg),
        'gate6_wfo': gate6_wfo(wfo_pf_list, cfg),
    }
    overall = all(g['pass'] for g in gates.values())
    return {'pass': bool(overall), 'gates': gates}
