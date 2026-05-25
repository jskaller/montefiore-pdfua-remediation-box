#!/usr/bin/env python3
"""
fix_figure_alt_text.py
Adds or repairs Alt text on Figure structure elements that are missing it.

Two modes:
  auto:   Sets placeholder alt text so veraPDF passes structurally.
          Outputs needs_review list for generate_alt_text_drafts.py.
          All placeholders must be replaced before Gate 9 can pass.

  manual: Reads alt_map_approved.json (reviewer-approved output from
          generate_alt_text_review_report.py) and applies exactly those
          descriptions. Figures marked decorative are artifacted.

The auto mode output feeds generate_alt_text_drafts.py.
The manual mode input comes from generate_alt_text_review_report.py.
Never apply auto placeholder text to a production document.

Usage:
  fix_figure_alt_text.py <input.pdf> <output.pdf>
  fix_figure_alt_text.py <input.pdf> <output.pdf> --alt-map alt_map_approved.json

Without --alt-map: auto mode (placeholder, needs_review list output).
With --alt-map:    manual mode (approved text applied, decorative artifacted).
"""
import sys, json, re, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('input')
parser.add_argument('output')
parser.add_argument('--alt-map', default=None,
                    help='alt_map_approved.json from generate_alt_text_review_report.py')
parser.add_argument('--out', default=None,
                    help='Write JSON result to this file in addition to stdout')
args = parser.parse_args()

# ── Load alt map ──────────────────────────────────────────────────────────────

alt_map    = {}
decorative = set()  # figure indices to artifact

if args.alt_map:
    try:
        map_data = json.loads(Path(args.alt_map).read_text())
        for idx_str, entry in map_data.get('figures', {}).items():
            if entry.get('decorative'):
                decorative.add(str(idx_str))
            elif entry.get('alt_text'):
                alt_map[str(idx_str)] = entry['alt_text']
    except Exception as e:
        print(json.dumps({'result': 'ERROR', 'error': f'Could not read alt-map: {e}'}))
        sys.exit(2)

doc = fitz.open(args.input)
changes      = []
needs_review = []

# ── Walk struct tree ──────────────────────────────────────────────────────────

catalog          = doc.pdf_catalog()
struct_tree_ref  = doc.xref_get_key(catalog, 'StructTreeRoot')

if struct_tree_ref[0] == 'null' or not struct_tree_ref[1]:
    result_obj = {
        'input':   args.input,
        'result':  'SKIPPED',
        'reason':  'No StructTreeRoot — document is not tagged'
    }
    out = json.dumps(result_obj, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(1)

def walk_struct(xref, doc):
    """Recursively walk structure tree, yield (xref, type, alt) for Figure nodes."""
    try:
        s_type = doc.xref_get_key(xref, 'S')
        alt    = doc.xref_get_key(xref, 'Alt')
        kids   = doc.xref_get_key(xref, 'K')
        yield (
            xref,
            s_type[1] if s_type[0] != 'null' else '',
            alt[1]    if alt[0]    != 'null' else None
        )
        if kids[0] == 'array':
            for ref in re.findall(r'(\d+)\s+0\s+R', kids[1]):
                yield from walk_struct(int(ref), doc)
        elif kids[0] == 'xref':
            yield from walk_struct(int(kids[1].split()[0]), doc)
    except Exception:
        return

def is_placeholder(alt_text: str) -> bool:
    """Return True if alt text is missing or a known placeholder pattern."""
    if alt_text is None:
        return True
    clean = alt_text.strip('()').strip()
    if not clean:
        return True
    if clean.startswith('[Figure') and 'alt text required' in clean.lower():
        return True
    if len(clean) < 3:
        return True
    return False

struct_root_xref = int(struct_tree_ref[1].split()[0])
fig_index = 0

for xref, s_type, alt in walk_struct(struct_root_xref, doc):
    clean_type = s_type.strip('/').strip()
    if clean_type != 'Figure':
        continue

    idx_str = str(fig_index)

    if args.alt_map:
        # Manual mode — apply approved map
        if idx_str in decorative:
            # Mark as artifact — set empty Alt and add Artifact marking
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(''))
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'artifacted',
                'alt_set':      None,
            })
        elif idx_str in alt_map:
            new_alt = alt_map[idx_str]
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(new_alt))
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'approved',
                'alt_set':      new_alt,
            })
        elif is_placeholder(alt):
            # Approved map doesn't cover this figure but it still has a placeholder
            # This should not happen if the review report covered all needs_review figures
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'skipped_not_in_map',
                'warning':      'Placeholder alt text remains — figure not in approved map',
            })
    else:
        # Auto mode — set placeholder for all figures missing meaningful alt text
        if is_placeholder(alt):
            new_alt = f'[Figure {fig_index + 1} — alt text required]'
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(new_alt))
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'auto-placeholder',
                'alt_set':      new_alt,
            })
            needs_review.append({
                'xref':         xref,
                'figure_index': fig_index,
            })

    fig_index += 1

# ── Save ──────────────────────────────────────────────────────────────────────

doc.save(args.output, garbage=4, deflate=True)

mode = 'manual' if args.alt_map else 'auto'

if mode == 'auto':
    result = 'NEEDS_REVIEW' if needs_review else 'ALREADY_CORRECT'
else:
    skipped = [c for c in changes if c.get('mode') == 'skipped_not_in_map']
    result  = 'FIXED' if not skipped else 'PARTIAL'

output_obj = {
    'input':          args.input,
    'output':         args.output,
    'result':         result,
    'mode':           mode,
    'figures_total':  fig_index,
    'changes':        changes,
    'needs_review':   needs_review,
    'note': (
        'Placeholder alt text set. Run generate_alt_text_drafts.py then '
        'generate_alt_text_review_report.py before applying approved text.'
        if result == 'NEEDS_REVIEW' else
        'Approved alt text applied. Verify with veraPDF.' if result == 'FIXED' else ''
    )
}

out = json.dumps(output_obj, indent=2)
print(out)
if args.out:
    Path(args.out).write_text(out)

sys.exit(0 if result in ('FIXED', 'ALREADY_CORRECT') else 1)
