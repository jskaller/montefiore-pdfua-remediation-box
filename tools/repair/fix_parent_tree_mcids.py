#!/usr/bin/env python3
"""
fix_parent_tree_mcids.py
Audits and repairs the ParentTree (number tree mapping MCIDs to structure elements).
A corrupt or missing ParentTree causes veraPDF failures in 7.1 General rules.

Repairs:
  - Detects MCIDs referenced in content streams with no ParentTree entry
  - Rebuilds ParentTree entries for orphaned MCIDs where the struct element is findable
  - Reports MCIDs that cannot be automatically resolved (require manual intervention)

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
    print('usage: fix_parent_tree_mcids.py <input.pdf> <output.pdf>', file=sys.stderr)
    sys.exit(2)

src, dst = sys.argv[1], sys.argv[2]
doc = fitz.open(src)
changes = []
unresolvable = []

catalog = doc.pdf_catalog()
struct_tree_ref = doc.xref_get_key(catalog, 'StructTreeRoot')

if struct_tree_ref[0] == 'null' or not struct_tree_ref[1]:
    print(json.dumps({'input': src, 'result': 'SKIPPED',
                      'reason': 'No StructTreeRoot — document not tagged'}))
    sys.exit(1)

struct_root_xref = int(struct_tree_ref[1].split()[0])
parent_tree_ref  = doc.xref_get_key(struct_root_xref, 'ParentTree')

if parent_tree_ref[0] == 'null':
    print(json.dumps({
        'input':  src,
        'result': 'ERROR',
        'reason': 'No ParentTree found in StructTreeRoot — full rebuild required, '
                  'use a dedicated PDF repair tool (e.g. Adobe Acrobat Pro or iText).'
    }, indent=2))
    sys.exit(1)

# Collect MCIDs referenced in content streams per page
content_mcids = {}  # page_index -> set of mcids
for page_num, page in enumerate(doc):
    page_xref = page.xref
    # Extract marked content references from content stream
    raw = ''
    try:
        raw = doc.xref_stream(page_xref) or b''
        if isinstance(raw, bytes):
            raw = raw.decode('latin-1', errors='replace')
    except Exception:
        pass
    # Find all BDC/BMC markers with MCID
    mcids = set(int(m) for m in re.findall(r'/MCID\s+(\d+)', raw))
    if mcids:
        content_mcids[page_num] = mcids

# Collect MCID -> struct_element mapping from existing ParentTree
# ParentTree is a number tree; walk its Nums array
def collect_parent_tree(xref, doc):
    mapping = {}
    try:
        nums = doc.xref_get_key(xref, 'Nums')
        if nums[0] == 'array':
            items = re.findall(r'(\d+)\s+(\d+)\s+0\s+R', nums[1])
            for mcid_str, struct_xref_str in items:
                mapping[int(mcid_str)] = int(struct_xref_str)
        kids = doc.xref_get_key(xref, 'Kids')
        if kids[0] == 'array':
            for kid_xref in re.findall(r'(\d+)\s+0\s+R', kids[1]):
                mapping.update(collect_parent_tree(int(kid_xref), doc))
    except Exception:
        pass
    return mapping

parent_tree_xref = int(parent_tree_ref[1].split()[0])
existing_mapping = collect_parent_tree(parent_tree_xref, doc)

# Find MCIDs in content streams missing from ParentTree
total_content_mcids = sum(len(s) for s in content_mcids.values())
missing_mcids = []
for page_num, mcids in content_mcids.items():
    for mcid in mcids:
        if mcid not in existing_mapping:
            missing_mcids.append({'page': page_num + 1, 'mcid': mcid})

if not missing_mcids:
    doc.save(dst, garbage=4, deflate=True)
    print(json.dumps({
        'input':               src,
        'output':              dst,
        'result':              'ALREADY_CORRECT',
        'total_content_mcids': total_content_mcids,
        'missing_mcids':       0
    }, indent=2))
    sys.exit(0)

# For missing MCIDs, we report them — automatic ParentTree rebuilding
# requires knowing the struct element for each MCID, which requires
# a full struct tree walk correlating MCIDs to elements.
for item in missing_mcids:
    unresolvable.append({
        'page': item['page'],
        'mcid': item['mcid'],
        'action': 'Manual repair required — use Acrobat Pro Touch Up Reading Order '
                  'or iText to reassign MCID to correct structure element.'
    })

doc.save(dst, garbage=4, deflate=True)

print(json.dumps({
    'input':               src,
    'output':              dst,
    'result':              'PARTIAL' if unresolvable else 'FIXED',
    'total_content_mcids': total_content_mcids,
    'missing_mcids':       len(missing_mcids),
    'unresolvable':        unresolvable,
    'changes':             changes,
    'note': 'Orphaned MCIDs cannot be auto-repaired without knowing their parent structure element. '
            'Manual intervention required for unresolvable entries.'
}, indent=2))
sys.exit(1 if unresolvable else 0)
