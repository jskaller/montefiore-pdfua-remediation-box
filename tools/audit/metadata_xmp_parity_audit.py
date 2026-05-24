#!/usr/bin/env python3
"""
metadata_xmp_parity_audit.py
Audits PDF metadata against the Montefiore required values AND checks
Info dictionary / XMP parity.

Required fixed values (per METADATA_XMP_PARITY_HARD_GATE.md):
  Author:   Montefiore Einstein
  Creator:  Montefiore Einstein
  Producer: Montefiore Einstein

Also checks:
  - pdfuaid:part = 1 (PDF/UA-1)
  - pdfuaid:rev must NOT be present (PDF/UA-2 field)
  - Title present and meaningful
  - Subject present
  - Document language set in catalog
  - Info dict and XMP values match for all key fields

Usage: metadata_xmp_parity_audit.py <pdf> [--out results.json]

Exit codes:
  0  PASS
  1  FAIL
  2  error
"""
import sys, json, re, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--out', default=None,
                    help='Write JSON result to this file in addition to stdout')
args = parser.parse_args()

# ── Required fixed values ─────────────────────────────────────────────────────
REQUIRED = {
    'author':   'Montefiore Einstein',
    'creator':  'Montefiore Einstein',
    'producer': 'Montefiore Einstein',
}

try:
    doc  = fitz.open(args.pdf)
    meta = doc.metadata or {}
    xmp  = doc.get_xml_metadata() or ''
except Exception as e:
    out = json.dumps({'result': 'ERROR', 'error': f'Could not open PDF: {e}'}, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(2)

checks = []

def xmp_val(tag):
    m = re.search(rf'<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>', xmp, re.S)
    return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''

def xmp_tag_present(tag):
    return bool(re.search(rf'<{re.escape(tag)}[\s>]', xmp))

# ── Check 1: Required fixed values ───────────────────────────────────────────

for field, required_value in REQUIRED.items():
    info_val = meta.get(field, '').strip()
    passed   = info_val == required_value
    checks.append({
        'field':          f'{field}_required_value',
        'info_value':     info_val,
        'required_value': required_value,
        'pass':           passed,
        'note':           f'Must be "{required_value}" — run fix_metadata_xmp_parity.py'
                          if not passed else ''
    })

# ── Check 2: Info/XMP parity ─────────────────────────────────────────────────

field_map = {
    'title':    'dc:title',
    'author':   'dc:creator',
    'subject':  'dc:description',
    'creator':  'xmp:CreatorTool',
    'producer': 'pdf:Producer',
}

for info_key, xmp_tag in field_map.items():
    info_val = meta.get(info_key, '').strip()
    xmp_v    = xmp_val(xmp_tag).strip()
    # Strip any residual RDF wrapper text from comparison
    info_clean = re.sub(r'<[^>]+>', '', info_val).strip()
    xmp_clean  = re.sub(r'<[^>]+>', '', xmp_v).strip()
    matched    = info_clean == xmp_clean
    checks.append({
        'field':      f'{info_key}_parity',
        'info_value': info_clean,
        'xmp_value':  xmp_clean,
        'pass':       matched,
        'note':       'Info/XMP mismatch — run fix_metadata_xmp_parity.py'
                      if not matched else ''
    })

# ── Check 3: PDF/UA-1 identifier ─────────────────────────────────────────────

has_part1 = bool(re.search(r'<pdfuaid:part[^>]*>1</pdfuaid:part>', xmp))
checks.append({
    'field': 'pdfuaid_part',
    'pass':  has_part1,
    'note':  'pdfuaid:part=1 missing — run fix_pdfua_identifier.py' if not has_part1 else ''
})

has_rev = xmp_tag_present('pdfuaid:rev')
checks.append({
    'field': 'pdfuaid_rev_absent',
    'pass':  not has_rev,
    'note':  'pdfuaid:rev is present — this is a PDF/UA-2 field and must be removed '
             'from PDF/UA-1 documents — run fix_metadata_xmp_parity.py'
             if has_rev else ''
})

# ── Check 4: Descriptive fields present ──────────────────────────────────────

title = re.sub(r'<[^>]+>', '', meta.get('title', '')).strip()
checks.append({
    'field': 'title_present',
    'value': title,
    'pass':  bool(title) and len(title) > 3,
    'note':  'No meaningful document title — pass --title to fix_metadata_xmp_parity.py'
             if not (bool(title) and len(title) > 3) else ''
})

subject = re.sub(r'<[^>]+>', '', meta.get('subject', '')).strip()
checks.append({
    'field': 'subject_present',
    'value': subject,
    'pass':  bool(subject) and len(subject) > 3,
    'note':  'No subject — pass --subject to fix_metadata_xmp_parity.py'
             if not (bool(subject) and len(subject) > 3) else ''
})

# ── Check 5: Document language ────────────────────────────────────────────────

catalog  = doc.pdf_catalog()
lang_ref = doc.xref_get_key(catalog, 'Lang')
has_lang = lang_ref[0] != 'null' and bool(lang_ref[1].strip().strip('()'))
checks.append({
    'field': 'catalog_lang',
    'value': lang_ref[1].strip('()') if has_lang else '',
    'pass':  has_lang,
    'note':  'No /Lang in catalog — set document language' if not has_lang else ''
})

# ── Result ────────────────────────────────────────────────────────────────────

result   = 'PASS' if all(c['pass'] for c in checks) else 'FAIL'
failures = [c for c in checks if not c['pass']]

output = json.dumps({
    'pdf':      args.pdf,
    'result':   result,
    'checks':   checks,
    'failures': failures,
    'info':     {k: re.sub(r'<[^>]+>', '', v).strip()
                 for k, v in meta.items() if isinstance(v, str)}
}, indent=2)

print(output)
if args.out:
    Path(args.out).write_text(output)

sys.exit(0 if result == 'PASS' else 1)
