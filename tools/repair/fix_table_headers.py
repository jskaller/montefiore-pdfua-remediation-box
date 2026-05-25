#!/usr/bin/env python3
"""
fix_table_headers.py
Repairs table header structure elements for PDF/UA-1 compliance:
  - Ensures TH cells have a Scope attribute (Column, Row, or Both)

Scope on TH is required by PDF/UA-1 (ISO 14289-1) and checked by veraPDF.
The Table Summary attribute is NOT required by PDF/UA-1 and is not written.

Rerun veraPDF PDF/UA (7.5 Tables) after applying.
"""
import sys, json, re
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 3:
    print('usage: fix_table_headers.py <input.pdf> <output.pdf>', file=sys.stderr)
    sys.exit(2)

src, dst = sys.argv[1], sys.argv[2]
doc = fitz.open(src)
changes = []

catalog = doc.pdf_catalog()
struct_tree_ref = doc.xref_get_key(catalog, 'StructTreeRoot')

if struct_tree_ref[0] == 'null' or not struct_tree_ref[1]:
    print(json.dumps({'input': src, 'result': 'SKIPPED',
                      'reason': 'No StructTreeRoot — document not tagged'}))
    sys.exit(1)

def get_kids_xrefs(xref, doc):
    kids = doc.xref_get_key(xref, 'K')
    if kids[0] == 'array':
        return [int(r) for r in re.findall(r'(\d+)\s+0\s+R', kids[1])]
    elif kids[0] == 'xref':
        return [int(kids[1].split()[0])]
    return []

def walk_for_type(xref, doc, target_types):
    try:
        s_type = doc.xref_get_key(xref, 'S')
        clean  = s_type[1].strip('/').strip() if s_type[0] != 'null' else ''
        if clean in target_types:
            yield xref, clean
        for kid_xref in get_kids_xrefs(xref, doc):
            yield from walk_for_type(kid_xref, doc, target_types)
    except Exception:
        return

struct_root_xref = int(struct_tree_ref[1].split()[0])

th_count      = 0
th_fixed_scope = 0

for xref, s_type in walk_for_type(struct_root_xref, doc, {'TH'}):
    th_count += 1
    attrs = doc.xref_get_key(xref, 'A')
    scope_present = attrs[0] != 'null' and 'Scope' in attrs[1]

    if not scope_present:
        attr_xref = doc.xref_get_key(xref, 'A')
        if attr_xref[0] == 'null':
            # Create attribute dictionary directly (no xref_append)
            doc.xref_set_key(xref, 'A', '<< /O /Table /Scope /Column >>')
        else:
            existing_attr_xref = int(attr_xref[1].split()[0])
            doc.xref_set_key(existing_attr_xref, 'Scope', '/Column')
        th_fixed_scope += 1
        changes.append({'type': 'TH_scope', 'xref': xref, 'set': 'Column'})

doc.save(dst, garbage=4, deflate=True)
result = 'FIXED' if changes else 'ALREADY_CORRECT'

print(json.dumps({
    'input':          src,
    'output':         dst,
    'result':         result,
    'th_cells_found': th_count,
    'th_scope_fixed': th_fixed_scope,
    'changes':        changes
}, indent=2))
sys.exit(0)
