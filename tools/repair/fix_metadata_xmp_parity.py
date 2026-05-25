#!/usr/bin/env python3
"""
fix_metadata_xmp_parity.py
Enforces Montefiore-required metadata values and synchronises the PDF Info
dictionary with the XMP metadata packet.

Required fixed values (per METADATA_XMP_PARITY_HARD_GATE.md):
  Author:   Montefiore Einstein
  Creator:  Montefiore Einstein
  Producer: Montefiore Einstein

Descriptive fields — MUST be derived from document content by the agent
and passed explicitly as arguments. The script will fail if these are not
provided and the source PDF values are inadequate:
  --title       Document title (from the visible document heading)
  --subject     One-sentence description of document purpose
  --keywords    Comma-separated keywords, content-specific
  --description Longer description (optional)
  --language    Primary language (default: en-US)

AGENT INSTRUCTION: Before calling this script, read the document and derive:
  - Title: the main visible heading, not a footer or filename
  - Subject: one sentence describing what the document is and its purpose
  - Keywords: 4-8 comma-separated terms covering topic, department, form ID
  These must be passed as --title, --subject, --keywords arguments.
  Do not rely on source PDF metadata — it is frequently wrong or empty.

PDF/UA-1 identifier (enforced):
  pdfuaid:part = 1
  pdfuaid:amd  = 2005
  pdfuaid:rev  is REMOVED (PDF/UA-2 only)

All fields are written to both the Info dictionary and the XMP packet.
Rerun metadata_xmp_parity_audit.py after applying to confirm PASS.

Usage:
  fix_metadata_xmp_parity.py <input.pdf> <output.pdf>
    --title "Document Title"
    --subject "One sentence subject"
    --keywords "keyword1, keyword2, keyword3"
    [--description "Longer description"]
    [--language en-US]
    [--out results.json]

Exit codes:
  0  success
  1  inadequate metadata — required args missing and source values unusable
  2  error (file I/O, missing dependency)
"""
import sys, json, re, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

# Values that look like metadata but are footer/artifact text.
# If source PDF values match these patterns, they are treated as absent.
ARTIFACT_PATTERNS = [
    r'^\d+$',
    r'^health information management$',
    r'^microsoft word',
    r'^adobe acrobat',
    r'^untitled',
    r'^document\d*$',
]

def is_meaningful(value, min_words=3):
    if not value or len(value.strip()) < 4:
        return False
    v = value.strip().lower()
    for pattern in ARTIFACT_PATTERNS:
        if re.match(pattern, v, re.I):
            return False
    if len(v.split()) < min_words:
        return False
    return True

parser = argparse.ArgumentParser()
parser.add_argument('input_pdf')
parser.add_argument('output_pdf')
parser.add_argument('--title',       default=None)
parser.add_argument('--subject',     default=None)
parser.add_argument('--description', default=None)
parser.add_argument('--keywords',    default=None)
parser.add_argument('--language',    default='en-US')
parser.add_argument('--out',         default=None)
args = parser.parse_args()

FIXED_AUTHOR   = 'Montefiore Einstein'
FIXED_CREATOR  = 'Montefiore Einstein'
FIXED_PRODUCER = 'Montefiore Einstein'

try:
    doc = fitz.open(args.input_pdf)
except Exception as e:
    out = json.dumps({'result': 'ERROR', 'error': f'Could not open PDF: {e}'}, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(2)

meta = doc.metadata or {}
xmp  = doc.get_xml_metadata() or ''
changes = []
warnings = []

# ── Resolve descriptive fields ────────────────────────────────────────────────
# Explicit args take priority. Source PDF values are used ONLY if meaningful.
# If neither is available, fail with a clear error.

def resolve_field(arg_value, source_value, field_name, min_words=3, required=True):
    """Return the best available value or None. Logs warnings/errors."""
    if arg_value and is_meaningful(arg_value, min_words=1):
        return arg_value.strip()
    src = re.sub(r'<[^>]+>', '', source_value or '').strip()
    if src and is_meaningful(src, min_words=min_words):
        warnings.append(
            f'{field_name}: no --{field_name.lower()} argument provided; '
            f'using source PDF value "{src}" — verify this is correct'
        )
        return src
    if required:
        return None  # caller will handle as error
    return ''

title    = resolve_field(args.title,    meta.get('title', ''),    'title',    min_words=3)
subject  = resolve_field(args.subject,  meta.get('subject', ''),  'subject',  min_words=3)
keywords = resolve_field(args.keywords, meta.get('keywords', ''), 'keywords', min_words=1)

# Collect missing required fields
missing = []
if not title:
    missing.append(
        '--title: not provided and source PDF title is missing or an artifact '
        f'("{meta.get("title", "")}")'
    )
if not subject:
    missing.append(
        '--subject: not provided and source PDF subject is missing or not meaningful '
        f'("{meta.get("subject", "")}")'
    )
if not keywords:
    missing.append(
        '--keywords: not provided and source PDF has no keywords. '
        'Derive 4-8 comma-separated keywords from document content and pass as --keywords.'
    )

if missing:
    out = json.dumps({
        'result':  'MISSING_REQUIRED_ARGS',
        'error':   (
            'Required descriptive metadata could not be determined. '
            'Read the document content and re-run with explicit arguments.'
        ),
        'missing': missing,
        'agent_instruction': (
            'Before calling this script, read the document and derive: '
            '(1) --title: the main visible heading of the document, not a footer or filename; '
            '(2) --subject: one sentence describing the document purpose; '
            '(3) --keywords: 4-8 comma-separated terms covering the topic, '
            'department, form number, and relevant clinical or administrative context.'
        )
    }, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(1)

description = args.description or ''
language    = args.language

# ── XMP helpers ───────────────────────────────────────────────────────────────

def get_xmp_val(tag, xmp_str):
    m = re.search(rf'<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>', xmp_str, re.S)
    return m.group(1).strip() if m else ''

def set_xmp_val(tag, value, xmp_str):
    new_tag = f'<{tag}>{value}</{tag}>'
    # Remove any existing occurrence (full element or self-closing)
    # Matches: <tag ...>...</tag> or <tag .../>
    pattern = rf'<{re.escape(tag)}\b[^>]*>(?:.*?</{re.escape(tag)}>)?|<{re.escape(tag)}\b[^>]*/>'
    cleaned = re.sub(pattern, '', xmp_str, flags=re.S)
    # Insert before </rdf:Description>
    return cleaned.replace('</rdf:Description>', f'  {new_tag}\n</rdf:Description>', 1)

def remove_xmp_tag(tag, xmp_str):
    # Match self-closing or full element
    pattern = rf'\s*<{re.escape(tag)}\b[^>]*>(?:.*?</{re.escape(tag)}>)?|\s*<{re.escape(tag)}\b[^>]*/>'
    cleaned = re.sub(pattern, '\n', xmp_str, flags=re.S)
    return (cleaned, cleaned != xmp_str)

# ── Apply required fixed values ───────────────────────────────────────────────

if meta.get('author') != FIXED_AUTHOR:
    meta['author'] = FIXED_AUTHOR
    changes.append(f'set Info.Author = {FIXED_AUTHOR!r}')
xmp = set_xmp_val('dc:creator',
                  f'<rdf:Seq><rdf:li>{FIXED_AUTHOR}</rdf:li></rdf:Seq>', xmp)

if meta.get('creator') != FIXED_CREATOR:
    meta['creator'] = FIXED_CREATOR
    changes.append(f'set Info.Creator = {FIXED_CREATOR!r}')
xmp = set_xmp_val('xmp:CreatorTool', FIXED_CREATOR, xmp)

if meta.get('producer') != FIXED_PRODUCER:
    meta['producer'] = FIXED_PRODUCER
    changes.append(f'set Info.Producer = {FIXED_PRODUCER!r}')
xmp = set_xmp_val('pdf:Producer', FIXED_PRODUCER, xmp)

# Title
if meta.get('title') != title:
    meta['title'] = title
    changes.append(f'set Info.Title = {title!r}')
xmp = set_xmp_val('dc:title',
                  f'<rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt>', xmp)

# Subject
if meta.get('subject') != subject:
    meta['subject'] = subject
    changes.append(f'set Info.Subject = {subject!r}')
xmp = set_xmp_val('dc:description',
                  f'<rdf:Alt><rdf:li xml:lang="x-default">{subject}</rdf:li></rdf:Alt>', xmp)

# Keywords
if meta.get('keywords') != keywords:
    meta['keywords'] = keywords
    changes.append(f'set Info.Keywords = {keywords!r}')
xmp = set_xmp_val('pdf:Keywords', keywords, xmp)

# Language
xmp = set_xmp_val('dc:language',
                  f'<rdf:Bag><rdf:li>{language}</rdf:li></rdf:Bag>', xmp)

# ── PDF/UA-1 identifier ───────────────────────────────────────────────────────

xmp = set_xmp_val('pdfuaid:part', '1', xmp)
xmp = set_xmp_val('pdfuaid:amd', '2005', xmp)
xmp, rev_removed = remove_xmp_tag('pdfuaid:rev', xmp)
if rev_removed:
    changes.append('removed pdfuaid:rev (PDF/UA-2 field)')

# ── Save ──────────────────────────────────────────────────────────────────────

try:
    doc.set_metadata(meta)
    doc.set_xml_metadata(xmp)
    doc.save(args.output_pdf, garbage=4, deflate=True)
except Exception as e:
    out = json.dumps({'result': 'ERROR', 'error': f'Could not save PDF: {e}'}, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(2)

result = 'FIXED' if changes else 'ALREADY_CORRECT'

output = json.dumps({
    'input':    args.input_pdf,
    'output':   args.output_pdf,
    'result':   result,
    'changes':  changes,
    'warnings': warnings,
    'metadata_applied': {
        'author':      FIXED_AUTHOR,
        'creator':     FIXED_CREATOR,
        'producer':    FIXED_PRODUCER,
        'title':       title,
        'subject':     subject,
        'keywords':    keywords,
        'description': description,
        'language':    language,
        'pdfuaid_part': '1',
        'pdfuaid_amd':  '2005',
        'pdfuaid_rev':  'removed',
    }
}, indent=2)

print(output)
if args.out:
    Path(args.out).write_text(output)

sys.exit(0)
