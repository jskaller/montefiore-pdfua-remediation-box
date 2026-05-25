#!/usr/bin/env python3
"""
package_deliverables.py
Assembles final deliverable package: copies remediated PDF, all reports,
QA artifacts, writes STATUS.json, generates checksums, and produces
a PACKAGE_CONTENTS.md summary.

Usage: package_deliverables.py <job-dir> <remediated-pdf> [--source-pdf original.pdf]
"""
import sys, json, shutil, argparse, hashlib
from pathlib import Path
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument('job_dir')
parser.add_argument('remediated_pdf')
parser.add_argument('--source-pdf', default='')
args = parser.parse_args()

job_dir = Path(args.job_dir)
pdf_src = Path(args.remediated_pdf)

if not job_dir.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'Job dir not found: {job_dir}'}))
    sys.exit(2)
if not pdf_src.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'PDF not found: {pdf_src}'}))
    sys.exit(2)

# Ensure subdirs
for sub in ('pdf', 'reports', 'qa', 'logs'):
    (job_dir / sub).mkdir(exist_ok=True)

copied = []

# Copy remediated PDF
dest_pdf = job_dir / 'pdf' / pdf_src.name
shutil.copy2(pdf_src, dest_pdf)
copied.append(str(dest_pdf))

# Move/copy all JSON reports into reports/
for json_file in sorted(job_dir.glob('*.json')):
    if json_file.name not in ('STATUS.json', 'PACKAGE_MANIFEST.json'):
        dest = job_dir / 'reports' / json_file.name
        shutil.copy2(json_file, dest)
        copied.append(str(dest))

# Move/copy QA images into qa/
for img in list(job_dir.glob('*.png')) + list(job_dir.glob('*.jpg')):
    dest = job_dir / 'qa' / img.name
    shutil.copy2(img, dest)
    copied.append(str(dest))

# Move XML reports into reports/
for xml in job_dir.glob('*.xml'):
    dest = job_dir / 'reports' / xml.name
    shutil.copy2(xml, dest)
    copied.append(str(dest))

# Generate checksums
def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

checksum_lines = []
for p in sorted(job_dir.rglob('*')):
    if p.is_file() and p.name not in ('SHA256SUMS.txt',):
        rel = p.relative_to(job_dir)
        checksum_lines.append(f'{sha256(p)}  {rel}\n')

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
| reports/ | Audit and validation JSON + XML reports |
| qa/ | Visual QA thumbnails and render comparisons |
| logs/ | Process logs |
| STATUS.json | Overall remediation status |
| SHA256SUMS.txt | File integrity checksums |
"""
(job_dir / 'PACKAGE_CONTENTS.md').write_text(contents)

print(json.dumps({
    'result':     'OK',
    'job_dir':    str(job_dir),
    'pdf':        str(dest_pdf),
    'files_copied': len(copied),
    'checksums':  str(job_dir / 'SHA256SUMS.txt'),
    'manifest':   str(job_dir / 'PACKAGE_CONTENTS.md')
}, indent=2))
sys.exit(0)
