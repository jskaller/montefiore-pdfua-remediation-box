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

Usage:
  status_json_writer.py <job-dir> [--pdf original.pdf] [--out STATUS.json]

Exit codes:
  0  PASS or REVIEW
  1  FAIL, INCOMPLETE, or NO_RESULTS
  2  error
"""
import sys, json, argparse
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

# ── Known gate files — check both root and subdirectories ────────────────────
# The agent may write JSON files to the root job_dir or to subdirectories
# depending on how scripts are called. We check both locations.

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

all_results = []
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
scan_dirs = [job_dir, job_dir / 'audit', job_dir / 'repair',
             job_dir / 'qa', job_dir / 'reports']

for scan_dir in scan_dirs:
    if not scan_dir.exists():
        continue
    for json_file in sorted(scan_dir.glob('*.json')):
        if json_file.name == args.out:
            continue
        if json_file.name in known_sources:
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
# NOTE: verapdf_pdfua is the post-repair final veraPDF result and must NOT
# be excluded — it is the authoritative compliance gate.
EXCLUDE_FROM_OVERALL = {
    'verapdf_baseline', 'parse_summary', 'repair_plan',
    'failures',         # pre-repair failure list — informational only
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

if not final_results:
    status['overall_result'] = 'NO_RESULTS'
elif any(r == 'FAIL' for r in normalized):
    status['overall_result'] = 'FAIL'
elif any(r in ('REVIEW', 'PARTIAL', 'WARN', 'NEEDS_REVIEW') for r in normalized):
    status['overall_result'] = 'REVIEW_REQUIRED'
elif all(r == 'PASS' for r in normalized):
    status['overall_result'] = 'PASS'
else:
    status['overall_result'] = 'INCOMPLETE'

out_path = job_dir / args.out
out_path.write_text(json.dumps(status, indent=2))

print(json.dumps(status, indent=2))
sys.exit(0 if status['overall_result'] in ('PASS', 'REVIEW_REQUIRED') else 1)
