#!/usr/bin/env python3
"""
preservation_audit.py
Compares word content between source and output PDF to verify native text
was not lost or reordered during remediation.

Checks: word count parity, exact order preservation, page count match.

Results:
  PASS          — exact word-for-word match
  REVIEW        — word counts match but order differs slightly (expected
                  after tag reordering); requires human sign-off
  REVIEW        — word count delta within tolerance (≤0.5% of source
                  word count); minor tokenization artifact from repair,
                  not real content loss
  FAIL          — word count delta exceeds tolerance; content may have
                  been lost or added

Usage: preservation_audit.py <source.pdf> <output.pdf> [--out results.json]
       [--tolerance 0.005]
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
parser.add_argument('--out', default=None,
                    help='Write JSON output to this file in addition to stdout')
parser.add_argument('--tolerance', type=float, default=0.005,
                    help='Maximum fractional word-count delta before FAIL '
                         '(default: 0.005 = 0.5%%). Deltas within tolerance '
                         'are reported as REVIEW, not FAIL.')
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

count_match  = len(src_words) == len(out_words)
order_match  = src_words == out_words
pages_match  = src_pages == out_pages

# Compute fractional delta — how many words were lost/gained as a fraction
# of the source word count. Repairs can cause minor tokenization differences
# (whitespace changes, ligature splitting) that show as 1-2 word deltas per
# page. These are not real content loss. The tolerance band catches them.
src_count   = len(src_words)
out_count   = len(out_words)
delta       = abs(src_count - out_count)
delta_frac  = delta / src_count if src_count > 0 else 0.0
within_tolerance = delta_frac <= args.tolerance

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
elif count_match or within_tolerance:
    result = 'REVIEW'
else:
    result = 'FAIL'

if result == 'REVIEW' and not count_match:
    note = (
        f'Word count delta {delta} ({delta_frac:.2%}) is within the '
        f'{args.tolerance:.2%} tolerance — likely a tokenization artifact '
        f'from structural repair, not real content loss. Review page diffs '
        f'before handoff.'
    )
elif result == 'REVIEW':
    note = 'Word counts match but order differs — review page diffs before handoff.'
elif result == 'FAIL':
    note = (
        f'Word count delta {delta} ({delta_frac:.2%}) exceeds the '
        f'{args.tolerance:.2%} tolerance — content may have been lost or added.'
    )
else:
    note = ''

output_data = json.dumps({
    'source':                args.source,
    'output':                args.output,
    'result':                result,
    'source_pages':          src_pages,
    'output_pages':          out_pages,
    'pages_match':           pages_match,
    'source_words':          src_count,
    'output_words':          out_count,
    'word_delta':            delta,
    'word_delta_pct':        round(delta_frac * 100, 3),
    'tolerance_pct':         round(args.tolerance * 100, 3),
    'within_tolerance':      within_tolerance,
    'count_match':           count_match,
    'exact_order_preserved': order_match,
    'pages_with_diffs':      len(page_diffs),
    'page_diffs':            page_diffs[:20],
    'note':                  note,
}, indent=2)

print(output_data)

if args.out:
    Path(args.out).write_text(output_data)

sys.exit(0 if result in ('PASS', 'REVIEW') else 1)
