#!/usr/bin/env python3
"""
fix_link_annotation_descriptions.py
Ensures all Link annotations have a Contents (tooltip/description) entry.
PDF/UA requires link annotations to have meaningful descriptions (veraPDF 7.18.5).
Rerun veraPDF PDF/UA after applying.
"""
import sys, json
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 3:
    print('usage: fix_link_annotation_descriptions.py <input.pdf> <output.pdf>',
          file=sys.stderr)
    sys.exit(2)

src, dst = sys.argv[1], sys.argv[2]
doc = fitz.open(src)
changes = []
needs_review = []

for page_num, page in enumerate(doc):
    for annot in page.annots():
        if annot.type[1] != 'Link':
            continue

        xref = annot.xref
        # Get existing Contents
        contents = doc.xref_get_key(xref, 'Contents')
        uri_action = doc.xref_get_key(xref, 'A')

        has_contents = (contents[0] != 'null' and
                        contents[1].strip().strip('()').strip())

        if not has_contents:
            # Try to derive a description from the URI if present
            desc = ''
            if uri_action[0] != 'null' and 'URI' in uri_action[1]:
                import re
                m = re.search(r'\(([^)]+)\)', uri_action[1])
                if m:
                    desc = m.group(1)

            if not desc:
                # Try to get the visible text near the annotation rect
                rect = annot.rect
                clip_text = page.get_text('text', clip=rect).strip()
                desc = clip_text[:120] if clip_text else f'[Link on page {page_num + 1} — description required]'
                if not clip_text:
                    needs_review.append({
                        'page': page_num + 1,
                        'xref': xref,
                        'note': 'No visible text found — placeholder set, review required'
                    })

            doc.xref_set_key(xref, 'Contents', fitz.get_pdf_str(desc))
            changes.append({
                'page':    page_num + 1,
                'xref':    xref,
                'set_to':  desc[:80] + ('...' if len(desc) > 80 else '')
            })

if changes:
    doc.save(dst, garbage=4, deflate=True)
    result = 'FIXED'
else:
    doc.save(dst, garbage=4, deflate=True)
    result = 'ALREADY_CORRECT'

print(json.dumps({
    'input':        src,
    'output':       dst,
    'result':       result,
    'links_fixed':  len(changes),
    'needs_review': needs_review,
    'changes':      changes
}, indent=2))
sys.exit(0)
