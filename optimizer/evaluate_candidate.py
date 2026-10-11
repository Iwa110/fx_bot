"""evaluate_candidate.py - weekly entrypoint of the Grid AI-loop.

Input : loop/candidate.yaml (the only file the LLM writes)
Output: loop/state.json (weeks[<YYYY-Www>]), loop/ledger.csv (append),
        loop/ledger_summary.md (regenerated), and on a 6-gate pass
        loop/reviews/<YYYY-Www>_review_pack.md + review_queue/ card.

Flow (one run per ISO week, idempotent):
  1. validate candidate against loop/search_space.yaml (family / params /
     ranges / pairs / sweep size) and the BT-run budget estimate
  2. explore each pair on IS (optimizer/loop explore: plateau representative)
  3. keep only the family-best representative (min plateau variation,
     tie-break IS PF); the others are marked status=not_selected
  4. confirm it (OOS + WFO + MC + 6 gates; consumes 1 monthly OOS slot)
  5. append ledger.csv, regenerate summary, write review pack on pass

The core BT (grid_floatstop_bt.py) is used read-only through the existing
optimizer/loop engine (hash_guard verified on every explore/confirm).
Re-running the same week with the same candidate prints the stored result
and runs no BT; a different candidate in an already-run week is refused.
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

OPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPT_DIR.parent
LOOP_ENGINE_DIR = OPT_DIR / 'loop'
sys.path.insert(0, str(LOOP_ENGINE_DIR))
# the engine shares this file's module name -> load it by path under a distinct name
_spec = importlib.util.spec_from_file_location('loop_engine', LOOP_ENGINE_DIR / 'evaluate_candidate.py')
ENGINE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ENGINE)
import card as C          # noqa: E402
import data_loader as DL  # noqa: E402
import ledger as L        # noqa: E402

LOOP_DIR = REPO_ROOT / 'loop'
STATE_PATH = LOOP_DIR / 'state.json'
SPACE_PATH = LOOP_DIR / 'search_space.yaml'
CANDIDATE_PATH = LOOP_DIR / 'candidate.yaml'
LEDGER_CSV = LOOP_DIR / 'ledger.csv'
SUMMARY_MD = LOOP_DIR / 'ledger_summary.md'
REVIEWS_DIR = LOOP_DIR / 'reviews'

CSV_FIELDS = ['week', 'run_at', 'candidate_hash', 'family_tag', 'pair', 'params', 'hypothesis_id',
              'status', 'is_pf', 'oos_pf', 'wfo_min_pf', 'plateau_variation', 'req_cap_99',
              'close_reason', 'bt_runs', 'elapsed_s']
CONFIRM_FIXED_RUNS = 4  # full + baseline full + IS + OOS (annual folds added per pair)


class CandidateError(ValueError):
    pass


def iso_week(now=None):
    y, w, _ = (now or datetime.now(timezone.utc)).isocalendar()
    return f'{y}-W{w:02d}'


def load_state(path=STATE_PATH):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_state(state, path=STATE_PATH):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write('\n')


def candidate_hash(cand):
    body = {k: v for k, v in cand.items() if k != 'week'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _check_value(name, v, rule):
    if 'list_len' in rule:
        if not isinstance(v, list) or len(v) != rule['list_len']:
            raise CandidateError(f'{name}: expected list of len {rule["list_len"]}, got {v!r}')
        items = v
    else:
        items = [v]
    for x in items:
        if not isinstance(x, (int, float)) or not (rule['min'] <= x <= rule['max']):
            raise CandidateError(f'{name}={v!r} outside [{rule["min"]}, {rule["max"]}]')


def oos_years(pair, data_dir=None):
    df, meta = DL.load_pair(pair, data_dir)
    if meta['sufficient_for_is_oos']:
        _, df_oos = DL.split_is_oos(df)
    else:
        df_oos = df[df.index >= df.index[int(len(df) * 0.6)]]
    return len(set(df_oos.index.year))


def validate(cand, space, week, data_dir=None):
    """Returns the estimated BT-run count; raises CandidateError on any violation."""
    for key in ('week', 'family_tag', 'structural_reason', 'pairs', 'sweep'):
        if key not in cand:
            raise CandidateError(f'missing key: {key}')
    if str(cand['week']) != week:
        raise CandidateError(f'candidate.week={cand["week"]} != current week {week} (stale candidate.yaml)')
    fam = space['families'].get(cand['family_tag'])
    if fam is None:
        raise CandidateError(f'family_tag {cand["family_tag"]!r} not in search_space {list(space["families"])}')
    lim = space['limits']
    pairs = cand['pairs']
    if not pairs or len(pairs) > lim['max_pairs_per_candidate'] or len(set(pairs)) != len(pairs):
        raise CandidateError(f'pairs must be 1..{lim["max_pairs_per_candidate"]} unique items')
    bad = [p for p in pairs if p not in space['pairs']]
    if bad:
        raise CandidateError(f'pairs outside search_space: {bad}')
    sweep = cand['sweep']
    rule = fam['sweep_params'].get(sweep.get('param'))
    if rule is None:
        raise CandidateError(f'sweep.param {sweep.get("param")!r} not allowed for {cand["family_tag"]}')
    values = sweep.get('values') or []
    if not (lim['min_sweep_values'] <= len(values) <= lim['max_sweep_values']):
        raise CandidateError(f'sweep.values must have {lim["min_sweep_values"]}..{lim["max_sweep_values"]} items')
    for v in values:
        _check_value(sweep['param'], v, rule)
    for k, v in (cand.get('fixed') or {}).items():
        if k == sweep['param'] or k not in fam['fixed_params']:
            raise CandidateError(f'fixed param {k!r} not allowed for {cand["family_tag"]}')
        _check_value(k, v, fam['fixed_params'][k])
    est = len(pairs) * len(values) + CONFIRM_FIXED_RUNS + max(oos_years(p, data_dir) for p in pairs)
    if est > lim['max_bt_runs']:
        raise CandidateError(f'estimated BT runs {est} > max_bt_runs {lim["max_bt_runs"]}')
    return est


def plateau_variation(record):
    """min-neighbour plateau variation of an explore representative, recomputed
    from the stored grid (the engine does not persist it)."""
    grid = (record.get('plateau') or {}).get('grid') or []
    labels = [g['label'] for g in grid]
    label = str(list(record['params'].values())[0])
    if label not in labels:
        return None
    i = labels.index(label)
    if i == 0 or i == len(grid) - 1:
        return None
    c, lpf, rpf = grid[i]['pf'], grid[i - 1]['pf'], grid[i + 1]['pf']
    if not c or lpf is None or rpf is None:
        return None
    return max(abs(lpf - c), abs(rpf - c)) / abs(c)


def _ledger_args(ledger_path, data_dir, **kw):
    return SimpleNamespace(ledger=ledger_path, data_dir=data_dir, review_queue_dir=None, **kw)


def _timeout_handler(signum, frame):
    raise TimeoutError('max_minutes exceeded')


def _csv_row(week, run_at, chash, rec, variation, bt_runs, elapsed):
    gates = (rec.get('gate_results') or {}).get('gates', {})
    wfo = gates.get('gate6_wfo', {}).get('wfo_min_pf')
    return {
        'week': week, 'run_at': run_at, 'candidate_hash': chash,
        'family_tag': rec.get('family_tag'), 'pair': rec.get('pair'),
        'params': json.dumps(rec.get('params'), sort_keys=True),
        'hypothesis_id': rec.get('hypothesis_id'), 'status': rec.get('status'),
        'is_pf': (rec.get('is_metrics') or {}).get('pf'),
        'oos_pf': (rec.get('oos_metrics') or {}).get('pf'),
        'wfo_min_pf': wfo,
        'plateau_variation': None if variation is None else round(variation, 4),
        'req_cap_99': (rec.get('mc_metrics') or {}).get('req_cap_99'),
        'close_reason': rec.get('close_reason'), 'bt_runs': bt_runs, 'elapsed_s': round(elapsed, 1),
    }


def append_csv(rows, path=LEDGER_CSV):
    new = not path.exists()
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def write_summary(ledger_path=None, path=SUMMARY_MD):
    month = L.current_month()
    cap = ENGINE.load_json(ENGINE.GATE_CONFIG_PATH)['gate4_family_budget']['oos_evaluations_per_month_max']
    used = L.oos_budget_used(month, ledger_path)
    rows = []
    if LEDGER_CSV.exists():
        with open(LEDGER_CSV, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    lines = [
        '# Grid AI-loop ledger summary', '',
        f'- generated: {L.now_iso()}',
        f'- OOS budget {month}: used {used}/{cap} (remaining {max(cap - used, 0)})',
        f'- closed (family, pair): {sorted(L.get_closed_family_pair_tags(ledger_path))}', '',
        '## Weekly runs (latest 20)', '',
        '| week | status | family | pair | params | IS PF | OOS PF | WFO min | var | reason |',
        '|---|---|---|---|---|---|---|---|---|---|',
    ]
    for r in rows[-20:][::-1]:
        lines.append(f'| {r["week"]} | {r["status"]} | {r["family_tag"]} | {r["pair"]} | {r["params"]} | '
                     f'{r["is_pf"]} | {r["oos_pf"]} | {r["wfo_min_pf"]} | {r["plateau_variation"]} | '
                     f'{r["close_reason"]} |')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_review_pack(week, cand, rec, card_path):
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEWS_DIR / f'{week}_review_pack.md'
    body = [
        f'# Review pack {week}: {rec["family_tag"]} / {rec["pair"]} ({rec["hypothesis_id"]})', '',
        'Human decision: approve / reject / hold (on this PR). vps/ is NOT changed by the loop.', '',
        f'- candidate sweep: {cand["sweep"]}  fixed: {cand.get("fixed")}',
        f'- card: {card_path.relative_to(REPO_ROOT)}', '',
        C.render_card(rec),
    ]
    path.write_text('\n'.join(body) + '\n', encoding='utf-8')
    return path


def evaluate(candidate_path=CANDIDATE_PATH, week=None, ledger_path=None, data_dir=None, force=False,
             state_path=STATE_PATH):
    week = week or iso_week()
    state = load_state(state_path)
    space = yaml.safe_load(SPACE_PATH.read_text(encoding='utf-8'))
    cand = yaml.safe_load(Path(candidate_path).read_text(encoding='utf-8'))
    chash = candidate_hash(cand)

    prev = state['weeks'].get(week)
    if prev and not force:
        if prev.get('candidate_hash') == chash and prev.get('status') != 'running':
            print(f'[IDEMPOTENT] {week} already evaluated: {prev["status"]} ({prev.get("summary")})')
            return prev
        raise SystemExit(f'[REFUSED] {week} already has a run (status={prev.get("status")}, '
                         f'hash={prev.get("candidate_hash")}); one candidate per week. --force to override.')

    try:
        est = validate(cand, space, week, data_dir)
    except CandidateError as e:
        entry = {'candidate_hash': chash, 'status': 'invalid_candidate', 'summary': str(e), 'at': L.now_iso()}
        state['weeks'][week] = entry
        save_state(state, state_path)
        print(f'[INVALID] {e}')
        return entry

    run_at = L.now_iso()
    state['weeks'][week] = {'candidate_hash': chash, 'status': 'running', 'at': run_at, 'bt_estimate': est}
    save_state(state, state_path)

    max_s = int(space['limits']['max_minutes'] * 60)
    t0 = time.time()
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(max_s)
    bt_runs = 0
    rows = []
    try:
        reps = []
        for pair in cand['pairs']:
            spec = {'family_tag': cand['family_tag'], 'pair': pair, 'base': pair,
                    'structural_reason': cand['structural_reason'], 'param': cand['sweep']['param'],
                    'values': cand['sweep']['values'], 'extra_params': cand.get('fixed') or {}}
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as tf:
                json.dump(spec, tf, ensure_ascii=False)
            hid = ENGINE.cmd_explore(_ledger_args(ledger_path, data_dir, spec=tf.name))
            Path(tf.name).unlink(missing_ok=True)
            if hid is None:
                continue  # gate5 reject or no plateau point (no BT / no budget spent)
            bt_runs += len(cand['sweep']['values'])
            rec = L.get_latest_by_id(hid, ledger_path)
            reps.append((plateau_variation(rec), -(rec['is_metrics']['pf'] or 0), hid, rec))

        reps = [r for r in reps if r[0] is not None]
        if not reps:
            status, summary, best = 'no_representative', 'no pair produced a plateau representative', None
        else:
            reps.sort(key=lambda r: (r[0], r[1]))
            best = reps[0]
            for var, _, hid, rec in reps[1:]:
                skipped = {**rec, 'status': 'not_selected', 'close_reason': f'family_best_is_{best[2]}',
                           'created_at': L.now_iso()}
                L.append_record(skipped, ledger_path)
                rows.append(_csv_row(week, run_at, chash, skipped, var, 0, 0))
            if time.time() - t0 > max_s:
                raise TimeoutError('max_minutes exceeded before confirm')
            res = ENGINE.cmd_confirm(_ledger_args(ledger_path, data_dir, hypothesis_id=best[2]))
            bt_runs += CONFIRM_FIXED_RUNS + len((res.get('wfo_metrics') or {}).get('folds', []))
            status = res['status']
            summary = (f'{best[2]} {res["pair"]} {res["params"]} -> {status}'
                       + (f' ({res["close_reason"]})' if res.get('close_reason') else ''))
            rows.append(_csv_row(week, run_at, chash, res, best[0], bt_runs, time.time() - t0))
            if status == 'gate_passed':
                card_path = C.write_card(res)
                pack = write_review_pack(week, cand, res, card_path)
                state['weeks'][week]['review_pack'] = str(pack.relative_to(REPO_ROOT))
    except TimeoutError as e:
        status, summary = 'timeout', str(e)
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)

    state['weeks'][week].update({'status': status, 'summary': summary, 'bt_runs': bt_runs,
                                 'elapsed_s': round(time.time() - t0, 1)})
    save_state(state, state_path)
    if rows:
        append_csv(rows)
    write_summary(ledger_path)
    print(f'[{status.upper()}] {week}: {summary}  bt_runs={bt_runs} elapsed={time.time() - t0:.1f}s')
    return state['weeks'][week]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--candidate', default=str(CANDIDATE_PATH))
    ap.add_argument('--week', default=None, help='override ISO week (YYYY-Www), default = current UTC week')
    ap.add_argument('--ledger', default=None, help='ledger.jsonl path (default optimizer/loop/ledger.jsonl)')
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--force', action='store_true', help='re-run a week that already has a result')
    args = ap.parse_args()
    evaluate(args.candidate, args.week, args.ledger, args.data_dir, args.force)


if __name__ == '__main__':
    main()
