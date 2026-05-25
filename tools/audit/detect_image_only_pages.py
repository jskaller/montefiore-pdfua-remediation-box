#!/usr/bin/env python3
"""
detect_image_only_pages.py
Pre-flight audit that classifies every page in a PDF as one of:

  text_native   — page has meaningful native text content (no OCR needed)
  image_only    — page has image(s) but no meaningful native text (OCR required)
  mixed         — page has both meaningful native text and embedded images
  blank         — page has neither text nor images

A page is treated as having no meaningful text when its character count
falls below MIN_CHARS (default: 30). This filters incidental text artifacts
common in scanned documents: page numbers, copyright symbols, watermarks,
and stray encoding operators that produce a handful of characters but carry
no readable content. A short but real text element — 'Table 1. Patient
Demographics' (30 chars) — passes this threshold and is classified correctly
by image presence as text_native or mixed.

This script must run as the FIRST step of the OCR gate. Its output determines
whether ocrmypdf should be invoked and on which pages. It does NOT invoke
OCR itself.

Exit codes:
  0 — all pages have native text (no OCR needed, safe to proceed)
  1 — one or more image-only pages detected (OCR required before repair)
  2 — error (document could not be opened or processed)

Usage: detect_image_only_pages.py <pdf> [--out results.json] [--min-chars N]
"""
import sys, json, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({
        'result': 'ERROR',
        'error': f'PyMuPDF unavailable: {e}'
    }))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--out', default=None,
                    help='Write JSON output to this file in addition to stdout')
parser.add_argument('--min-chars', type=int, default=30,
                    help='Pages with fewer characters than this are classified '
                         'as image_only (or blank if no images present). '
                         'Default 30 filters page numbers, watermarks, and '
                         'stray encoding artifacts while passing real content '
                         'such as short captions and table titles.')
args = parser.parse_args()

try:
    doc = fitz.open(args.pdf)
except Exception as e:
    output = json.dumps({
        'pdf': args.pdf,
        'result': 'ERROR',
        'error': f'Could not open document: {e}'
    }, indent=2)
    print(output)
    if args.out:
        Path(args.out).write_text(output)
    sys.exit(2)

MIN_CHARS = args.min_chars

page_results      = []
image_only_pages  = []
mixed_pages       = []
blank_pages       = []
text_native_pages = []

for page_num in range(len(doc)):
    page       = doc[page_num]
    page_label = page_num + 1  # 1-based

    # Extract text — strip whitespace to avoid false positives from pages
    # that encode only spaces or newlines as text operators
    raw_text   = page.get_text('text', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    char_count = len(raw_text.strip())
    has_meaningful_text = char_count >= MIN_CHARS

    # Detect embedded images
    images     = page.get_images(full=False)
    has_images = len(images) > 0

    # Calculate image coverage fraction — useful diagnostic even though
    # it does not affect classification in this single-condition model
    image_coverage = None
    if has_images:
        page_area = page.rect.width * page.rect.height
        if page_area > 0:
            blocks       = page.get_text('blocks')
            image_blocks = [b for b in blocks if b[6] == 1]  # type 1 = image
            if image_blocks:
                covered        = sum((b[2] - b[0]) * (b[3] - b[1]) for b in image_blocks)
                image_coverage = round(min(covered / page_area, 1.0), 3)

    # Classification: single condition — meaningful text or not
    if has_meaningful_text and has_images:
        classification = 'mixed'
        mixed_pages.append(page_label)
    elif has_meaningful_text and not has_images:
        classification = 'text_native'
        text_native_pages.append(page_label)
    elif not has_meaningful_text and has_images:
        classification = 'image_only'
        image_only_pages.append(page_label)
    else:
        classification = 'blank'
        blank_pages.append(page_label)

    page_results.append({
        'page':           page_label,
        'classification': classification,
        'char_count':     char_count,
        'image_count':    len(images),
        'image_coverage': image_coverage,
    })

doc.close()

ocr_required = len(image_only_pages) > 0

if ocr_required:
    overall = 'OCR_REQUIRED'
elif mixed_pages:
    overall = 'PASS_WITH_MIXED_PAGES'
else:
    overall = 'PASS'

output = json.dumps({
    'pdf':           args.pdf,
    'result':        overall,
    'ocr_required':  ocr_required,
    'page_count':    len(page_results),
    'min_chars_used': MIN_CHARS,
    'summary': {
        'text_native': len(text_native_pages),
        'image_only':  len(image_only_pages),
        'mixed':       len(mixed_pages),
        'blank':       len(blank_pages),
    },
    'image_only_pages': image_only_pages,
    'mixed_pages':      mixed_pages,
    'blank_pages':      blank_pages,
    'pages':            page_results,
    'note': (
        'image_only_pages is the list to report in STATUS.json ocr_pages field. '
        'Use --skip-text with ocrmypdf to preserve native text on mixed pages.'
    ) if ocr_required else None,
}, indent=2)

print(output)

if args.out:
    Path(args.out).write_text(output)

sys.exit(1 if ocr_required else 0)
