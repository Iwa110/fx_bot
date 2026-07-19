"""test_evaluate_candidate_e2e.py - Phase 0 mechanism + real-data E2E test.

Drives the full explore -> confirm -> card path through the real CLI
(evaluate_candidate.py) against throwaway ledgers/review_queue/data dirs
under a temp dir, so it never touches the committed
optimizer/loop/ledger.jsonl or review_queue/.

Two data scenarios are covered:
  - test_explore_confirm_card_real_data(): uses the repo's real
    data/AUDCAD_1h_dukas.csv (11yr Dukascopy, fetched 2026-07-19 once this
    environment's network policy allowed reaching freeserv.dukascopy.com).
    This exercises the REAL IS(2015-21)/OOS(2022-26)/annual-WFO/MC pipeline
    and asserts the gate verdicts are self-consistent - it does not assert a
    specific pass/fail outcome, since that is a property of the strategy,
    not the harness.
  - test_insufficient_data_fallback(): points --data-dir at a temp dir
    containing only a short 2yr legacy CSV (no _dukas.csv sibling), so
    data_loader.py's real fallback path is exercised in isolation from
    whatever real data does or doesn't exist in the repo, and asserts
    confirm() forces status=insufficient_data regardless of gate outcome.

Usage:
  python optimizer/loop/test_evaluate_candidate_e2e.py
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

LOOP_DIR = Path(__file__).resolve().parent
REPO_ROOT = LOOP_DIR.parent.parent
sys.path.insert(0, str(LOOP_DIR))
sys.path.insert(0, str(LOOP_DIR.parent))

import evaluate_candidate as EC  # noqa: E402
import hash_guard  # noqa: E402
import ledger as L  # noqa: E402


def make_args(ledger, review_queue_dir=None, data_dir=None, **kw):
    ns = argparse.Namespace(ledger=str(ledger), data_dir=str(data_dir) if data_dir else None,
                             review_queue_dir=str(review_queue_dir) if review_queue_dir else None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def write_spec(path, family_tag, values=(0.2, 0.3, 0.4, 0.5, 0.6), pair='AUDCAD'):
    spec = {
        'family_tag': family_tag,
        'pair': pair,
        'base': pair,
        'structural_reason': (
            '浅い含み益を早期に一部確定させ回転率を上げつつ本TPまでの回復力を残す。'
            'テストHで機構検証のためのダミー構造理由。'
        ),
        'param': 'ptp_frac',
        'extra_params': {'ptp_mult': 0.5},
        'values': list(values),
    }
    Path(path).write_text(json.dumps(spec, ensure_ascii=False))
    return path


def test_hash_guard_ok():
    hash_guard.verify()
    print('[PASS] hash_guard.verify() accepts the current (untampered) protected files')


def test_hash_guard_detects_tamper(tmp):
    fake_repo = tmp / 'fake_repo'
    with open(hash_guard.HASHES_PATH, 'r') as f:
        frozen = json.load(f)
    for rel in frozen['sha256']:
        src = hash_guard.REPO_ROOT / rel
        dst = fake_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    tampered = fake_repo / 'optimizer' / 'grid_floatstop_bt.py'
    tampered.write_text(tampered.read_text() + '\n# tampered for test\n')
    try:
        hash_guard.verify(repo_root=fake_repo)
        raise AssertionError('expected HashMismatchError on tampered copy')
    except hash_guard.HashMismatchError:
        print('[PASS] hash_guard.verify() detects a tampered core-BT file')


def test_graveyard_rejects_closed_family(tmp):
    ledger_path = tmp / 'ledger_graveyard.jsonl'
    spec_path = tmp / 'spec_graveyard.json'
    write_spec(spec_path, family_tag='session_gate')  # seeded closed family
    args = make_args(ledger_path, spec=str(spec_path))
    rep = EC.cmd_explore(args)
    assert rep is None, 'graveyard-closed family must not produce a representative'
    assert L.load_all(ledger_path) == [], 'graveyard rejection must not write any ledger record'
    print('[PASS] gate5_graveyard rejects a known-closed family_tag before any BT compute')


def test_explore_confirm_card_real_data(tmp):
    dukas_path = REPO_ROOT / 'data' / 'AUDCAD_1h_dukas.csv'
    if not dukas_path.exists():
        print('[SKIP] data/AUDCAD_1h_dukas.csv not present - skipping real-data test '
              '(run optimizer/fetch_dukascopy_ohlc.py first)')
        return

    ledger_path = tmp / 'ledger_real.jsonl'
    review_queue = tmp / 'review_queue'
    spec_path = tmp / 'spec_real.json'
    write_spec(spec_path, family_tag='gain_partial_tp_e2e_real')

    args = make_args(ledger_path, review_queue_dir=review_queue, spec=str(spec_path))
    rep_hid = EC.cmd_explore(args)
    assert rep_hid is not None, 'expected a plateau representative from a 5-point interior grid'

    records = L.load_all(ledger_path)
    assert len(records) == 5, f'expected 5 grid-point records, got {len(records)}'
    reps = [r for r in records if r['status'] == 'plateau_selected']
    assert len(reps) == 1 and reps[0]['hypothesis_id'] == rep_hid
    print(f'[PASS] explore (real 11yr data): 5 grid points recorded, representative={rep_hid}')

    result = EC.cmd_confirm(make_args(ledger_path, hypothesis_id=rep_hid))
    assert result['status'] in ('gate_passed', 'closed'), (
        f'expected a real gate verdict (gate_passed/closed) on real 11yr data, got {result["status"]}')
    assert result['data_meta']['sufficient_for_is_oos'] is True
    assert result['is_metrics']['n_trades'] > 100, 'IS window should have a real sample size over 6+ years'
    assert result['oos_metrics']['n_trades'] > 50
    assert len(result['wfo_metrics']['folds']) >= 3, 'annual WFO over 2022-2026 should yield multiple folds'
    assert result['mc_metrics']['req_cap_99'] > 0
    assert result['baseline_mc_metrics']['req_cap_99'] > 0
    assert result['oos_consumed_at'] is not None
    print(f'[PASS] confirm (real data): status={result["status"]} '
          f'IS_pf={result["is_metrics"]["pf"]} OOS_pf={result["oos_metrics"]["pf"]} '
          f'wfo_folds={[f["pf"] for f in result["wfo_metrics"]["folds"]]}')

    card_path = EC.cmd_card(make_args(ledger_path, review_queue_dir=review_queue, hypothesis_id=rep_hid))
    text = card_path.read_text()
    assert card_path.exists()
    assert 'Candidate Card' in text and rep_hid in text
    assert '構造的理由' in text and 'Gate verdicts' in text
    assert 'dukas_11yr' in text
    print(f'[PASS] card (real data): rendered to {card_path.relative_to(tmp)}')


def test_insufficient_data_fallback(tmp):
    """Isolates the insufficient-data fallback path from whatever real data
    does or doesn't exist in the repo, by pointing --data-dir at a temp dir
    holding only a short legacy-style CSV (no _dukas.csv sibling)."""
    fake_data_dir = tmp / 'fake_data'
    fake_data_dir.mkdir()
    legacy_src = REPO_ROOT / 'data' / 'AUDCAD_1h.csv'
    if not legacy_src.exists():
        print('[SKIP] data/AUDCAD_1h.csv (legacy 2yr) not present - skipping insufficient-data test')
        return
    shutil.copy(legacy_src, fake_data_dir / 'AUDCAD_1h.csv')

    ledger_path = tmp / 'ledger_insufficient.jsonl'
    spec_path = tmp / 'spec_insufficient.json'
    write_spec(spec_path, family_tag='gain_partial_tp_e2e_insufficient')

    args = make_args(ledger_path, data_dir=fake_data_dir, spec=str(spec_path))
    rep_hid = EC.cmd_explore(args)
    assert rep_hid is not None

    result = EC.cmd_confirm(make_args(ledger_path, data_dir=fake_data_dir, hypothesis_id=rep_hid))
    assert result['status'] == 'insufficient_data', (
        f'expected insufficient_data on a 2yr-only data dir, got {result["status"]}')
    assert result['data_meta']['sufficient_for_is_oos'] is False
    assert result['is_metrics'] is not None and result['oos_metrics'] is not None
    assert result['gate_results']['gates']['gate1_is_oos']['pf_is'] > 0
    print('[PASS] confirm (2yr-only data dir): pipeline ran end-to-end and forced '
          'status=insufficient_data regardless of gate outcome')


def test_oos_budget_cap(tmp):
    ledger_path = tmp / 'ledger_budget.jsonl'
    cap = 4  # must match gate_config.json gate4_family_budget.oos_evaluations_per_month_max
    for i in range(cap):
        spec_path = tmp / f'spec_budget_{i}.json'
        write_spec(spec_path, family_tag=f'gain_partial_tp_budget_{i}', values=(0.2, 0.3, 0.4))
        args = make_args(ledger_path, spec=str(spec_path))
        rep_hid = EC.cmd_explore(args)
        result = EC.cmd_confirm(make_args(ledger_path, hypothesis_id=rep_hid))
        assert result['oos_consumed_at'] is not None

    spec_over = tmp / 'spec_budget_over.json'
    write_spec(spec_over, family_tag='gain_partial_tp_budget_over', values=(0.2, 0.3, 0.4))
    rep_hid = EC.cmd_explore(make_args(ledger_path, spec=str(spec_over)))
    result = EC.cmd_confirm(make_args(ledger_path, hypothesis_id=rep_hid))
    assert result['status'] == 'closed' and result['close_reason'] == 'oos_budget_exhausted'
    print(f'[PASS] gate4_family_budget: {cap+1}th confirm this month is closed '
          'without running the expensive pipeline')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true', help='keep the temp dir for inspection')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='grid_loop_e2e_'))
    try:
        test_hash_guard_ok()
        test_hash_guard_detects_tamper(tmp)
        test_graveyard_rejects_closed_family(tmp)
        test_explore_confirm_card_real_data(tmp)
        test_insufficient_data_fallback(tmp)
        test_oos_budget_cap(tmp)
        print('\nALL PHASE 0 E2E TESTS PASSED')
    finally:
        if args.keep:
            print(f'(kept temp dir: {tmp})')
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
