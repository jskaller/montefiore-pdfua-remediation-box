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
            elif entry.get('alt_text'):
                alt_map[str(idx_str)] = entry['alt_text']
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

# ── Page resolution helpers ───────────────────────────────────────────────────

def _build_page_xref_map(doc):
    """Return dict mapping page-object xref -> 0-based page index."""
    m = {}
    for i in range(len(doc)):
        try:
            m[doc.page_xref(i)] = i
        except Exception:
            pass
    return m

_page_xref_map = _build_page_xref_map(doc)

def _xref_int_from_ref(ref_str: str):
    """Parse '42 0 R' -> 42, or a bare integer string -> int. Returns None on failure."""
    # Indirect reference: '42 0 R'
    m = re.match(r'^\s*(\d+)\s+0\s+R\s*$', ref_str.strip())
    if m:
        return int(m.group(1))
    # Some PyMuPDF versions return a bare integer for type 'int' / 'xref'
    m = re.match(r'^\s*(\d+)\s*$', ref_str.strip())
    if m:
        return int(m.group(1))
    return None

def _page_num_from_pg(struct_xref: int) -> int | None:
    """
    Try /Pg on the struct element itself.
    Returns 0-based page index, or None if /Pg absent or unresolvable.
    Handles PyMuPDF type strings: 'xref', 'ref', 'indirect', and bare 'int'.
    """
    try:
        pg = doc.xref_get_key(struct_xref, 'Pg')
        # Accept all indirect-reference type labels across PyMuPDF versions,
        # plus bare integer (some versions return the xref number as type 'int').
        if pg[0] in ('xref', 'ref', 'indirect', 'int'):
            page_xref = _xref_int_from_ref(pg[1])
            if page_xref is not None and page_xref in _page_xref_map:
                return _page_xref_map[page_xref]
    except Exception:
        pass
    return None

def _mcids_from_struct(struct_xref: int) -> list[int]:
    """
    Collect MCID integer values directly under a struct element's /K array.
    Handles: single inline MCID (type 'int'), mixed arrays containing bare
    integers and/or MCID dicts (<</Type /MCR /MCID N ...>>).
    Returns deduplicated list (may be empty).
    """
    mcids = []
    try:
        kids = doc.xref_get_key(struct_xref, 'K')
        if kids[0] == 'int':
            # Single inline MCID
            mcids.append(int(kids[1]))
        elif kids[0] in ('array', 'dict'):
            raw = kids[1]
            # Extract explicit /MCID dict entries first (most reliable)
            for mcid_val in re.findall(r'/MCID\s+(\d+)', raw):
                mcids.append(int(mcid_val))
            if kids[0] == 'array' and not mcids:
                # No MCID dicts found; look for bare integers in the array
                # that are NOT part of an indirect reference (N 0 R).
                # Match whole tokens: digit sequence not preceded or followed
                # by other digits, and not immediately followed by ' 0 R'.
                for m in re.finditer(r'\b(\d+)\b', raw):
                    token = m.group(1)
                    after = raw[m.end():]
                    if not re.match(r'\s+0\s+R', after):
                        mcids.append(int(token))
    except Exception:
        pass
    return list(set(mcids))

def _page_num_from_mcid_walk(struct_xref: int) -> int | None:
    """
    Walk the struct element's kids to find a MCID, then scan every page's
    marked-content sequences to find which page owns that MCID.
    Returns 0-based page index, or None if not found.
    This is the fallback when /Pg is absent on the struct element.
    """
    mcids = _mcids_from_struct(struct_xref)
    if not mcids:
        return None

    target_mcids = set(mcids)

    for page_num in range(len(doc)):
        try:
            page = doc[page_num]
            # get_text('rawdict') includes mcid in block metadata for image blocks
            blocks = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE).get('blocks', [])
            for block in blocks:
                if block.get('type') == 1 and block.get('mcid') in target_mcids:
                    return page_num
            # Scan raw content stream for BDC markers — covers figures that
            # don't surface as image blocks in get_text (e.g. vector graphics,
            # form XObjects tagged at the content-stream level).
            content = page.read_contents().decode('latin-1', errors='replace')
            for mcid in target_mcids:
                if re.search(r'/MCID\s+' + str(mcid) + r'\b', content):
                    return page_num
        except Exception:
            continue

    return None

def resolve_page_num(struct_xref: int) -> tuple[int, str]:
    """
    Return (page_num, resolution_label) for a Figure struct element.
    Strategy 1: /Pg attribute on the struct element (O(1), most reliable).
    Strategy 2: Walk kids to extract MCIDs, scan page content streams.
    Fallback:   page 0 with label 'fallback' — caller should log a warning.

    Returns a tuple so the caller never needs to invoke the helpers a second
    time just to determine which strategy succeeded.
    """
    page_num = _page_num_from_pg(struct_xref)
    if page_num is not None:
        return page_num, 'pg_attr'

    page_num = _page_num_from_mcid_walk(struct_xref)
    if page_num is not None:
        return page_num, 'mcid_walk'

    return 0, 'fallback'

# ── Main struct tree walk ─────────────────────────────────────────────────────

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

            # resolve_page_num returns (page_num, label) in one call —
            # no redundant re-invocation of the expensive MCID walk.
            page_num, page_resolution = resolve_page_num(xref)

            changes.append({
                'xref':         xref,
                'figure_index': fig_index,
                'mode':         'auto-placeholder',
                'alt_set':      new_alt,
                'lang_set':     args.language,
            })
            needs_review.append({
                'struct_xref':     xref,           # struct element xref (for reference only)
                'figure_index':    fig_index,
                'page_num':        page_num,        # 0-based; used by generate_alt_text_drafts.py
                'page_resolution': page_resolution, # 'pg_attr' | 'mcid_walk' | 'fallback'
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
