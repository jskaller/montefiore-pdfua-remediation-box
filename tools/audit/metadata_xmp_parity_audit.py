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
  - Title present, meaningful, and not a footer/header artifact
  - Subject present and meaningful (not a single word or number)
  - Keywords present and non-empty
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

# Values that look like metadata but are actually artifacts from footers,
# page numbers, or source application defaults. Any title or subject matching
# these patterns is treated as missing.
ARTIFACT_PATTERNS = [
    r'^\d+$',                           # pure number e.g. "10"
    r'^health information management$', # footer text
    r'^microsoft word',                 # application name
    r'^adobe acrobat',                  # application name
    r'^untitled',                       # default
    r'^document\d*$',                   # default
]

def is_meaningful(value, min_words=3):
    """Return True if value looks like real content rather than an artifact."""
    if not value or len(value.strip()) < 4:
        return False
    v = value.strip().lower()
    for pattern in ARTIFACT_PATTERNS:
        if re.match(pattern, v, re.I):
            return False
    # Must have at least min_words words
    if len(v.split()) < min_words:
        return False
    return True

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
    info_val   = meta.get(info_key, '').strip()
    xmp_v      = xmp_val(xmp_tag).strip()
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
    'note':  'pdfuaid:rev is present — PDF/UA-2 field, must be removed from PDF/UA-1 documents'
             if has_rev else ''
})

# ── Check 4: Title — present, meaningful, not an artifact ────────────────────

title = re.sub(r'<[^>]+>', '', meta.get('title', '')).strip()
title_ok = is_meaningful(title, min_words=3)
checks.append({
    'field': 'title_present',
    'value': title,
    'pass':  title_ok,
    'note':  (
        'Title is missing, too short, or appears to be a footer/artifact '
        f'("{title}") — pass --title to fix_metadata_xmp_parity.py with the '
        'actual document title derived from document content'
    ) if not title_ok else ''
})

# ── Check 5: Subject — present and meaningful ─────────────────────────────────

subject = re.sub(r'<[^>]+>', '', meta.get('subject', '')).strip()
subject_ok = is_meaningful(subject, min_words=3)
checks.append({
    'field': 'subject_present',
    'value': subject,
    'pass':  subject_ok,
    'note':  (
        'Subject is missing, too short, or not meaningful '
        f'("{subject}") — pass --subject to fix_metadata_xmp_parity.py '
        'with a one-sentence description derived from document content'
    ) if not subject_ok else ''
})

# ── Check 6: Keywords — present and non-empty ────────────────────────────────

keywords = re.sub(r'<[^>]+>', '', meta.get('keywords', '')).strip()
keywords_ok = bool(keywords) and len(keywords) > 3
checks.append({
    'field': 'keywords_present',
    'value': keywords,
    'pass':  keywords_ok,
    'note':  (
        'Keywords are missing or empty — pass --keywords to '
        'fix_metadata_xmp_parity.py with comma-separated keywords '
        'derived from document content'
    ) if not keywords_ok else ''
})

# ── Check 7: Document language ────────────────────────────────────────────────

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
