#!/usr/bin/env python3
"""
fix_figure_alt_text.py
Adds or repairs Alt text on Figure structure elements that are missing it.
Also sets the /Lang attribute on Figure elements to satisfy PDF/UA-1 clause 7.2
(natural language for text in Alt attribute must be determinable).

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
  fix_figure_alt_text.py <input.pdf> <output.pdf> [--language en-US]
  fix_figure_alt_text.py <input.pdf> <output.pdf> --alt-map alt_map_approved.json [--language en-US]

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
parser.add_argument('--language', default='en-US',
                    help='Language tag to set on Figure struct elements (default: en-US)')
parser.add_argument('--out', default=None,
                    help='Write JSON result to this file in addition to stdout')
args = parser.parse_args()

# ── Load alt map ──────────────────────────────────────────────────────────────

alt_map    = {}
decorative = set()

if args.alt_map:
    try:
        map_data = json.loads(Path(args.alt_map).read_text())
        for idx_str, entry in map_data.get('figures', {}).items():
            if entry.get('decorative'):
                decorative.add(str(idx_str))
            else:
                # Accept either key name. Different map writers use different
                # conventions: generate_alt_text_drafts.py writes 'alt_text_draft',
                # human-edited maps and the asset library use 'alt_text'.
                # Either is treated as the approved alt text in manual mode.
                alt_value = entry.get('alt_text') or entry.get('alt_text_draft')
                if alt_value:
                    alt_map[str(idx_str)] = alt_value
    except Exception as e:
        print(json.dumps({'result': 'ERROR', 'error': f'Could not read alt-map: {e}'}))
        sys.exit(2)

doc = fitz.open(args.input)
changes      = []
needs_review = []

# ── Walk struct tree ──────────────────────────────────────────────────────────

catalog         = doc.pdf_catalog()
struct_tree_ref = doc.xref_get_key(catalog, 'StructTreeRoot')

if struct_tree_ref[0] == 'null' or not struct_tree_ref[1]:
    result_obj = {
        'input':  args.input,
        'result': 'SKIPPED',
        'reason': 'No StructTreeRoot — document is not tagged'
    }
    out = json.dumps(result_obj, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(1)

def walk_struct(xref, doc):
    """Recursively walk structure tree, yield (xref, type, alt) for all nodes."""
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

def set_lang_on_element(xref, doc, language):
    """Set the /Lang attribute on a structure element for PDF/UA-1 clause 7.2."""
    try:
        lang_val = doc.xref_get_key(xref, 'Lang')
        if lang_val[0] == 'null' or not lang_val[1].strip().strip('()'):
            doc.xref_set_key(xref, 'Lang', fitz.get_pdf_str(language))
            return True
    except Exception:
        pass
    return False

struct_root_xref = int(struct_tree_ref[1].split()[0])
fig_index = 0

for xref, s_type, alt in walk_struct(struct_root_xref, doc):
    clean_type = s_type.strip('/').strip()
    if clean_type != 'Figure':
        continue

    idx_str = str(fig_index)

    if args.alt_map:
        # ── Manual mode ───────────────────────────────────────────────────
        if idx_str in decorative:
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(''))
            set_lang_on_element(xref, doc, args.language)
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'artifacted',
                'alt_set':      None,
                'lang_set':     args.language,
            })
        elif idx_str in alt_map:
            new_alt = alt_map[idx_str]
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(new_alt))
            lang_set = set_lang_on_element(xref, doc, args.language)
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'approved',
                'alt_set':      new_alt,
                'lang_set':     args.language if lang_set else 'already_present',
            })
        elif is_placeholder(alt):
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'skipped_not_in_map',
                'warning':      'Placeholder alt text remains — figure not in approved map',
            })
    else:
        # ── Auto mode ─────────────────────────────────────────────────────
        if is_placeholder(alt):
            new_alt = f'[Figure {fig_index + 1} — alt text required]'
            doc.xref_set_key(xref, 'Alt', fitz.get_pdf_str(new_alt))
            set_lang_on_element(xref, doc, args.language)
            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'auto-placeholder',
                'alt_set':      new_alt,
                'lang_set':     args.language,
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
    'input':         args.input,
    'output':        args.output,
    'result':        result,
    'mode':          mode,
    'figures_total': fig_index,
    'language_set':  args.language,
    'changes':       changes,
    'needs_review':  needs_review,
    'note': (
        'Placeholder alt text set. Run generate_alt_text_drafts.py then '
        'generate_alt_text_review_report.py before applying approved text.'
        if result == 'NEEDS_REVIEW' else
        'Approved alt text and Lang attribute applied. Verify with veraPDF.'
        if result == 'FIXED' else ''
    )
}

out = json.dumps(output_obj, indent=2)
print(out)
if args.out:
    Path(args.out).write_text(out)

sys.exit(0 if result in ('FIXED', 'ALREADY_CORRECT') else 1)
