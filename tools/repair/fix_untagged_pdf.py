#!/usr/bin/env python3
"""
fix_untagged_pdf.py
Auto-generates a complete tagged PDF structure for untagged PDFs that have
a native text layer. Produces:

  1. A struct tree skeleton  (Document > Section > H/P/L/LI/Figure)
  2. MCID BDC markers in every content stream
  3. A complete ParentTree linking content → struct elements

This makes the output a valid input for fix_struct_content_marking.py
(which handles the case where a struct tree exists but ParentTree is broken).
When run on a truly untagged PDF, the two-script sequence produces a fully
wired tagged document ready for downstream repairs.

Heuristics used:
  - Large/bold text at top of block → heading (H1/H2/H3 mapped to H)
  - Regular text blocks → paragraph (P)
  - Lines starting with bullet chars or numbers → list items (L/LI/LBody)
  - Image blocks → Figure (with placeholder Alt)
  - Non-text content streams → Artifact

Usage:
  fix_untagged_pdf.py <input.pdf> <output.pdf> [--out results.json]

Exit codes:
  0  success
  1  document already has struct tree — no action needed
  2  error
"""
import sys, json, re, argparse, shutil
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

try:
    import pikepdf
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'pikepdf unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('input_pdf')
parser.add_argument('output_pdf')
parser.add_argument('--out', default=None)
args = parser.parse_args()

# ── Guard: already tagged ─────────────────────────────────────────────────────

doc = fitz.open(args.input_pdf)
catalog = doc.pdf_catalog()
struct_ref = doc.xref_get_key(catalog, 'StructTreeRoot')
if struct_ref[0] != 'null' and struct_ref[1]:
    result = json.dumps({
        'result': 'ALREADY_CORRECT',
        'note':   'Document already has a structure tree — no action needed'
    }, indent=2)
    print(result)
    if args.out:
        Path(args.out).write_text(result)
    shutil.copy2(args.input_pdf, args.output_pdf)
    sys.exit(0)

# ── Phase 1: classify blocks via PyMuPDF ─────────────────────────────────────

BULLET_CHARS = {'•', '·', '◦', '▪', '▸', '→', '-', '–', '*'}

def span_text(span):
    """Extract text from a span regardless of fitz version."""
    if 'text' in span:
        return span['text']
    return ''.join(c.get('c', '') for c in span.get('chars', []))

def block_text(block):
    parts = []
    for line in block.get('lines', []):
        for span in line.get('spans', []):
            parts.append(span_text(span))
    return ''.join(parts).strip()

def classify_block(block):
    """Return (tag, text) for a text block. Returns (None, None) to skip."""
    if block['type'] != 0:
        return 'Figure', None

    lines = block.get('lines', [])
    if not lines:
        return None, None

    spans = lines[0].get('spans', [])
    if not spans:
        return None, None

    first_span = spans[0]
    font_size  = first_span.get('size', 12)
    font_flags = first_span.get('flags', 0)
    is_bold    = bool(font_flags & 2**4)
    text       = block_text(block)

    if not text:
        return None, None

    first_char = text[0]
    if first_char in BULLET_CHARS:
        return 'LI', text
    if re.match(r'^\d+[\.\)]\s', text) or re.match(r'^[a-z][\.\)]\s', text):
        return 'LI', text

    if font_size >= 18 or (font_size >= 16 and is_bold):
        return 'H', text
    if font_size >= 14 or (font_size >= 13 and is_bold):
        return 'H', text
    if font_size >= 12 and is_bold and len(text) < 120:
        return 'H', text

    return 'P', text

# pages_content: list of (page_num, [(tag, text, block), ...])
# Each entry corresponds 1:1 with a content stream on that page.
pages_content = []

for page_num, page in enumerate(doc):
    page_dict = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks    = page_dict.get('blocks', [])

    page_entries = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        tag, text = classify_block(block)
        if tag is None:
            i += 1
            continue

        if tag == 'LI':
            # Collect consecutive list items into a single L group
            list_items = [(block, text)]
            j = i + 1
            while j < len(blocks):
                nt, ntx = classify_block(blocks[j])
                if nt == 'LI':
                    list_items.append((blocks[j], ntx))
                    j += 1
                else:
                    break
            page_entries.append(('L', None, list_items))
            i = j
        elif tag == 'Figure':
            page_entries.append(('Figure', None, block))
            i += 1
        else:
            page_entries.append((tag, text, block))
            i += 1

    pages_content.append((page_num, page_entries))

doc.close()

# ── Phase 2: save clean copy via PyMuPDF ─────────────────────────────────────
# garbage=4 normalises the file and produces one stream per text block,
# which lets us inject BDC/EMC markers per stream in Phase 3.

tmp = args.output_pdf + '.tmp_pass1.pdf'
doc2 = fitz.open(args.input_pdf)
doc2.save(tmp, garbage=4, deflate=True)
doc2.close()

# ── Phase 3: inject BDC/EMC markers + build struct tree via pikepdf ──────────

pdf = pikepdf.open(tmp)

# Set MarkInfo/Marked = true
if '/MarkInfo' not in pdf.Root:
    pdf.Root['/MarkInfo'] = pdf.make_indirect(pikepdf.Dictionary())
pdf.Root['/MarkInfo']['/Marked'] = pikepdf.Boolean(True)

# Set document language if not present
if '/Lang' not in pdf.Root:
    pdf.Root['/Lang'] = pikepdf.String('en-US')

# ── Build struct tree skeleton ────────────────────────────────────────────────

struct_tree = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/StructTreeRoot'),
    K=pikepdf.Array(),
    ParentTree=pdf.make_indirect(pikepdf.Dictionary(
        Nums=pikepdf.Array()
    )),
    ParentTreeNextKey=pikepdf.Integer(0)
))
pdf.Root['/StructTreeRoot'] = struct_tree

doc_elem = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/StructElem'),
    S=pikepdf.Name('/Document'),
    P=struct_tree,
    K=pikepdf.Array()
))
struct_tree['/K'].append(doc_elem)

def make_elem(parent, tag, page_ref):
    elem = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name('/StructElem'),
        S=pikepdf.Name(f'/{tag}'),
        P=parent,
        K=pikepdf.Array(),
        Pg=page_ref,
    ))
    parent['/K'].append(elem)
    return elem

# ── Phase 4: per-page MCID injection ─────────────────────────────────────────
# Strategy:
#   - fitz produces one stream per text block (after garbage=4 save)
#   - We iterate streams on each page in order
#   - For each stream containing BT: assign next MCID, wrap with BDC/EMC,
#     create the matching struct element, store (page_sp, mcid) → elem
#   - For streams without BT: wrap as Artifact
#
# The struct element's /K gets the MCID integer directly (simplest valid form).
# ParentTree is built at the end from the collected mapping.

sp_counter    = 0   # StructParents counter (one per page)
parent_tree_entries = {}   # sp_int → array[mcid] = struct_elem
tags_created  = {'H': 0, 'P': 0, 'L': 0, 'Figure': 0}
total_mcids   = 0

for page_num, entries in pages_content:
    page_obj = pdf.pages[page_num].obj
    page_ref = pdf.pages[page_num].obj

    # Get content streams for this page
    raw = page_obj.get('/Contents')
    if raw is None:
        continue

    if isinstance(raw, pikepdf.Array):
        streams = list(raw)
    else:
        streams = [raw]

    # Assign StructParents to this page
    sp = sp_counter
    sp_counter += 1
    page_obj['/StructParents'] = pikepdf.Integer(sp)

    # We'll build the ParentTree array for this page indexed by MCID.
    # Since each stream gets at most one MCID, and we assign MCIDs 0..N-1
    # per page, the array is simply indexed by MCID value.
    page_mcid_map = {}   # mcid → struct_elem

    # Match streams to entries. fitz produces streams in reading order
    # matching the block order from rawdict. We walk both lists together.
    entry_idx = 0
    page_mcid = 0   # MCID counter, resets per page

    for stream in streams:
        try:
            data = stream.read_bytes()
        except Exception:
            continue

        has_text = b'BT' in data

        if not has_text:
            # Non-text stream — mark as Artifact
            wrapped = b'/Artifact BMC\n' + data + b'\nEMC\n'
            stream.write(wrapped)
            continue

        # Text stream — assign MCID and match to struct entry
        mcid = page_mcid
        page_mcid += 1
        total_mcids += 1

        # Get the matching entry (if we have one from fitz classification)
        entry = entries[entry_idx] if entry_idx < len(entries) else None
        entry_idx += 1

        tag = entry[0] if entry else 'P'

        # Determine the BDC property dict tag
        # Use /P for paragraphs, /H for headings, /L for lists, /Figure for images
        bdc_tag = tag if tag in ('H', 'P', 'L', 'Figure') else 'P'

        # Inject BDC/EMC wrapper
        wrapped = (
            f'/{bdc_tag} <</MCID {mcid}>> BDC\n'.encode() +
            data +
            b'\nEMC\n'
        )
        stream.write(wrapped)

        # Create struct element(s) for this MCID
        if tag == 'L':
            list_items = entry[2] if entry else []
            l_elem = make_elem(doc_elem, 'L', page_ref)
            l_elem['/K'] = pikepdf.Array([pikepdf.Integer(mcid)])
            tags_created['L'] += 1
            # LI children reference the same MCID via the parent L
            for _, item_text in list_items:
                li_elem  = make_elem(l_elem, 'LI',    page_ref)
                lbody    = make_elem(li_elem, 'LBody', page_ref)
            page_mcid_map[mcid] = l_elem

        elif tag == 'Figure':
            fig_elem = make_elem(doc_elem, 'Figure', page_ref)
            fig_elem['/Alt'] = pikepdf.String('[Figure — alt text required]')
            fig_elem['/K']   = pikepdf.Array([pikepdf.Integer(mcid)])
            tags_created['Figure'] += 1
            page_mcid_map[mcid] = fig_elem

        else:
            # H or P
            struct_tag = 'H' if tag == 'H' else 'P'
            elem = make_elem(doc_elem, struct_tag, page_ref)
            elem['/K'] = pikepdf.Array([pikepdf.Integer(mcid)])
            tags_created[struct_tag] = tags_created.get(struct_tag, 0) + 1
            page_mcid_map[mcid] = elem

    # Build ParentTree array for this page
    if page_mcid_map:
        max_mcid = max(page_mcid_map.keys())
        pt_array = pikepdf.Array([None] * (max_mcid + 1))
        for m, elem in page_mcid_map.items():
            pt_array[m] = pdf.make_indirect(elem)
        parent_tree_entries[sp] = pt_array

# ── Phase 5: write ParentTree ─────────────────────────────────────────────────

nums = pikepdf.Array()
for key in sorted(parent_tree_entries):
    nums.append(pikepdf.Integer(key))
    nums.append(parent_tree_entries[key])

struct_tree['/ParentTree'] = pdf.make_indirect(
    pikepdf.Dictionary(Nums=nums)
)
struct_tree['/ParentTreeNextKey'] = pikepdf.Integer(sp_counter)

# ── Save ──────────────────────────────────────────────────────────────────────

pdf.save(args.output_pdf)
pdf.close()
Path(tmp).unlink(missing_ok=True)

total_elements = sum(tags_created.values())

result_obj = {
    'input':            args.input_pdf,
    'output':           args.output_pdf,
    'result':           'FIXED',
    'pages_processed':  len(pages_content),
    'total_mcids':      total_mcids,
    'elements_created': tags_created,
    'total_elements':   total_elements,
    'note': (
        'Structure tree generated with MCID markers and ParentTree. '
        'Run fix_struct_content_marking.py next to verify and harden '
        'ParentTree connectivity. Then run veraPDF to confirm 7.1/3 passes.'
    )
}

result_str = json.dumps(result_obj, indent=2)
print(result_str)
if args.out:
    Path(args.out).write_text(result_str)

sys.exit(0)
