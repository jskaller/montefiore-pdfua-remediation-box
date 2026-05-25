#!/usr/bin/env python3
"""
contrast_audit.py
Audits text contrast ratios across all pages against WCAG 1.4.3 thresholds.

Usage: contrast_audit.py <pdf> [--out results.json]

Note: background is assumed white. Colored backgrounds may produce false positives.
"""
import sys, json, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'})); sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--out', default=None, help='Write JSON output to this file in addition to stdout')
args = parser.parse_args()

doc = fitz.open(args.pdf)

def relative_luminance(r, g, b):
    def lin(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

def contrast_ratio(l1, l2):
    a, b = max(l1, l2), min(l1, l2)
    return (a + 0.05) / (b + 0.05)

def color_to_rgb(c):
    if isinstance(c, int):
        return ((c >> 16 & 0xFF) / 255, (c >> 8 & 0xFF) / 255, (c & 0xFF) / 255)
    if isinstance(c, (list, tuple)) and len(c) == 3: return tuple(c)
    if isinstance(c, (list, tuple)) and len(c) == 1: return (c[0], c[0], c[0])
    return (0, 0, 0)

failures, warnings, by_page = [], [], {}
BG = (1.0, 1.0, 1.0)
bg_lum = relative_luminance(*BG)

for page_num, page in enumerate(doc):
    page_failures = 0
    for block in page.get_text('dict').get('blocks', []):
        if block.get('type') != 0: continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                text  = span.get('text', '').strip()
                if not text: continue
                size  = span.get('size', 12)
                flags = span.get('flags', 0)
                is_bold  = bool(flags & 16)
                is_large = size >= 18 or (is_bold and size >= 14)
                threshold = 3.0 if is_large else 4.5
                fg  = color_to_rgb(span.get('color', 0))
                lum = relative_luminance(*fg)
                cr  = contrast_ratio(lum, bg_lum)
                if cr < threshold:
                    entry = {
                        'page': page_num + 1, 'text': text[:60],
                        'font': span.get('font', ''), 'size': round(size, 1),
                        'ratio': round(cr, 2), 'required': threshold,
                        'fg_rgb': [round(x, 3) for x in fg]
                    }
                    (failures if cr < 2.0 else warnings).append(entry)
                    page_failures += 1
    if page_failures:
        by_page[str(page_num + 1)] = page_failures

result = 'PASS' if not failures else 'FAIL'

output = json.dumps({
    'pdf':            args.pdf,
    'result':         result,
    'failures':       len(failures),
    'warnings':       len(warnings),
    'pages_affected': len(by_page),
    'by_page':        by_page,
    'failure_list':   failures[:20],
    'warning_list':   warnings[:20],
    'note':           'Background assumed white. Colored backgrounds may produce false positives.'
}, indent=2)

print(output)

if args.out:
    Path(args.out).write_text(output)

sys.exit(0 if result == 'PASS' else 1)
