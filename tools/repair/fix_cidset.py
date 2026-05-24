#!/usr/bin/env python3
"""
fix_cidset.py
Removes /CIDSet entries from the FontDescriptor of every subset-embedded
CID font in the document.

Subset-embedded fonts are identified by the '+' prefix convention in the
BaseFont name (e.g. 'DKAECI+Calibri'). This prefix is assigned by the PDF
producer and is unique per document — the font names themselves cannot be
predicted in advance.

Why this fix is correct:
  ISO 14289-1 (PDF/UA-1) clause 7.21.4.2 requires that if a CIDSet stream
  is present in a FontDescriptor, it must identify ALL CIDs present in the
  embedded font program. Many PDF producers (Adobe PDFMaker, LibreOffice,
  Word) write incomplete CIDSet entries for subset fonts, causing veraPDF
  to fail this check.

  Removing CIDSet entirely is always standards-compliant. A missing CIDSet
  is explicitly permitted. An incomplete CIDSet is not.

Usage:
  fix_cidset.py <input.pdf> <output.pdf> [--out results.json]

Exit codes:
  0  success (fixed or already correct)
  1  partial — some fonts processed, some errors
  2  error — could not open or save document
"""
import sys, json, argparse
from pathlib import Path

try:
    import pikepdf
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'pikepdf unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('input_pdf')
parser.add_argument('output_pdf')
parser.add_argument('--out', default=None,
                    help='Write JSON result to this file in addition to stdout')
args = parser.parse_args()

try:
    pdf = pikepdf.open(args.input_pdf)
except Exception as e:
    out = json.dumps({'result': 'ERROR', 'error': f'Could not open PDF: {e}'}, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(2)

removed   = []
errors    = []
inspected = 0

for obj in pdf.objects:
    if obj is None:
        continue
    try:
        # Only process Font dictionaries
        if obj.get('/Type') != '/Font':
            continue

        base = obj.get('/BaseFont')
        if base is None:
            continue

        base_str = str(base)

        # Subset fonts have a 6-character uppercase prefix followed by '+'
        # e.g. 'DKAECI+Calibri'. This is the universal subset indicator.
        if '+' not in base_str:
            continue

        inspected += 1

        # CIDSet lives in the FontDescriptor of the CIDFont (descendant),
        # not the Type0 wrapper. Walk both locations.
        font_objects_to_check = []

        # Check descendant CIDFont
        if '/DescendantFonts' in obj:
            descendants = obj['/DescendantFonts']
            if isinstance(descendants, list):
                for d in descendants:
                    font_objects_to_check.append(d)
            else:
                font_objects_to_check.append(descendants)

        # Also check the object itself (in case it is a CIDFont directly)
        subtype = obj.get('/Subtype')
        if subtype in ('/CIDFontType0', '/CIDFontType2'):
            font_objects_to_check.append(obj)

        for font_obj in font_objects_to_check:
            try:
                fd = font_obj.get('/FontDescriptor')
                if fd is not None and '/CIDSet' in fd:
                    del fd['/CIDSet']
                    removed.append(base_str)
            except Exception as inner_e:
                errors.append({'font': base_str, 'error': str(inner_e)})

    except Exception as e:
        errors.append({'object': 'unknown', 'error': str(e)})

try:
    pdf.save(args.output_pdf)
    pdf.close()
except Exception as e:
    out = json.dumps({'result': 'ERROR', 'error': f'Could not save PDF: {e}'}, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(2)

if errors and not removed:
    result = 'FAIL'
elif errors:
    result = 'PARTIAL'
elif removed:
    result = 'FIXED'
else:
    result = 'ALREADY_CORRECT'

output = json.dumps({
    'input':                  args.input_pdf,
    'output':                 args.output_pdf,
    'result':                 result,
    'subset_fonts_inspected': inspected,
    'cidset_removed_from':    removed,
    'errors':                 errors,
    'note': (
        'CIDSet removed from subset fonts. Re-run veraPDF PDF/UA-1 to confirm '
        'ISO 14289-1 clause 7.21.4.2 passes.'
        if removed else
        'No incomplete CIDSet entries found — document already compliant.'
    )
}, indent=2)

print(output)
if args.out:
    Path(args.out).write_text(output)

sys.exit(0 if result in ('FIXED', 'ALREADY_CORRECT') else (1 if result == 'PARTIAL' else 2))
