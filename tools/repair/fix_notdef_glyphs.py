#!/usr/bin/env python3
"""
fix_notdef_glyphs.py
Detects and reports .notdef glyph usage (veraPDF rule 7.21.8 / PDF/UA 7.2).
.notdef glyphs appear when a font cannot render a character — they show as
empty boxes and break text extraction/reflow.

This script CANNOT automatically repair .notdef glyphs — the fix requires
either font substitution or content re-encoding, which must be done manually
or via fix_contrast_color_runs.py / font_replacement_report.py pipeline.

Output: JSON report of all .notdef occurrences with page, position, and font.
Exit 0 if none found, exit 1 if .notdef glyphs present.
"""
import sys, json
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 2:
    print('usage: fix_notdef_glyphs.py <input.pdf>', file=sys.stderr)
    sys.exit(2)

src = sys.argv[1]
doc = fitz.open(src)
findings = []

for page_num, page in enumerate(doc):
    # Extract raw text with glyph-level detail
    blocks = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in blocks.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                font = span.get('font', '')
                for char in span.get('chars', []):
                    # c == 65533 is the Unicode replacement char (often mapped from .notdef)
                    # glyph == 0 is the .notdef glyph index
                    c = char.get('c', -1)
                    glyph = char.get('glyph', -1)
                    if glyph == 0 or c == 65533:
                        findings.append({
                            'page':   page_num + 1,
                            'font':   font,
                            'char':   c,
                            'glyph':  glyph,
                            'origin': char.get('origin', []),
                            'bbox':   char.get('bbox', [])
                        })

result = 'PASS' if not findings else 'FAIL'
print(json.dumps({
    'input':          src,
    'result':         result,
    'notdef_count':   len(findings),
    'findings':       findings[:50],  # cap output at 50 for readability
    'total_findings': len(findings),
    'action_required': (
        'Font substitution or re-encoding required. '
        'Run font_inventory.py, then font_geometry_matcher.py to identify replacement candidates. '
        'Manual review of each occurrence is required before fix.'
    ) if findings else 'None'
}, indent=2))
sys.exit(0 if not findings else 1)
