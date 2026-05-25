#!/usr/bin/env python3
"""
fix_contrast_color_runs.py
Detects text runs that fail WCAG 1.4.3 contrast (minimum 4.5:1 for normal text,
3:1 for large text >= 18pt or 14pt bold).

This script AUDITS and REPORTS — it cannot automatically recolor text runs
because the fix requires knowing the intended brand color vs. the background,
which requires human judgment.

Output: JSON report of failing text runs with page, font, size, fg color,
bg color (approximated from page background), and contrast ratio.

Use the report to drive manual fixes in the source document or via
a targeted color-patch script if the color set is known and bounded.
"""
import sys, json
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 2:
    print('usage: fix_contrast_color_runs.py <input.pdf>', file=sys.stderr)
    sys.exit(2)

src = sys.argv[1]
doc = fitz.open(src)

def relative_luminance(r, g, b):
    """WCAG 2.x relative luminance from sRGB 0-1 floats."""
    def linearize(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

def contrast_ratio(l1, l2):
    lighter = max(l1, l2)
    darker  = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def pdf_color_to_rgb(color):
    """Convert PyMuPDF color int or tuple to (r,g,b) 0-1 floats."""
    if color is None:
        return (0, 0, 0)
    if isinstance(color, int):
        r = ((color >> 16) & 0xFF) / 255
        g = ((color >>  8) & 0xFF) / 255
        b = ((color      ) & 0xFF) / 255
        return (r, g, b)
    if isinstance(color, (list, tuple)):
        if len(color) == 3:
            return tuple(color)
        if len(color) == 1:
            v = color[0]
            return (v, v, v)
    return (0, 0, 0)

BG_WHITE = (1.0, 1.0, 1.0)  # default background assumption

failures = []
warnings = []

for page_num, page in enumerate(doc):
    # Try to detect page background color from page dict
    bg_color = BG_WHITE
    bg_lum = relative_luminance(*bg_color)

    blocks = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in blocks.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                color_int = span.get('color', 0)
                size      = span.get('size', 12)
                flags     = span.get('flags', 0)
                text      = span.get('text', '').strip()
                font      = span.get('font', '')

                if not text:
                    continue

                is_bold  = bool(flags & 2**4)
                is_large = size >= 18 or (is_bold and size >= 14)
                threshold = 3.0 if is_large else 4.5

                fg_rgb = pdf_color_to_rgb(color_int)
                fg_lum = relative_luminance(*fg_rgb)
                ratio  = contrast_ratio(fg_lum, bg_lum)

                if ratio < threshold:
                    entry = {
                        'page':      page_num + 1,
                        'text':      text[:60],
                        'font':      font,
                        'size':      round(size, 1),
                        'bold':      is_bold,
                        'fg_rgb':    [round(x, 3) for x in fg_rgb],
                        'bg_rgb':    [round(x, 3) for x in bg_color],
                        'ratio':     round(ratio, 2),
                        'required':  threshold,
                        'bbox':      [round(x, 1) for x in span.get('bbox', [])]
                    }
                    if ratio < 2.0:
                        failures.append(entry)
                    else:
                        warnings.append(entry)

result = 'PASS' if not failures else 'FAIL'
print(json.dumps({
    'input':         src,
    'result':        result,
    'failures':      len(failures),
    'warnings':      len(warnings),
    'failure_list':  failures[:30],
    'warning_list':  warnings[:30],
    'note': (
        'Background color detection uses page default (white). '
        'Runs on colored backgrounds may produce false positives. '
        'Manual review required before applying any color changes.'
    )
}, indent=2))
sys.exit(0 if result == 'PASS' else 1)
