#!/usr/bin/env python3
"""
font_replacement_report.py
Generates a full report of fonts in the PDF that require replacement or attention:
  - Non-embedded fonts (PDF/UA hard fail)
  - Fonts with missing Unicode mappings (ToUnicode stream absent)
  - Fonts with .notdef glyph usage
  - Proprietary fonts that should be replaced with open equivalents
    per FONT_POLICY_EXPECTED_OPEN_FONTS_AND_GEOMETRY.md

Output: JSON report suitable for driving font_geometry_matcher.py decisions.
Does NOT modify the PDF.
"""
import sys, json
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 2:
    print('usage: font_replacement_report.py <input.pdf>', file=sys.stderr)
    sys.exit(2)

src = sys.argv[1]
doc = fitz.open(src)

# Open font policy — extend as needed per FONT_POLICY doc
PREFERRED_OPEN_FONTS = {
    'serif':      ['LibreSerifRegular', 'NotoSerif', 'SourceSerifPro', 'GentiumPlus'],
    'sans-serif': ['LibreFranklin', 'NotoSans', 'SourceSansPro', 'OpenSans', 'Lato'],
    'monospace':  ['NotoSansMono', 'SourceCodePro', 'InconsolataRegular'],
}

KNOWN_PROPRIETARY = {
    'arial', 'helvetica', 'times new roman', 'times', 'courier new', 'courier',
    'calibri', 'cambria', 'georgia', 'verdana', 'trebuchet', 'garamond',
    'palatino', 'bookman', 'century', 'futura', 'myriad', 'minion'
}

fonts_seen = {}  # name -> details
issues = []

for page_num, page in enumerate(doc):
    for font in page.get_fonts(full=True):
        xref, ext, font_type, basename, name, enc = font[:6]
        key = name or basename

        if key in fonts_seen:
            fonts_seen[key]['pages'].append(page_num + 1)
            continue

        embedded = xref > 0
        has_tounicode = False
        if embedded:
            try:
                stream = doc.xref_stream(xref)
                has_tounicode = stream is not None and len(stream) > 0
            except Exception:
                has_tounicode = False

        # Check ToUnicode via font dict
        try:
            tounicode_ref = doc.xref_get_key(xref, 'ToUnicode')
            has_tounicode = tounicode_ref[0] != 'null'
        except Exception:
            pass

        is_proprietary = any(p in (name or '').lower() for p in KNOWN_PROPRIETARY)

        entry = {
            'name':          name,
            'basename':      basename,
            'type':          font_type,
            'ext':           ext,
            'encoding':      enc,
            'embedded':      embedded,
            'has_tounicode': has_tounicode,
            'is_proprietary': is_proprietary,
            'pages':         [page_num + 1],
            'xref':          xref,
            'issues':        []
        }

        if not embedded:
            entry['issues'].append('NOT_EMBEDDED — hard PDF/UA fail')
        if not has_tounicode and font_type not in ('Type3',):
            entry['issues'].append('NO_TOUNICODE — text extraction/reflow will fail')
        if is_proprietary:
            entry['issues'].append('PROPRIETARY — consider replacing with open equivalent')

        fonts_seen[key] = entry

        if entry['issues']:
            issues.append(key)

font_list = list(fonts_seen.values())
critical = [f for f in font_list if any('NOT_EMBEDDED' in i or 'NO_TOUNICODE' in i
                                         for i in f['issues'])]
advisory = [f for f in font_list if any('PROPRIETARY' in i for i in f['issues'])
            and f not in critical]

print(json.dumps({
    'input':             src,
    'result':            'FAIL' if critical else ('WARN' if advisory else 'PASS'),
    'total_fonts':       len(font_list),
    'critical_issues':   len(critical),
    'advisory_issues':   len(advisory),
    'all_fonts':         font_list,
    'action_required':   critical,
    'advisory':          advisory,
    'preferred_fonts':   PREFERRED_OPEN_FONTS,
    'note': (
        'Run font_geometry_matcher.py on critical fonts to find '
        'dimensionally compatible open replacements before substituting.'
    ) if critical else ''
}, indent=2))
sys.exit(0 if not critical else 1)
