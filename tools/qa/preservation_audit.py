#!/usr/bin/env python3
"""
preservation_audit.py
Compares word content between source and output PDF to verify native text
was not lost or reordered during remediation.

Checks: word count parity, exact order preservation, page count match.
A REVIEW result means word counts match but order differs slightly —
expected after tag reordering; requires human sign-off.

Usage: preservation_audit.py <source.pdf> <output.pdf> [--out results.json]
"""
import sys, json, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': str(e)})); sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('source')
parser.add_argument('output')
parser.add_argument('--out', default=None, help='Write JSON output to this file in addition to stdout')
args = parser.parse_args()

def extract(path):
    doc = fitz.open(path)
    words_by_page = []
    all_words = []
    for page in doc:
        pw = [w[4] for w in page.get_text('words')]
        words_by_page.append(pw)
        all_words.extend(pw)
    return all_words, words_by_page, len(doc)

src_words, src_by_page, src_pages = extract(args.source)
out_words, out_by_page, out_pages = extract(args.output)

count_match = len(src_words) == len(out_words)
order_match = src_words == out_words
pages_match = src_pages == out_pages

page_diffs = []
for i, (sp, op) in enumerate(zip(src_by_page, out_by_page)):
    if sp != op:
        page_diffs.append({
            'page':         i + 1,
            'source_words': len(sp),
            'output_words': len(op),
            'exact_match':  sp == op
        })

if order_match:
    result = 'PASS'
elif count_match:
    result = 'REVIEW'
else:
    result = 'FAIL'

output_data = json.dumps({
    'source':                args.source,
    'output':                args.output,
    'result':                result,
    'source_pages':          src_pages,
    'output_pages':          out_pages,
    'pages_match':           pages_match,
    'source_words':          len(src_words),
    'output_words':          len(out_words),
    'count_match':           count_match,
    'exact_order_preserved': order_match,
    'pages_with_diffs':      len(page_diffs),
    'page_diffs':            page_diffs[:20],
    'note': ('Word counts match but order differs — review page diffs before handoff.'
             if result == 'REVIEW' else
             'Word count mismatch — content may have been lost or added.'
             if result == 'FAIL' else '')
}, indent=2)

print(output_data)

if args.out:
    Path(args.out).write_text(output_data)

sys.exit(0 if result == 'PASS' else 1)
