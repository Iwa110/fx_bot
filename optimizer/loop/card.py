"""card.py - renders one ledger record (status=gate_passed) into the single
Markdown candidate card reviewed by the human (design doc sec 7.3).

Card contents (per design): structural reason / IS+OOS PF & decay & n /
plateau table (in lieu of a chart, Phase 0 is text-only) / gate verdicts /
graveyard check result / req_cap change / recommended demo config.

Human review is exactly 3 choices (approve/reject/hold) applied to the PR
this card ships in - this module only produces the file, it does not
implement the review workflow itself.
"""

from pathlib import Path

REVIEW_QUEUE_DIR = Path(__file__).resolve().parent.parent.parent / 'review_queue'


def _gate_line(name, detail):
    mark = 'PASS' if detail.get('pass') else 'FAIL'
    rest = {k: v for k, v in detail.items() if k != 'pass'}
    return f'| {name} | {mark} | {rest} |'


def render_card(record):
    hid = record.get('hypothesis_id', '?')
    pair = record.get('pair', '?')
    family = record.get('family_tag', '?')
    reason = record.get('structural_reason', '')
    params = record.get('params', {})
    base = record.get('base_config', '?')

    is_m = record.get('is_metrics') or {}
    oos_m = record.get('oos_metrics') or {}
    mc_m = record.get('mc_metrics') or {}
    baseline_mc_m = record.get('baseline_mc_metrics') or {}
    gate_res = record.get('gate_results') or {}
    plateau = record.get('plateau') or {}

    lines = []
    lines.append(f'# Candidate Card: {hid} ({pair} / {family})')
    lines.append('')
    lines.append(f'- base_config: `{base}`')
    lines.append(f'- params (delta): `{params}`')
    lines.append(f'- created_at: {record.get("created_at")}')
    lines.append(f'- data source: {record.get("data_meta", {}).get("source", "?")} '
                 f'({record.get("data_meta", {}).get("start", "?")} ~ {record.get("data_meta", {}).get("end", "?")})')
    lines.append('')
    lines.append('## 構造的理由 (structural reason)')
    lines.append(reason or '_(missing)_')
    lines.append('')
    lines.append('## IS / OOS metrics')
    lines.append('| window | PF | net | n_trades | n_years |')
    lines.append('|---|---:|---:|---:|---:|')
    lines.append(f'| IS  | {is_m.get("pf")} | {is_m.get("total_pnl")} | {is_m.get("n_trades")} | {is_m.get("n_years")} |')
    lines.append(f'| OOS | {oos_m.get("pf")} | {oos_m.get("total_pnl")} | {oos_m.get("n_trades")} | {oos_m.get("n_years")} |')
    decay = gate_res.get('gates', {}).get('gate1_is_oos', {}).get('decay')
    lines.append(f'- decay = 1 - PF_OOS/PF_IS = **{decay}**')
    lines.append('')
    lines.append('## WFO (annual folds)')
    wfo = gate_res.get('gates', {}).get('gate6_wfo', {})
    lines.append(f'- folds: {wfo.get("wfo_folds")}')
    lines.append(f'- wfo_min_pf: **{wfo.get("wfo_min_pf")}** (threshold {wfo.get("threshold")})')
    lines.append('')
    lines.append('## Plateau (neighbor +-1 step, IS window)')
    lines.append('| variant | pf |')
    lines.append('|---|---:|')
    for v in plateau.get('grid', []):
        marker = ' <- selected' if v.get('label') == str(list(params.values())[0]) else ''
        lines.append(f'| {v.get("label")}{marker} | {v.get("pf")} |')
    lines.append(f'- max_variation_pct: {gate_res.get("gates", {}).get("gate3_plateau", {}).get("max_variation_pct")}')
    lines.append('')
    lines.append('## Gate verdicts')
    lines.append('| gate | verdict | detail |')
    lines.append('|---|---|---|')
    for gname, gdetail in gate_res.get('gates', {}).items():
        lines.append(_gate_line(gname, gdetail))
    lines.append(f'- **overall: {"PASS" if gate_res.get("pass") else "FAIL"}**')
    lines.append('')
    lines.append('## 墓場照合 (graveyard check)')
    gy = gate_res.get('gates', {}).get('gate5_graveyard', {})
    lines.append(f'- {gy}')
    lines.append('')
    lines.append('## Required capital (req_cap) change')
    lines.append(f'- baseline req_cap_99: {baseline_mc_m.get("req_cap_99", "n/a")}')
    lines.append(f'- candidate req_cap_99: {mc_m.get("req_cap_99", "n/a")}')
    lines.append(f'- p_loss_5yr: {mc_m.get("p_loss_5yr", "n/a")}')
    lines.append('')
    lines.append('## 推奨demo設定 (recommended demo config)')
    demo_cfg = record.get('demo_config') or {}
    lines.append(f'`{demo_cfg}`')
    lines.append('')
    lines.append('---')
    lines.append('Review: approve (PR merge = demo投入) / reject (理由1行) / hold. See design doc sec 7.3.')
    return '\n'.join(lines)


def write_card(record, out_dir=None):
    out_dir = Path(out_dir) if out_dir else REVIEW_QUEUE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    hid = record.get('hypothesis_id', 'H0000')
    pair = record.get('pair', 'UNK')
    family = record.get('family_tag', 'unknown')
    fname = f'{hid}_{pair}_{family}.md'
    path = out_dir / fname
    path.write_text(render_card(record), encoding='utf-8')
    return path
