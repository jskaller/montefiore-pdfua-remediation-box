#!/usr/bin/env python3
"""
status_json_writer.py
Assembles a STATUS.json for a remediation job by collecting results
from all audit/repair script outputs in a job directory.

The job directory has the following structure:
  jobs/{TICKET}_{basename}/
    audit/      ← audit JSONs (veraPDF, metadata, contrast, etc.)
    repair/     ← repair JSONs (fix_* outputs)
    qa/         ← QA JSONs (preservation, render_compare, visual_qa)
    reports/    ← alt text drafts, review HTML, alt maps

Reads orchestrator sidecars from audit/ when present:
  - orchestrator_outcome.json       authoritative overall_result from remediate.py
  - openclaw_signals.json           agent-intervention signals
  - strategy_attempts.json          per-rule attempt history
  - proposed_taxonomy_additions.json doc taxonomy proposals
  - doc_tags.json                    assigned doc tags

Overall result determination, in priority order:
  1. orchestrator_outcome.json's overall_result (if present) — authoritative.
     The orchestrator computes this with full knowledge of iteration state,
     critical gate failures, and escalation conditions. status_json_writer
     does not re-adjudicate when this is present.
  2. Derived from gates + openclaw signals (fallback when sidecar missing):
     - FAIL if any non-pre gate failed
     - ESCALATION if any openclaw_signal has escalation-tier reason
     - REVIEW_REQUIRED if any review-tier signal or other openclaw_signal
     - PASS if everything else is PASS-equivalent
     - INCOMPLETE if results are mixed in ways above don't capture
     - NO_RESULTS if no gate produced any signal

Usage:
  status_json_writer.py <job-dir> [--pdf original.pdf] [--out STATUS.json]

Exit codes:
  0  PASS or REVIEW_REQUIRED
  1  FAIL, ESCALATION, INCOMPLETE, or NO_RESULTS
  2  error
"""
import sys, json, argparse, re
from pathlib import Path
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument('job_dir')
parser.add_argument('--pdf',  default='', help='Source PDF path for reference')
parser.add_argument('--out',  default='STATUS.json', help='Output filename (default: STATUS.json)')
args = parser.parse_args()

job_dir = Path(args.job_dir)
if not job_dir.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'Job dir not found: {job_dir}'}))
    sys.exit(2)

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None

status = {
    'generated_at':   datetime.now(timezone.utc).isoformat(),
    'pdf':            args.pdf,
    'job_dir':        str(job_dir),
    'overall_result': 'UNKNOWN',
    'gates':          {}
}

# ── Read orchestrator sidecars ───────────────────────────────────────────────

audit_dir = job_dir / 'audit'

openclaw_signals = load_json(audit_dir / 'openclaw_signals.json') or []
strategy_attempts = load_json(audit_dir / 'strategy_attempts.json') or {}
proposed_taxonomy_additions = load_json(audit_dir / 'proposed_taxonomy_additions.json') or []
doc_tags = load_json(audit_dir / 'doc_tags.json') or []
orchestrator_outcome = load_json(audit_dir / 'orchestrator_outcome.json')

if openclaw_signals:
    status['openclaw_signals'] = openclaw_signals
if strategy_attempts:
    status['strategy_attempts'] = strategy_attempts
if proposed_taxonomy_additions:
    status['proposed_taxonomy_additions'] = proposed_taxonomy_additions
if doc_tags:
    status['doc_tags'] = doc_tags

# ── Known gate files — check both root and subdirectories ────────────────────

def find_file(job_dir, *candidates):
    """Find first existing file from a list of candidate paths."""
    for c in candidates:
        p = Path(c) if Path(c).is_absolute() else job_dir / c
        if p.exists():
            return p
    return None

gate_files = {
    'verapdf_pdfua':   find_file(job_dir, 'audit/verapdf_summary.json',        'verapdf_summary.json'),
    'metadata_parity': find_file(job_dir, 'audit/metadata_parity_final.json',   'audit/metadata_xmp_parity_audit.json', 'metadata_xmp_parity_audit.json'),
    'preservation':    find_file(job_dir, 'qa/preservation_audit.json',          'preservation_audit.json'),
    'contrast':        find_file(job_dir, 'audit/contrast_final.json',           'audit/contrast_audit.json', 'contrast_audit.json'),
    'table_semantics': find_file(job_dir, 'audit/table_semantics_final.json',    'audit/table_semantics_audit.json', 'table_semantics_audit.json'),
    'font_inventory':  find_file(job_dir, 'audit/font_inventory.json',           'font_inventory.json'),
    'qpdf':            find_file(job_dir, 'audit/qpdf_check.json',               'qpdf_check.json'),
    'visual_qa':       find_file(job_dir, 'qa/visual_qa.json',                   'visual_qa.json'),
    'render_compare':  find_file(job_dir, 'qa/render_compare.json',              'render_compare.json'),
    'alt_text':        find_file(job_dir, 'repair/fix_figure_alt_text.json',     'repair/fix_figure_alt_text_approved.json', 'fix_figure_alt_text_approved.json'),
    'ocr_detection':   find_file(job_dir, 'audit/detect_image_only_pages.json',  'detect_image_only_pages.json'),
    'repair_plan':     find_file(job_dir, 'audit/repair_plan.json',              'repair_plan.json'),
    'parse_summary':   find_file(job_dir, 'audit/failures.json',                 'audit/parse_summary.json'),
}

all_results = []  # collected for legacy fallback path; primary outcome comes from sidecar
for gate_name, gate_path in gate_files.items():
    if gate_path and gate_path.exists():
        data = load_json(gate_path)
        if data:
            result = data.get('result', 'UNKNOWN')
            status['gates'][gate_name] = {
                'result': result,
                'source': str(gate_path.relative_to(job_dir))
            }
            all_results.append(result)

# ── Scan all subdirectories for additional JSON result files ──────────────────

known_sources = {v.name for v in gate_files.values() if v}
# Don't treat sidecars as gates
sidecar_names = {
    'openclaw_signals.json',
    'strategy_attempts.json',
    'proposed_taxonomy_additions.json',
    'doc_tags.json',
    'orchestrator_outcome.json',
}
scan_dirs = [job_dir, audit_dir, job_dir / 'repair',
             job_dir / 'qa', job_dir / 'reports']

# Exclude iteration-numbered intermediate files (e.g. preservation_iter3.json).
ITER_PATTERN = re.compile(r'_iter\d+', re.IGNORECASE)

for scan_dir in scan_dirs:
    if not scan_dir.exists():
        continue
    for json_file in sorted(scan_dir.glob('*.json')):
        if json_file.name == args.out:
            continue
        if json_file.name in known_sources:
            continue
        if json_file.name in sidecar_names:
            continue
        if ITER_PATTERN.search(json_file.name):
            continue
        data = load_json(json_file)
        if data and 'result' in data:
            gate_name = json_file.stem
            if gate_name not in status['gates']:
                result = data.get('result', 'UNKNOWN')
                status['gates'][gate_name] = {
                    'result': result,
                    'source': str(json_file.relative_to(job_dir))
                }
                all_results.append(result)

# ── Normalize results ─────────────────────────────────────────────────────────

NORMALIZED_PASS = {
    'PASS', 'FIXED', 'ALREADY_CORRECT',
    'PASS_WITH_MIXED_PAGES', 'PASS_WITH_ONLY_NATIVE_TEXT',
    'SKIPPED', 'OK', 'PLAN_READY', 'NO_FAILURES'
}

# Exclude pre-repair baseline gates from overall result.
# Keys ending in _pre are expected to fail — that's why we run repairs.
# Also exclude informational-only gates that don't affect compliance verdict.
EXCLUDE_FROM_OVERALL = {
    'verapdf_baseline', 'parse_summary', 'repair_plan',
    'verapdf_pdfua',    # baseline pre-repair veraPDF — use verapdf_post instead
    'failures',         # pre-repair failure list — informational only
    'failures_post',    # already reflected in verapdf_post
    'detect_image_only_pages',  # pre-flight only
}

# Escalation-tier signals — if any openclaw_signal carries one of these reasons,
# the job result is ESCALATION regardless of gate outcomes.
ESCALATION_REASONS = {
    'per_rule_cap_reached',
    'job_hard_cap_reached',
    'all_strategies_exhausted',
}

# Review-tier signals — if openclaw_signal present but not escalation-tier,
# the job needs human review (e.g. manual_no_strategies, unknown_rule).
REVIEW_REASONS = {
    'manual_no_strategies',
    'unknown_rule',
}

final_results = []
for gate_name, gate_info in status.get('gates', {}).items():
    if gate_name.endswith('_pre'):
        continue
    if gate_name in EXCLUDE_FROM_OVERALL:
        continue
    r = gate_info.get('result', 'UNKNOWN') if isinstance(gate_info, dict) else gate_info
    final_results.append(r)

normalized = ['PASS' if r in NORMALIZED_PASS else r for r in final_results]

# Analyze openclaw signals
has_escalation_signal = any(
    s.get('reason') in ESCALATION_REASONS for s in openclaw_signals
)
has_review_signal = any(
    s.get('reason') in REVIEW_REASONS for s in openclaw_signals
)

# If the orchestrator wrote an authoritative outcome sidecar, use it directly.
# This prevents this writer and the orchestrator from disagreeing when both
# would compute the same answer in normal cases — and prevents drift in edge
# cases (e.g. ESCALATION vs FAIL distinction).
if orchestrator_outcome and orchestrator_outcome.get('overall_result'):
    status['overall_result']      = orchestrator_outcome['overall_result']
    status['outcome_source']      = 'orchestrator'
    status['orchestrator_outcome'] = orchestrator_outcome
elif not final_results:
    status['overall_result'] = 'NO_RESULTS'
    status['outcome_source'] = 'derived'
elif any(r == 'FAIL' for r in normalized):
    status['overall_result'] = 'FAIL'
    status['outcome_source'] = 'derived'
elif has_escalation_signal:
    status['overall_result'] = 'ESCALATION'
    status['outcome_source'] = 'derived'
elif has_review_signal:
    status['overall_result'] = 'REVIEW_REQUIRED'
    status['outcome_source'] = 'derived'
elif any(r in ('REVIEW', 'PARTIAL', 'WARN', 'NEEDS_REVIEW') for r in normalized):
    status['overall_result'] = 'REVIEW_REQUIRED'
    status['outcome_source'] = 'derived'
elif all(r == 'PASS' for r in normalized):
    status['overall_result'] = 'PASS'
    status['outcome_source'] = 'derived'
else:
    status['overall_result'] = 'INCOMPLETE'
    status['outcome_source'] = 'derived'

out_path = job_dir / args.out
out_path.write_text(json.dumps(status, indent=2))

print(json.dumps(status, indent=2))
sys.exit(0 if status['overall_result'] in ('PASS', 'REVIEW_REQUIRED') else 1)
