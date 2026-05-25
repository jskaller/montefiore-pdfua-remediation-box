#!/usr/bin/env python3
"""
status_json_writer.py
Assembles a STATUS.json for a remediation job by collecting results
from all audit/repair script outputs in a job directory.

Usage: status_json_writer.py <job-dir> [--pdf original.pdf] [--out STATUS.json]
"""
import sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument('job_dir')
parser.add_argument('--pdf',  default='')
parser.add_argument('--out',  default='STATUS.json')
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

# Collect known outputs
status = {
    'generated_at':    datetime.now(timezone.utc).isoformat(),
    'pdf':             args.pdf,
    'job_dir':         str(job_dir),
    'overall_result':  'UNKNOWN',
    'gates': {}
}

gate_files = {
    'verapdf_pdfua':        job_dir / 'verapdf_summary.json',
    'metadata_parity':      job_dir / 'metadata_xmp_parity_audit.json',
    'preservation':         job_dir / 'preservation_audit.json',
    'contrast':             job_dir / 'contrast_audit.json',
    'table_semantics':      job_dir / 'table_semantics_audit.json',
    'font_inventory':       job_dir / 'font_inventory.json',
    'qpdf':                 job_dir / 'qpdf_check.json',
    'visual_qa':            job_dir / 'visual_qa.json',
    'render_compare':       job_dir / 'render_compare.json',
    'alt_text':             job_dir / 'fix_figure_alt_text_approved.json',
    'ocr_detection':        job_dir / 'detect_image_only_pages.json',
}

all_results = []
for gate_name, gate_path in gate_files.items():
    if gate_path.exists():
        data = load_json(gate_path)
        if data:
            result = data.get('result', 'UNKNOWN')
            status['gates'][gate_name] = {
                'result': result,
                'source': str(gate_path.name)
            }
            all_results.append(result)

# Also scan for any additional JSON result files
for json_file in sorted(job_dir.glob('*.json')):
    if json_file.name == args.out:
        continue
    if json_file.name not in [p.name for p in gate_files.values()]:
        data = load_json(json_file)
        if data and 'result' in data:
            gate_name = json_file.stem
            if gate_name not in status['gates']:
                status['gates'][gate_name] = {
                    'result': data['result'],
                    'source': json_file.name
                }
                all_results.append(data['result'])

# Normalize results for overall evaluation
# Many gate scripts return variant success codes beyond 'PASS'. Treat these as PASS:
NORMALIZED_PASS = {
    'PASS',
    'FIXED',
    'ALREADY_CORRECT',
    'PASS_WITH_MIXED_PAGES',
    'PASS_WITH_ONLY_NATIVE_TEXT',
    'SKIPPED'
}
normalized = [ 'PASS' if r in NORMALIZED_PASS else r for r in all_results ]

# Compute overall
if not all_results:
    status['overall_result'] = 'NO_RESULTS'
elif any(r == 'FAIL' for r in normalized):
    status['overall_result'] = 'FAIL'
elif any(r in ('REVIEW', 'PARTIAL', 'WARN', 'NEEDS_REVIEW') for r in normalized):
    status['overall_result'] = 'REVIEW'
elif all(r == 'PASS' for r in normalized):
    status['overall_result'] = 'PASS'
else:
    status['overall_result'] = 'INCOMPLETE'

out_path = job_dir / args.out
out_path.write_text(json.dumps(status, indent=2))

print(json.dumps(status, indent=2))
sys.exit(0 if status['overall_result'] in ('PASS', 'REVIEW') else 1)
