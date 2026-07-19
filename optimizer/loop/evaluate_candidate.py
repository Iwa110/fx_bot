"""evaluate_candidate.py - the single evaluation entrypoint for the Grid AI-loop
(design doc sec 7.1: "評価は optimizer/loop/evaluate_candidate.py の単一経路のみ").

Subcommands:
  explore  --spec <hypothesis_spec.json>
      Runs a family's parameter grid on the IS window only (cheap), records
      every grid point in the ledger, and marks the plateau-flattest point
      (design doc gate3: "plateau幅最大の代表1件") as the representative that
      is allowed to spend an OOS-evaluation budget slot.

  confirm  --hypothesis-id <id>
      Runs the representative through OOS + annual WFO + MC-required-capital,
      applies all 6 gates, and appends the final gate_passed/closed record.
      Consumes one OOS-budget slot for the hypothesis's family this month
      (design doc gate4) UNLESS the monthly cap is already spent, in which
      case the hypothesis is closed with reason=oos_budget_exhausted without
      running the expensive stages.

  card  --hypothesis-id <id>
      Renders the gate_passed record to review_queue/<id>_<pair>_<family>.md.

Every invocation calls hash_guard.verify() first (read-only protection of
the frozen core BT). Only grid_floatstop_bt.py's public functions
(run_backtest / compute_atr_series / compute_ci_series) are used - this
module never edits or monkeypatches the core engine.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))         # this dir (flat sibling imports)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # optimizer/ (core BT)
import grid_floatstop_bt as G  # noqa: E402  (core BT, read-only)

import card as C          # noqa: E402
import data_loader as DL  # noqa: E402
import gates as GT        # noqa: E402
import hash_guard         # noqa: E402
import ledger as L        # noqa: E402
import mc_capital as MC   # noqa: E402

LOOP_DIR = Path(__file__).resolve().parent
BASELINES_PATH = LOOP_DIR / 'known_baselines.json'
GRAVEYARD_PATH = LOOP_DIR / 'graveyard.json'
GATE_CONFIG_PATH = LOOP_DIR / 'gate_config.json'

MIN_FOLD_DAYS = 60  # exclude degenerate partial-year WFO folds shorter than this


def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)


def build_cfg(base_name, params, baselines):
    base = baselines['baselines'].get(base_name)
    if base is None:
        raise ValueError(f'unknown base_config "{base_name}", known: {list(baselines["baselines"])}')
    cfg = {k: v for k, v in base.items() if k != 'magic'}
    cfg.update(params)
    return cfg


def annual_wfo_folds(pair, cfg, df, atr_series, ci_series, min_fold_days=MIN_FOLD_DAYS):
    """One fold per calendar year present in df. Folds shorter than
    min_fold_days are excluded (degenerate partial years, e.g. a stub Jan)."""
    folds = []
    for year in sorted(set(df.index.year)):
        df_y = df[df.index.year == year]
        if (df_y.index[-1] - df_y.index[0]).days < min_fold_days:
            continue
        res_y = G.run_backtest(pair, cfg, df_y, atr_series, ci_series)
        folds.append({'year': int(year), 'pf': res_y['pf'], 'n_trades': GT.n_trades(res_y)})
    return folds


def run_window(pair, cfg, df_window, atr_series, ci_series):
    if len(df_window) == 0:
        return None
    res = G.run_backtest(pair, cfg, df_window, atr_series, ci_series)
    n_years = max((df_window.index[-1] - df_window.index[0]).days / 365.25, 1e-6)
    return {
        'pf': res['pf'], 'total_pnl': res['total_pnl'],
        'n_trades': GT.n_trades(res), 'n_years': round(n_years, 3),
        '_raw': res,
    }


def cmd_explore(args):
    hash_guard.verify()
    baselines = load_json(BASELINES_PATH)
    graveyard = load_json(GRAVEYARD_PATH)
    gate_cfg = load_json(GATE_CONFIG_PATH)
    spec = load_json(args.spec)

    family_tag = spec['family_tag']
    pair = spec['pair']
    base_name = spec.get('base', pair)
    structural_reason = spec['structural_reason']
    param_name = spec['param']
    values = spec['values']
    extra_params = spec.get('extra_params', {})
    center_index = spec.get('center_index', len(values) // 2)

    # graveyard check up-front - do not spend even the cheap IS compute on a
    # closed family.
    gy_check = GT.gate5_graveyard(family_tag, pair, structural_reason, graveyard, args.ledger, gate_cfg)
    if not gy_check['pass']:
        print(f'[REJECTED at explore] gate5_graveyard: {gy_check}')
        return None

    df, data_meta = DL.load_pair(pair, args.data_dir)
    atr_series = G.compute_atr_series(df)
    ci_series = G.compute_ci_series(df)

    if data_meta['sufficient_for_is_oos']:
        df_is, _ = DL.split_is_oos(df)
        is_label = 'IS_2015_2021'
    else:
        split_at = df.index[int(len(df) * 0.6)]
        df_is = df[df.index < split_at]
        is_label = 'IS_PROXY_60pct(insufficient_data)'

    grid_results = []
    for i, v in enumerate(values):
        params = {param_name: v, **extra_params}
        cfg = build_cfg(base_name, params, baselines)
        res = run_window(pair, cfg, df_is, atr_series, ci_series)
        grid_results.append({'index': i, 'value': v, 'params': params, 'pf': res['pf'] if res else None,
                              'n_trades': res['n_trades'] if res else 0})

    center = grid_results[center_index]
    representative = None
    best_variation = None
    for i, point in enumerate(grid_results):
        if i == 0 or i == len(grid_results) - 1:
            continue  # need both neighbors for a plateau check
        if point['pf'] is None or point['pf'] == 0:
            continue
        left, right = grid_results[i - 1]['pf'], grid_results[i + 1]['pf']
        if left is None or right is None:
            continue
        variation = max(abs(left - point['pf']), abs(right - point['pf'])) / abs(point['pf'])
        if best_variation is None or variation < best_variation:
            best_variation = variation
            representative = {**point, 'neighbor_pfs': [left, right],
                               'neighbors': [grid_results[i - 1]['value'], grid_results[i + 1]['value']],
                               'variation': variation}

    now = L.now_iso()
    hids = []
    for point in grid_results:
        hid = L.next_hypothesis_id(args.ledger)
        is_rep = representative is not None and point['index'] == representative['index']
        record = {
            'hypothesis_id': hid, 'family_tag': family_tag, 'pair': pair,
            'structural_reason': structural_reason, 'base_config': base_name,
            'params': point['params'], 'created_at': now,
            'status': 'plateau_selected' if is_rep else 'candidate',
            'is_metrics': {'pf': point['pf'], 'n_trades': point['n_trades'], 'window': is_label},
            'data_meta': data_meta,
            'plateau': {
                'grid': [{'label': str(p['value']), 'pf': p['pf']} for p in grid_results],
                'is_representative': is_rep,
            } if is_rep else None,
        }
        L.append_record(record, args.ledger)
        hids.append(hid)
        marker = ' <- REPRESENTATIVE (proceeds to confirm)' if is_rep else ''
        print(f'{hid}  {param_name}={point["value"]}  IS_pf={point["pf"]}  n={point["n_trades"]}{marker}')

    if representative is None:
        print('[WARN] no interior grid point qualified as a plateau representative '
              '(need >=3 grid values with valid PF). Nothing eligible for confirm.')
    else:
        rep_hid = hids[representative['index']]
        print(f'\nRepresentative: {rep_hid} (variation={representative["variation"]:.4f}). '
              f'Run: evaluate_candidate.py confirm --hypothesis-id {rep_hid}')
        return rep_hid
    return None


def cmd_confirm(args):
    hash_guard.verify()
    baselines = load_json(BASELINES_PATH)
    graveyard = load_json(GRAVEYARD_PATH)
    gate_cfg = load_json(GATE_CONFIG_PATH)

    record = L.get_latest_by_id(args.hypothesis_id, args.ledger)
    if record is None:
        raise SystemExit(f'no ledger record for hypothesis_id={args.hypothesis_id}')
    if record['status'] != 'plateau_selected':
        raise SystemExit(f'hypothesis {args.hypothesis_id} has status={record["status"]!r}, '
                          f'expected plateau_selected (run explore first, and only confirm the representative)')

    pair = record['pair']
    family_tag = record['family_tag']
    month = L.current_month()

    budget_used = L.oos_budget_used(month, args.ledger)
    budget_cap = gate_cfg['gate4_family_budget']['oos_evaluations_per_month_max']
    if budget_used >= budget_cap:
        closed = {**record, 'status': 'closed', 'close_reason': 'oos_budget_exhausted',
                  'created_at': L.now_iso()}
        L.append_record(closed, args.ledger)
        print(f'[CLOSED] {args.hypothesis_id}: monthly OOS budget exhausted ({budget_used}/{budget_cap})')
        return closed

    base_name = record['base_config']
    cfg = build_cfg(base_name, record['params'], baselines)

    df, data_meta = DL.load_pair(pair, args.data_dir)
    atr_series = G.compute_atr_series(df)
    ci_series = G.compute_ci_series(df)

    baseline_cfg = build_cfg(base_name, {}, baselines)
    full_res = G.run_backtest(pair, cfg, df, atr_series, ci_series)
    baseline_full_res = G.run_backtest(pair, baseline_cfg, df, atr_series, ci_series)

    if data_meta['sufficient_for_is_oos']:
        df_is, df_oos = DL.split_is_oos(df)
        insufficient = False
    else:
        split_at = df.index[int(len(df) * 0.6)]
        df_is, df_oos = df[df.index < split_at], df[df.index >= split_at]
        insufficient = True

    is_m = run_window(pair, cfg, df_is, atr_series, ci_series)
    oos_m = run_window(pair, cfg, df_oos, atr_series, ci_series)
    wfo_folds = annual_wfo_folds(pair, cfg, df_oos, atr_series, ci_series)
    wfo_pf_list = [f['pf'] for f in wfo_folds]

    single_events = list(full_res.get('fs_events', [])) + list(full_res.get('b48_events', []))
    baseline_single_events = list(baseline_full_res.get('fs_events', [])) + list(baseline_full_res.get('b48_events', []))
    mc_m = MC.required_capital(full_res['monthly'], single_events, gate_cfg)
    baseline_mc_m = MC.required_capital(baseline_full_res['monthly'], baseline_single_events, gate_cfg)

    plateau = record.get('plateau') or {}
    grid = plateau.get('grid', [])
    center_pf = is_m['pf']
    # neighbor_pfs/center_pf were already validated at explore time; re-derive
    # them here from the stored grid for the gate3 record on this confirm run.
    values_in_grid = [g['pf'] for g in grid]
    try:
        center_idx = [g['label'] for g in grid].index(str(list(record['params'].values())[0]))
        neighbor_pfs = [p for j, p in enumerate(values_in_grid) if j in (center_idx - 1, center_idx + 1)
                        and 0 <= j < len(values_in_grid)]
    except (ValueError, IndexError):
        neighbor_pfs = []

    gate_results = GT.run_all_gates(
        is_pf=is_m['pf'], oos_pf=oos_m['pf'] if oos_m else None,
        n_is=is_m['n_trades'], n_oos=oos_m['n_trades'] if oos_m else 0,
        n_years_is=is_m['n_years'], n_years_oos=oos_m['n_years'] if oos_m else 1e-6,
        neighbor_pfs=neighbor_pfs, center_pf=center_pf,
        family_tag=family_tag, pair=pair, month=month, ledger_path=args.ledger,
        structural_reason=record['structural_reason'], graveyard=graveyard,
        wfo_pf_list=wfo_pf_list, cfg=gate_cfg,
    )

    final_status = 'gate_passed' if gate_results['pass'] else 'closed'
    if insufficient:
        final_status = 'insufficient_data'

    updated = {
        **record,
        'created_at': L.now_iso(),
        'status': final_status,
        'oos_consumed_at': L.now_iso(),
        'is_metrics': {**is_m, '_raw': None} if is_m else None,
        'oos_metrics': {**oos_m, '_raw': None} if oos_m else None,
        'wfo_metrics': {'folds': wfo_folds},
        'mc_metrics': mc_m,
        'baseline_mc_metrics': baseline_mc_m,
        'gate_results': gate_results,
        'data_meta': data_meta,
        'close_reason': None if gate_results['pass'] else 'gate_failed',
        'demo_config': cfg if gate_results['pass'] and not insufficient else None,
    }
    for k in ('is_metrics', 'oos_metrics'):
        if updated[k] is not None:
            updated[k].pop('_raw', None)

    L.append_record(updated, args.ledger)
    print(f'[{final_status.upper()}] {args.hypothesis_id}  IS_pf={is_m["pf"]}  '
          f'OOS_pf={oos_m["pf"] if oos_m else None}  wfo_min={min(wfo_pf_list) if wfo_pf_list else None}')
    if insufficient:
        print('  NOTE: data_meta.sufficient_for_is_oos=False -> this ran on a 60/40 proxy split of the '
              'available 2yr file, NOT the real IS(2015-21)/OOS(2022-26) windows. Status forced to '
              'insufficient_data regardless of gate outcome; not eligible for demo.')
    return updated


def cmd_card(args):
    record = L.get_latest_by_id(args.hypothesis_id, args.ledger)
    if record is None:
        raise SystemExit(f'no ledger record for hypothesis_id={args.hypothesis_id}')
    path = C.write_card(record, args.review_queue_dir)
    print(f'Card written: {path}')
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ledger', default=None, help='path to ledger.jsonl (default optimizer/loop/ledger.jsonl)')
    ap.add_argument('--data-dir', default=None, help='path to data/ dir (default repo data/)')
    ap.add_argument('--review-queue-dir', default=None, help='path to review_queue/ (default repo review_queue/)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_explore = sub.add_parser('explore', help='IS-only grid sweep + plateau representative selection')
    p_explore.add_argument('--spec', required=True, help='path to hypothesis spec JSON')
    p_explore.set_defaults(func=cmd_explore)

    p_confirm = sub.add_parser('confirm', help='OOS + WFO + MC + gates for the plateau representative')
    p_confirm.add_argument('--hypothesis-id', required=True)
    p_confirm.set_defaults(func=cmd_confirm)

    p_card = sub.add_parser('card', help='render a gate_passed record to review_queue/')
    p_card.add_argument('--hypothesis-id', required=True)
    p_card.set_defaults(func=cmd_card)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
