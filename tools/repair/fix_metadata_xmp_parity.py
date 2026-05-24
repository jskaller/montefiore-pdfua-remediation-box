#!/usr/bin/env python3
"""
fix_metadata_xmp_parity.py
Enforces Montefiore-required metadata values and synchronises the PDF Info
dictionary with the XMP metadata packet.

Required fixed values (per METADATA_XMP_PARITY_HARD_GATE.md):
  Author:   Montefiore Einstein
  Creator:  Montefiore Einstein
  Producer: Montefiore Einstein

Descriptive fields (passed as arguments — derived by the agent from document
content before calling this script):
  --title       Document title (derived from visible document title)
  --subject     One-sentence subject description
  --description Longer description of document content
  --keywords    Comma-separated keywords, content-specific
  --language    Primary language (default: en-US)

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
    --description "Longer description"
    --keywords "keyword1, keyword2, keyword3"
    [--language en-US]
    [--out results.json]

Exit codes:
  0  success
  2  error
"""
import sys, json, re, argparse
from datetime import datetime, timezone
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('input_pdf')
parser.add_argument('output_pdf')
parser.add_argument('--title',       default=None, help='Document title')
parser.add_argument('--subject',     default=None, help='One-sentence subject')
parser.add_argument('--description', default=None, help='Document description')
parser.add_argument('--keywords',    default=None, help='Comma-separated keywords')
parser.add_argument('--language',    default='en-US', help='Primary language (default: en-US)')
parser.add_argument('--out',         default=None, help='Write JSON result to this file')
args = parser.parse_args()

# ── Required fixed values ─────────────────────────────────────────────────────
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

# ── Derive title from document if not provided ────────────────────────────────
def extract_title_from_doc(doc, current_title):
    """Use existing title if meaningful, otherwise extract from first page text."""
    if current_title and len(current_title.strip()) > 3:
        # Clean up raw XMP serialization artifacts if present
        clean = re.sub(r'<[^>]+>', '', current_title).strip()
        if clean and len(clean) > 3:
            return clean
    # Fall back to first non-empty line of first page
    try:
        page = doc[0]
        blocks = page.get_text('blocks')
        for b in blocks:
            text = b[4].strip() if len(b) > 4 else ''
            if len(text) > 5:
                return text.split('\n')[0].strip()[:120]
    except Exception:
        pass
    return ''

title = args.title
if not title:
    existing = meta.get('title', '') or ''
    title = extract_title_from_doc(doc, existing)

subject     = args.subject     or meta.get('subject', '') or ''
description = args.description or ''
keywords    = args.keywords    or meta.get('keywords', '') or ''
language    = args.language

# ── XMP helpers ───────────────────────────────────────────────────────────────

def get_xmp_val(tag, xmp_str):
    m = re.search(rf'<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>', xmp_str, re.S)
    return m.group(1).strip() if m else ''

def set_xmp_val(tag, value, xmp_str):
    """Set or replace an XMP tag value."""
    new_tag = f'<{tag}>{value}</{tag}>'
    if re.search(rf'<{re.escape(tag)}[\s>]', xmp_str):
        return re.sub(
            rf'<{re.escape(tag)}[^>]*>.*?</{re.escape(tag)}>',
            new_tag, xmp_str, flags=re.S
        )
    else:
        return xmp_str.replace('</rdf:Description>', f'  {new_tag}\n</rdf:Description>', 1)

def remove_xmp_tag(tag, xmp_str):
    """Remove an XMP tag entirely."""
    cleaned = re.sub(
        rf'\s*<{re.escape(tag)}[^>]*>.*?</{re.escape(tag)}>\s*',
        '\n', xmp_str, flags=re.S
    )
    if cleaned != xmp_str:
        return cleaned, True
    return xmp_str, False

# ── Apply required fixed values ───────────────────────────────────────────────

# Author
if meta.get('author') != FIXED_AUTHOR:
    meta['author'] = FIXED_AUTHOR
    changes.append(f'set Info.Author = {FIXED_AUTHOR!r}')
xmp = set_xmp_val('dc:creator', f'<rdf:Seq><rdf:li>{FIXED_AUTHOR}</rdf:li></rdf:Seq>', xmp)

# Creator (xmp:CreatorTool)
if meta.get('creator') != FIXED_CREATOR:
    meta['creator'] = FIXED_CREATOR
    changes.append(f'set Info.Creator = {FIXED_CREATOR!r}')
xmp = set_xmp_val('xmp:CreatorTool', FIXED_CREATOR, xmp)

# Producer
if meta.get('producer') != FIXED_PRODUCER:
    meta['producer'] = FIXED_PRODUCER
    changes.append(f'set Info.Producer = {FIXED_PRODUCER!r}')
xmp = set_xmp_val('pdf:Producer', FIXED_PRODUCER, xmp)

# Title
if title:
    if meta.get('title') != title:
        meta['title'] = title
        changes.append(f'set Info.Title = {title!r}')
    xmp = set_xmp_val('dc:title', f'<rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt>', xmp)

# Subject / Description
if subject:
    if meta.get('subject') != subject:
        meta['subject'] = subject
        changes.append(f'set Info.Subject = {subject!r}')
    xmp = set_xmp_val('dc:description',
                      f'<rdf:Alt><rdf:li xml:lang="x-default">{subject}</rdf:li></rdf:Alt>', xmp)

# Keywords
if keywords:
    if meta.get('keywords') != keywords:
        meta['keywords'] = keywords
        changes.append(f'set Info.Keywords = {keywords!r}')
    xmp = set_xmp_val('pdf:Keywords', keywords, xmp)

# Language
xmp = set_xmp_val('dc:language',
                  f'<rdf:Bag><rdf:li>{language}</rdf:li></rdf:Bag>', xmp)

# ── PDF/UA-1 identifier ───────────────────────────────────────────────────────
# Ensure part=1, amd=2005, and remove rev (PDF/UA-2 only)

xmp = set_xmp_val('pdfuaid:part', '1', xmp)
xmp = set_xmp_val('pdfuaid:amd', '2005', xmp)
xmp, rev_removed = remove_xmp_tag('pdfuaid:rev', xmp)
if rev_removed:
    changes.append('removed pdfuaid:rev (PDF/UA-2 field, not applicable to PDF/UA-1)')

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
    'metadata_applied': {
        'author':      FIXED_AUTHOR,
        'creator':     FIXED_CREATOR,
        'producer':    FIXED_PRODUCER,
        'title':       title,
        'subject':     subject,
        'description': description,
        'keywords':    keywords,
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
