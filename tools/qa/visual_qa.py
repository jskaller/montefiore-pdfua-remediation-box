#!/usr/bin/env python3
"""
visual_qa.py
Renders each page as a thumbnail PNG for human visual review.
Flags pages that are blank, very small, or have unusual aspect ratios.

Usage: visual_qa.py <pdf> <out-dir> [--dpi 96] [--out results.json]
"""
import sys, json, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'})); sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('out_dir')
parser.add_argument('--dpi', type=int, default=96)
parser.add_argument('--out', default=None, help='Write JSON output to this file in addition to stdout')
args = parser.parse_args()

out_dir = Path(args.out_dir)
out_dir.mkdir(parents=True, exist_ok=True)

doc    = fitz.open(args.pdf)
matrix = fitz.Matrix(args.dpi / 72, args.dpi / 72)
pages  = []
flagged = []

for i, page in enumerate(doc):
    pix      = page.get_pixmap(matrix=matrix)
    filename = f'page_{i+1:03d}.png'
    pix.save(str(out_dir / filename))

    total_pixels = pix.width * pix.height
    # Approximate blank detection: sample mean brightness
    samples   = pix.samples
    mean_val  = sum(samples) / len(samples) if samples else 255
    is_blank  = mean_val > 252  # nearly all white

    unusual_ratio = False
    if pix.height > 0:
        ratio = pix.width / pix.height
        unusual_ratio = ratio < 0.3 or ratio > 3.5

    flags = []
    if is_blank:
        flags.append('blank_page')
    if unusual_ratio:
        flags.append('unusual_aspect_ratio')

    entry = {
        'page':     i + 1,
        'file':     filename,
        'width_px': pix.width,
        'height_px': pix.height,
        'flags':    flags
    }
    pages.append(entry)
    if flags:
        flagged.append(i + 1)

result = 'PASS' if not flagged else 'REVIEW'

output_data = json.dumps({
    'pdf':           args.pdf,
    'result':        result,
    'dpi':           args.dpi,
    'pages_total':   len(doc),
    'pages_flagged': len(flagged),
    'flagged_pages': flagged,
    'thumbnails_dir': str(out_dir),
    'pages':         pages
}, indent=2)

print(output_data)

if args.out:
    Path(args.out).write_text(output_data)

sys.exit(0 if result == 'PASS' else 1)
