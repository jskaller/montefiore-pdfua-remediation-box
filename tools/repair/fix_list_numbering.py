[init] Validation profiles already present — skipping clone.
[init] Copied AGENTS.md to workspace.
[init] Copied SOUL.md to workspace.
[init] Copied IDENTITY.md to workspace.
[init] Copied TOOLS.md to workspace.
[init] Starting: cat /app/tools/repair/fix_list_numbering.py
#!/usr/bin/env python3
"""
fix_list_numbering.py
Repairs list structure elements:
  - Ensures L elements contain only LI children
  - Ensures LI elements contain Lbl and LBody children
  - Adds missing ListNumbering attribute to L elements
Rerun veraPDF PDF/UA after applying.
"""
import sys, json, re
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

if len(sys.argv) < 3:
    print('usage: fix_list_numbering.py <input.pdf> <output.pdf>', file=sys.stderr)
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

def get_type(xref, doc):
    s = doc.xref_get_key(xref, 'S')
    return s[1].strip('/').strip() if s[0] != 'null' else ''

def walk_lists(xref, doc):
    try:
        t = get_type(xref, doc)
        if t == 'L':
            yield xref
        for kid in get_kids_xrefs(xref, doc):
            yield from walk_lists(kid, doc)
    except Exception:
        return

struct_root_xref = int(struct_tree_ref[1].split()[0])

for l_xref in walk_lists(struct_root_xref, doc):
    # Check for ListNumbering attribute
    attrs = doc.xref_get_key(l_xref, 'A')
    has_numbering = attrs[0] != 'null' and 'ListNumbering' in attrs[1]

    if not has_numbering:
        # Detect if list is ordered by inspecting Lbl text of first LI
        list_type = 'Unordered'
        for kid_xref in get_kids_xrefs(l_xref, doc):
            if get_type(kid_xref, doc) == 'LI':
                for li_kid in get_kids_xrefs(kid_xref, doc):
                    if get_type(li_kid, doc) == 'Lbl':
                        # Peek at actual content text via MCIDs would be ideal;
                        # default safe choice is Unordered unless we have evidence
                        pass
                break

        if attrs[0] == 'null':
            # Create attribute dictionary as direct object (no need for xref_append)
            doc.xref_set_key(l_xref, 'A', f'<< /O /List /ListNumbering /{list_type} >>')
        else:
            existing_xref = int(attrs[1].split()[0])
            doc.xref_set_key(existing_xref, 'ListNumbering', f'/{list_type}')

        changes.append({'xref': l_xref, 'set': f'ListNumbering={list_type}'})

if changes:
    doc.save(dst, garbage=4, deflate=True)
    result = 'FIXED'
else:
    doc.save(dst, garbage=4, deflate=True)
    result = 'ALREADY_CORRECT'

print(json.dumps({
    'input':   src,
    'output':  dst,
    'result':  result,
    'changes': changes,
    'note': 'ListNumbering defaults to Unordered. Review ordered lists and correct manually if needed.'
            if any('Unordered' in str(c) for c in changes) else ''
}, indent=2))
sys.exit(0)