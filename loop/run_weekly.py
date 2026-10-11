"""run_weekly.py - weekly driver of the Grid AI-loop.

The LLM's only job is to write loop/candidate.yaml (inside search_space.yaml)
before this runs. Everything else is deterministic:

  1. preconditions (any failure -> SKIP summary line, exit 0, no BT):
     - review pending: any remote loop/* branch not merged into origin/<base>
       (also makes a same-week re-run a no-op until the PR is merged)
     - monthly OOS budget exhausted (optimizer/loop gate4 cap)
     - data stale: newest *_1h_dukas.csv bar of each search-space pair older
       than state.config.max_data_age_days
     - candidate.yaml missing
  2. git checkout -b loop/<YYYY-Www> from origin/<base>
  3. optimizer/evaluate_candidate.py (idempotent per week)
  4. commit loop/ + optimizer/loop/ledger.jsonl + review_queue/, push, open a
     PR (gh CLI -> GitHub REST with GITHUB_TOKEN -> loop/pr_request.json
     fallback for the calling session to open via its own GitHub tools)
  5. last stdout line = 1-line summary for the scheduler's push notification:
     [grid-loop <week>] PASS|FAIL|SKIP|ERROR <detail> | PR: <url|-|pending ...>

Every week's ledger update ships as a PR; the next week waits until it is
merged, so the ledger/budget on <base> is always current.

Usage: python loop/run_weekly.py [--dry-run] [--no-git] [--week YYYY-Www]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

LOOP_DIR = Path(__file__).resolve().parent
REPO_ROOT = LOOP_DIR.parent
sys.path.insert(0, str(REPO_ROOT / 'optimizer'))
import evaluate_candidate as EV  # noqa: E402  (optimizer/evaluate_candidate.py)

REPO_SLUG = 'Iwa110/fx_bot'
PR_REQUEST_PATH = LOOP_DIR / 'pr_request.json'


def git(*args, check=True):
    r = subprocess.run(['git', *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: {r.stderr.strip()}')
    return r.stdout.strip()


def git_retry(*args):
    for i, wait in enumerate([0, 2, 4, 8, 16]):
        time.sleep(wait)
        try:
            return git(*args)
        except RuntimeError:
            if i == 4:
                raise


def summary_line(week, verdict, detail, pr='-'):
    """The one line the scheduler relays as its notification (always printed last)."""
    return f'[grid-loop {week}] {verdict} {detail} | PR: {pr}'


def unmerged_loop_branches(base):
    """Remote loop/* branches not yet merged into origin/<base>. Every week's
    ledger ships via its PR, so an open one blocks the next run (a PR closed
    without merge: delete its branch to unblock)."""
    heads = git('ls-remote', '--heads', 'origin', 'loop/*', check=False).splitlines()
    out = []
    for line in heads:
        sha, ref = line.split()
        branch = ref.replace('refs/heads/', '')
        git('fetch', 'origin', branch, check=False)
        r = subprocess.run(['git', 'merge-base', '--is-ancestor', sha, f'origin/{base}'], cwd=REPO_ROOT)
        if r.returncode != 0:
            out.append(branch)
    return out


def data_age_days(pair):
    path = REPO_ROOT / 'data' / f'{pair}_1h_dukas.csv'
    if not path.exists():
        return None
    with open(path, 'rb') as f:
        f.seek(max(path.stat().st_size - 4096, 0))
        last = f.read().decode(errors='ignore').strip().splitlines()[-1]
    ts = pd.Timestamp(last.split(',')[0], tz='UTC')
    return (pd.Timestamp.now(tz='UTC') - ts).days


def preconditions(state, base, use_git):
    if use_git:
        open_branches = unmerged_loop_branches(base)
        if open_branches:
            return f'review pending (unmerged {open_branches})'
    cap = EV.ENGINE.load_json(EV.ENGINE.GATE_CONFIG_PATH)['gate4_family_budget']['oos_evaluations_per_month_max']
    used = EV.L.oos_budget_used(EV.L.current_month())
    if used >= cap:
        return f'OOS budget exhausted ({used}/{cap})'
    max_age = state['config']['max_data_age_days']
    space = yaml.safe_load(EV.SPACE_PATH.read_text(encoding='utf-8'))
    stale = {p: data_age_days(p) for p in space['pairs']}
    stale = {p: a for p, a in stale.items() if a is None or a > max_age}
    if stale:
        return f'data stale (>{max_age}d): {stale}'
    if not EV.CANDIDATE_PATH.exists():
        return 'candidate.yaml missing'
    return None


def open_pr(branch, base, title, body):
    if subprocess.run(['gh', 'auth', 'status'], capture_output=True).returncode == 0:
        r = subprocess.run(['gh', 'pr', 'create', '--repo', REPO_SLUG, '--base', base, '--head', branch,
                            '--title', title, '--body', body], cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip().splitlines()[-1]
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{REPO_SLUG}/pulls',
            data=json.dumps({'title': title, 'head': branch, 'base': base, 'body': body}).encode(),
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'fx_bot-loop'})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)['html_url']
        except Exception as e:
            print(f'[pr] REST failed: {e}')
    PR_REQUEST_PATH.write_text(json.dumps({'repo': REPO_SLUG, 'head': branch, 'base': base,
                                           'title': title, 'body': body}, ensure_ascii=False, indent=2),
                               encoding='utf-8')
    return None


def run(args, week):
    state = EV.load_state()
    base = state['config']['base_branch']
    use_git = not args.no_git
    if use_git:
        git_retry('fetch', 'origin', base)

    reason = preconditions(state, base, use_git)
    if reason:
        return summary_line(week, 'SKIP', reason)
    if args.dry_run:
        return summary_line(week, 'SKIP', 'dry-run: preconditions OK')

    branch = f'loop/{week}'
    if use_git:
        git('checkout', '-B', branch, f'origin/{base}')

    res = EV.evaluate(week=week)
    status = res.get('status')
    summary = res.get('summary', '')

    pr = '-'
    if use_git:
        git('add', 'loop', 'optimizer/loop/ledger.jsonl', 'review_queue')
        if git('diff', '--cached', '--name-only'):
            git('commit', '-m', f'loop {week}: {status}\n\n{summary}')
            git_retry('push', '-u', 'origin', branch)
            body = (f'Weekly grid-loop run {week}\n\n- status: {status}\n- {summary}\n'
                    + (f'- review pack: {res["review_pack"]}\n\nDecision: approve / reject / hold.\n'
                       if res.get('review_pack') else '\nLedger-only update (no gate pass).\n'))
            pr = (open_pr(branch, base, f'loop {week}: {status}', body)
                  or f'pending ({PR_REQUEST_PATH.relative_to(REPO_ROOT)}, branch {branch})')
    verdict = 'PASS' if status == 'gate_passed' else 'FAIL'
    return summary_line(week, verdict, f'{status}: {summary}', pr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--week', default=None)
    ap.add_argument('--dry-run', action='store_true', help='preconditions only')
    ap.add_argument('--no-git', action='store_true', help='evaluate in the current tree, no branch/push/PR')
    args = ap.parse_args()

    week = args.week or EV.iso_week()
    try:
        line, code = run(args, week), 0
    except BaseException as e:  # incl. SystemExit(REFUSED) from evaluate: still emit the summary line
        traceback.print_exc()
        line, code = summary_line(week, 'ERROR', f'{type(e).__name__}: {e}'.replace('\n', ' ')[:300]), 1
    print(line, flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
