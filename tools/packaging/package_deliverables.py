#!/usr/bin/env python3
"""
package_deliverables.py
Assembles final deliverable package and places exactly two files in the
output directory: the remediated PDF and an AUDIT_REPORT.md.

The full internal package (reports, QA, logs, checksums) is assembled
inside the job directory for reference and archiving.

Usage:
  package_deliverables.py <job-dir> <remediated-pdf> \
    --output-dir <output-dir> \
    [--source-pdf original.pdf] \
    [--skip-pdf]

  <job-dir>       jobs/{TICKET}_{basename}/ — intermediate work directory
  <remediated-pdf> final repaired PDF path
  --output-dir    output/{TICKET}_remediated/ — final deliverables destination
  --source-pdf    original source PDF (for preservation comparison reference)
  --skip-pdf      write audit report only, do not copy PDF to output-dir
                  (used for FAIL/ESCALATION outcomes where no remediated PDF
                  should be handed off)

Output:
  $OUTPUT_DIR/{basename}_remediated.pdf   ← final PDF (unless --skip-pdf)
  $OUTPUT_DIR/{basename}_AUDIT_REPORT.md  ← human-readable audit summary

Exit codes:
  0  success
  2  error
"""
import sys, json, shutil, argparse, hashlib, re
from pathlib import Path
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument('job_dir')
parser.add_argument('remediated_pdf')
parser.add_argument('--output-dir', default=None,
                    help='Final deliverables destination (output/{TICKET}_remediated/)')
parser.add_argument('--source-pdf', default='')
parser.add_argument('--skip-pdf', action='store_true',
                    help='Write audit report only — do not copy PDF to output-dir')
args = parser.parse_args()

job_dir  = Path(args.job_dir)
pdf_src  = Path(args.remediated_pdf)

if not job_dir.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'Job dir not found: {job_dir}'}))
    sys.exit(2)
if not pdf_src.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'PDF not found: {pdf_src}'}))
    sys.exit(2)

# ── Resolve output directory ──────────────────────────────────────────────────

if args.output_dir:
    output_dir = Path(args.output_dir)
else:
    # Fall back to sibling of jobs/ named output/{ticket}_remediated
    ticket_part = job_dir.name.split('_')[0]
    workspace   = job_dir.parent.parent
    output_dir  = workspace / 'output' / f'{ticket_part}_remediated'

output_dir.mkdir(parents=True, exist_ok=True)

# ── Assemble internal package inside job_dir ──────────────────────────────────

for sub in ('pdf', 'reports', 'qa', 'logs', 'audit'):
    (job_dir / sub).mkdir(exist_ok=True)

copied_internal = []

# Copy remediated PDF into job/pdf/
dest_pdf_internal = job_dir / 'pdf' / pdf_src.name
shutil.copy2(pdf_src, dest_pdf_internal)
copied_internal.append(str(dest_pdf_internal))

# Generate checksums over all job files
def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

checksum_lines = []
for p in sorted(job_dir.rglob('*')):
    if p.is_file() and p.name not in ('SHA256SUMS.txt',):
        try:
            rel = p.relative_to(job_dir)
            checksum_lines.append(f'{sha256(p)}  {rel}\n')
        except Exception:
            pass

(job_dir / 'SHA256SUMS.txt').write_text(''.join(checksum_lines))

# Write PACKAGE_CONTENTS.md
contents = f"""# Package Contents

**Job:** {job_dir.name}
**Assembled:** {datetime.now(timezone.utc).isoformat()}
**Remediated PDF:** {pdf_src.name}
{"**Source PDF:** " + args.source_pdf if args.source_pdf else ""}

## Files

| Path | Description |
|------|-------------|
| pdf/{pdf_src.name} | Remediated PDF output |
| audit/ | Audit and validation JSON + XML reports |
| repair/ | Intermediate repair outputs |
| qa/ | Visual QA thumbnails and render comparisons |
| reports/ | Alt text drafts, review HTML, alt maps |
| STATUS.json | Overall remediation status |
| SHA256SUMS.txt | File integrity checksums |
"""
(job_dir / 'PACKAGE_CONTENTS.md').write_text(contents)

# ── Read STATUS.json for audit report ────────────────────────────────────────

status_path = job_dir / 'STATUS.json'
status = {}
if status_path.exists():
    try:
        status = json.loads(status_path.read_text())
    except Exception:
        pass

overall       = status.get('overall_result', 'UNKNOWN')
gates         = status.get('gates', {})
generated_at  = status.get('generated_at', datetime.now(timezone.utc).isoformat())

# ── Generate AUDIT_REPORT.md ─────────────────────────────────────────────────

# Derive clean basename from source PDF if provided, otherwise from remediated PDF
if args.source_pdf:
    basename = Path(args.source_pdf).stem
else:
    # Fallback: strip pass-numbering and _remediated suffix from remediated PDF name
    basename = pdf_src.stem
    basename = re.sub(r'^pass\d+_', '', basename)
    basename = basename.replace('_remediated', '').replace('-remediated', '')

def gate_row(name, display):
    g = gates.get(name, {})
    result = g.get('result', 'NOT_RUN')
    icon   = '✅' if result in ('PASS', 'FIXED', 'ALREADY_CORRECT',
                                 'PASS_WITH_MIXED_PAGES', 'SKIPPED') else \
             '⚠️' if result in ('REVIEW_REQUIRED', 'WARN', 'NEEDS_REVIEW') else \
             '❌' if result == 'FAIL' else '—'
    return f'| {display} | {icon} {result} |\n'

audit_report = f"""# Montefiore PDF/UA Remediation Audit Report

**Source:** {basename}.pdf
**Remediated:** {pdf_src.name}
**Job:** {job_dir.name}
**Generated:** {generated_at}
**Overall Result:** {overall}

---

## Gate Results

| Gate | Result |
|------|--------|
{gate_row('qpdf',           'Structural integrity (qpdf)')}
{gate_row('verapdf_pdfua',  'veraPDF PDF/UA-1 + WCAG 2.2')}
{gate_row('metadata_parity','Metadata XMP parity')}
{gate_row('preservation',   'Native text preservation')}
{gate_row('table_semantics','Table semantics')}
{gate_row('contrast',       'Contrast (WCAG 1.4.3)')}
{gate_row('alt_text',       'Figure alt text')}
{gate_row('ocr_detection',  'OCR pre-flight')}
{gate_row('render_compare', 'Visual render comparison')}
{gate_row('visual_qa',      'Visual QA')}

---

## Repairs Applied

*(See STATUS.json and job directory for full repair log.)*

---

## External Validators

axesCheck and PAC 2024 are not run in this container. The receiving party
should run these before final sign-off.

---

## Notes

- This document and associated files are provided for review.
- Source files are preserved in the job directory.
- SHA-256 checksums are in SHA256SUMS.txt in the job directory.

**Do not consider this PDF fully compliant until external validators
(axesCheck, PAC 2024) have been run.**
"""

# ── Write final deliverables to output_dir ───────────────────────────────────

# Determine output PDF name
out_pdf_name    = f'{basename}_remediated.pdf'
out_report_name = f'{basename}_AUDIT_REPORT.md'

out_report = output_dir / out_report_name
out_report.write_text(audit_report)

deliverables = {
    'audit_report': str(out_report),
}

if not args.skip_pdf:
    out_pdf = output_dir / out_pdf_name
    shutil.copy2(pdf_src, out_pdf)
    deliverables['pdf'] = str(out_pdf)
    # Checksum both output files
    out_checksum  = f'{sha256(out_pdf)}  {out_pdf_name}\n'
    out_checksum += f'{sha256(out_report)}  {out_report_name}\n'
else:
    # Audit report only — no PDF handed off for FAIL/ESCALATION
    out_checksum = f'{sha256(out_report)}  {out_report_name}\n'

(output_dir / 'SHA256SUMS.txt').write_text(out_checksum)
deliverables['checksums'] = str(output_dir / 'SHA256SUMS.txt')

print(json.dumps({
    'result':      'OK',
    'job_dir':     str(job_dir),
    'output_dir':  str(output_dir),
    'skip_pdf':    args.skip_pdf,
    'deliverables': deliverables,
    'internal_package': {
        'pdf':       str(dest_pdf_internal),
        'checksums': str(job_dir / 'SHA256SUMS.txt'),
        'manifest':  str(job_dir / 'PACKAGE_CONTENTS.md'),
    },
    'overall_result': overall,
}, indent=2))
sys.exit(0)
