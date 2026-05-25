#!/usr/bin/env python3
"""
package_scaffold.py
Creates the standard job directory structure for a remediation job.

Creates TWO directories:
  jobs/{TICKET}_{basename}/     — all intermediate work
    audit/                      — audit JSONs, veraPDF XMLs
    repair/                     — intermediate PDFs (pass0, pass1 etc.)
    qa/                         — render compare images, visual QA renders
    reports/                    — alt text review HTML, alt map drafts

  output/{TICKET}_remediated/   — final deliverables only
    review/                     — created only if REVIEW_REQUIRED
    failed/                     — created only if FAIL

Nothing else should be written to output/ except the final remediated PDF
and AUDIT_REPORT.md (plus review/ or failed/ subfolders if needed).

Usage:
  package_scaffold.py <workspace-root> <ticket-id> <source-pdf-basename>

Example:
  package_scaffold.py /app/workspace MM-17893 consent_form

Exit codes:
  0  success
  2  usage error
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone

if len(sys.argv) < 4:
    print('usage: package_scaffold.py <workspace-root> <ticket-id> <source-pdf-basename>',
          file=sys.stderr)
    sys.exit(2)

workspace    = Path(sys.argv[1])
ticket       = sys.argv[2]
basename     = Path(sys.argv[3]).stem  # strip .pdf if present

# Sanitise basename for directory name
safe_basename = basename.replace(' ', '_').replace('/', '_')

job_name     = f'{ticket}_{safe_basename}'
job_dir      = workspace / 'jobs'    / job_name
output_dir   = workspace / 'output'  / f'{ticket}_remediated'

# Create jobs/ subdirectories
job_subdirs = ['audit', 'repair', 'qa', 'reports']
for sub in job_subdirs:
    (job_dir / sub).mkdir(parents=True, exist_ok=True)

# Create output/ directory (no subdirs yet — only created when needed)
output_dir.mkdir(parents=True, exist_ok=True)

# Write STATUS.json stub to jobs/ dir
status = {
    'job_name':      job_name,
    'ticket':        ticket,
    'source_basename': basename,
    'created_at':    datetime.now(timezone.utc).isoformat(),
    'result':        'IN_PROGRESS',
    'gates': {
        'qpdf':              None,
        'ocr_detection':     None,
        'verapdf_pdfua1':    None,
        'verapdf_wcag':      None,
        'metadata_parity':   None,
        'preservation':      None,
        'table_semantics':   None,
        'contrast':          None,
        'alt_text':          None,
        'render_compare':    None,
        'visual_qa':         None,
    }
}
(job_dir / 'STATUS.json').write_text(json.dumps(status, indent=2))

print(json.dumps({
    'result':        'OK',
    'job_name':      job_name,
    'job_dir':       str(job_dir),
    'output_dir':    str(output_dir),
    'job_subdirs':   [str(job_dir / s) for s in job_subdirs],
    'note': (
        f'Intermediate work → {job_dir}\n'
        f'Final deliverables → {output_dir}\n'
        f'Never write intermediate PDFs or audit JSONs to output/.'
    )
}, indent=2))
sys.exit(0)
