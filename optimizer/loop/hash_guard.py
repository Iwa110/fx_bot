"""hash_guard.py - read-only protection for the frozen core BT engine.

Verifies optimizer/grid_floatstop_bt.py, optimizer/test_grid_floatstop_static.py
and optimizer/grid_floatstop_static_baseline.json against the SHA-256 values
frozen in optimizer/loop/protected_hashes.json (design doc sec 7.1/7.5).

evaluate_candidate.py must call verify() before running any backtest. On
mismatch it raises HashMismatchError; the caller should abort (SystemExit),
never patch around it. If an engine change is genuinely needed, that is a
human-only action: re-run test_grid_floatstop_static.py --freeze, update
grid_floatstop_static_baseline.json, then rewrite protected_hashes.json in
the same commit as the engine change.
"""

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HASHES_PATH = Path(__file__).resolve().parent / 'protected_hashes.json'


class HashMismatchError(RuntimeError):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(hashes_path=None, repo_root=None):
    """Raise HashMismatchError if any protected file has drifted or is missing.

    Returns the frozen hash dict on success (for logging in the ledger).
    """
    hashes_path = Path(hashes_path) if hashes_path else HASHES_PATH
    repo_root = Path(repo_root) if repo_root else REPO_ROOT

    with open(hashes_path, 'r') as f:
        frozen = json.load(f)

    expected = frozen['sha256']
    mismatches = []
    for rel_path, expected_hash in expected.items():
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            mismatches.append(f'{rel_path}: MISSING')
            continue
        actual_hash = _sha256(abs_path)
        if actual_hash != expected_hash:
            mismatches.append(f'{rel_path}: expected {expected_hash} got {actual_hash}')

    if mismatches:
        detail = '\n'.join(mismatches)
        raise HashMismatchError(
            'Protected core-BT files have drifted from the frozen baseline.\n'
            f'{detail}\n'
            'This is a read-only guard (design sec 7.1/7.5): if the engine change '
            'is intentional, re-run optimizer/test_grid_floatstop_static.py --freeze, '
            'update grid_floatstop_static_baseline.json, and rewrite '
            'optimizer/loop/protected_hashes.json in the same commit. '
            'evaluate_candidate.py refuses to run against unverified engine code.'
        )
    return expected


if __name__ == '__main__':
    verify()
    print('OK: protected core-BT files match the frozen hashes.')
