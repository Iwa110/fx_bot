"""ledger.py - append-only JSONL store for the Grid AI-loop experiment ledger.

Design doc sec 7.1: "全仮説・パラメータ・結果・Close理由を構造化記録。
'墓場'のデータ化であり、ループの状態の中核。CLAUDE.md=方針、台帳=事実。"

The ledger file itself (ledger.jsonl) is NOT protected/frozen - it is the
loop's live state and grows with every explore/confirm/card run.

Record schema (one JSON object per line): see grid_loop_engineering_design.md
sec "台帳スキーマ" / the Phase 0 session prompt for the field list. This
module does not enforce a rigid schema beyond the handful of fields it reads
(hypothesis_id, family_tag, pair, status, created_at) - evaluate_candidate.py
owns record construction.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent / 'ledger.jsonl'


def _json_default(o):
    """Records carry numpy scalars straight out of pandas/grid_floatstop_bt
    (pf/pnl values, boolean comparisons on them, etc.) - json.dumps chokes on
    numpy.bool_/integer/floating/ndarray, so normalize them to native types
    at the one place every record passes through instead of chasing down
    every comparison site."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f'Object of type {type(o).__name__} is not JSON serializable')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def current_month():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def load_all(ledger_path=None):
    ledger_path = Path(ledger_path) if ledger_path else DEFAULT_LEDGER_PATH
    if not ledger_path.exists():
        return []
    records = []
    with open(ledger_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_record(record, ledger_path=None):
    """Appends one record as a JSON line. Does not mutate or reorder prior
    lines - the ledger is append-only; updates to a hypothesis (e.g. status
    transitions) are new lines carrying the same hypothesis_id, and readers
    (get_latest_by_id) take the last line for a given id as current state."""
    ledger_path = Path(ledger_path) if ledger_path else DEFAULT_LEDGER_PATH
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=_json_default) + '\n')
    return record


def get_latest_by_id(hypothesis_id, ledger_path=None):
    latest = None
    for rec in load_all(ledger_path):
        if rec.get('hypothesis_id') == hypothesis_id:
            latest = rec
    return latest


def get_latest_per_id(ledger_path=None):
    """Returns {hypothesis_id: latest_record} collapsing the append-only log
    to current state per hypothesis."""
    latest = {}
    for rec in load_all(ledger_path):
        hid = rec.get('hypothesis_id')
        if hid:
            latest[hid] = rec
    return latest


def get_family_records(family_tag, ledger_path=None):
    """All records (latest state per hypothesis) belonging to a family_tag,
    for plateau-representative selection within `explore`."""
    return [r for r in get_latest_per_id(ledger_path).values() if r.get('family_tag') == family_tag]


def next_hypothesis_id(ledger_path=None):
    existing = get_latest_per_id(ledger_path)
    n = 0
    for hid in existing:
        if hid.startswith('H') and hid[1:].isdigit():
            n = max(n, int(hid[1:]))
    return f'H{n + 1:04d}'


def oos_budget_used(month, ledger_path=None):
    """Counts distinct hypotheses that consumed an OOS evaluation (i.e. ran
    `confirm`) in the given YYYY-MM month, for gate4's monthly cap."""
    used = 0
    for rec in get_latest_per_id(ledger_path).values():
        consumed_at = rec.get('oos_consumed_at')
        if consumed_at and consumed_at.startswith(month):
            used += 1
    return used


def get_closed_family_tags(ledger_path=None):
    """Family tags the ledger itself has already closed (in addition to the
    static optimizer/loop/graveyard.json seed list)."""
    tags = set()
    for rec in get_latest_per_id(ledger_path).values():
        if rec.get('status') == 'closed' and rec.get('family_tag'):
            tags.add(rec['family_tag'])
    return tags
