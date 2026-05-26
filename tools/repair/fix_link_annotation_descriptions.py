#!/usr/bin/env python3
"""
fix_link_annotation_descriptions.py
Fixes Link annotations to satisfy PDF/UA-1 7.18.1, 7.18.5/1, and 7.18.5/2:
  - 7.18.5/2: Link must have /Contents key with alternate description
  - 7.18.1/2: Same — annotation must have Contents OR enclosing Alt
  - 7.18.5/1: Link must be tagged with /Link struct element

For each Link annotation missing Contents:
  - Derive description from URI (preferring readable URL or mailto address)
  - Fall back to visible text in annotation rect
  - Fall back to generic placeholder

For struct tree tagging:
  - For each link without /StructParent, create a /Link struct element
    containing an /OBJR child that references the annotation
  - Append /Link element under the Document or appropriate parent in the
    struct tree, then register the annotation in ParentTree at a new key
  - Set /StructParent on the annotation to point to its ParentTree entry

This script uses pikepdf throughout (no PyMuPDF) because PyMuPDF's
page.annots() iterator misses annotations in some PDFs produced by
fix_untagged_pdf.py's pikepdf save.

Usage:
  fix_link_annotation_descriptions.py <input.pdf> <output.pdf>
"""
import sys, json, re, urllib.parse
from pathlib import Path

try:
    import pikepdf
    from pikepdf import Name, Dictionary, Array, Integer, String
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': 'pikepdf unavailable: %s' % e}))
    sys.exit(2)

if len(sys.argv) < 3:
    print('usage: fix_link_annotation_descriptions.py <input.pdf> <output.pdf>',
          file=sys.stderr)
    sys.exit(2)

src, dst = sys.argv[1], sys.argv[2]

def derive_description(uri_str):
    """Build a human-friendly Contents value from a URI string."""
    if not uri_str:
        return None
    s = str(uri_str)
    # mailto:foo%40bar.org?subject=... → "Email foo@bar.org"
    if s.lower().startswith('mailto:'):
        rest = s[len('mailto:'):]
        # strip query
        if '?' in rest:
            rest = rest.split('?', 1)[0]
        try:
            decoded = urllib.parse.unquote(rest)
        except Exception:
            decoded = rest
        return 'Email %s' % decoded
    # http(s) URL → use the URL itself for clarity
    if s.lower().startswith(('http://', 'https://')):
        try:
            decoded = urllib.parse.unquote(s)
        except Exception:
            decoded = s
        return 'Link: %s' % decoded
    # Other schemes
    return 'Link: %s' % s


def get_or_create_struct_tree_root(pdf):
    """Return the StructTreeRoot, creating it if missing."""
    root = pdf.Root
    sroot = root.get('/StructTreeRoot')
    if sroot is None:
        sroot = pdf.make_indirect(Dictionary(
            Type=Name('/StructTreeRoot'),
            K=Array(),
            ParentTree=pdf.make_indirect(Dictionary(Nums=Array())),
            ParentTreeNextKey=Integer(0)
        ))
        root['/StructTreeRoot'] = sroot
    return sroot


def get_or_create_root_parent(pdf, sroot):
    """
    Find a sensible parent struct element under which to add /Link elements.
    Prefer the first Document child; otherwise the first child of any kind;
    otherwise create a Document element.
    """
    k = sroot.get('/K')
    if k is None or (isinstance(k, Array) and len(k) == 0):
        # No children — create a Document element
        doc_elem = pdf.make_indirect(Dictionary(
            Type=Name('/StructElem'),
            S=Name('/Document'),
            P=sroot,
            K=Array()
        ))
        if k is None:
            sroot['/K'] = Array([doc_elem])
        else:
            k.append(doc_elem)
        return doc_elem

    # Look for a Document child
    children = k if isinstance(k, Array) else [k]
    for child_ref in children:
        try:
            child = pdf.get_object(child_ref.objgen) if hasattr(child_ref, 'objgen') else child_ref
        except Exception:
            continue
        if isinstance(child, Dictionary) and str(child.get('/S', '')) == '/Document':
            return child
    # Fallback: first child of any type
    first = children[0]
    try:
        return pdf.get_object(first.objgen) if hasattr(first, 'objgen') else first
    except Exception:
        return first


def get_next_parent_tree_key(sroot):
    """Get next available ParentTree key and increment."""
    next_key = sroot.get('/ParentTreeNextKey')
    if next_key is None:
        next_key = 0
    else:
        try:
            next_key = int(next_key)
        except Exception:
            next_key = 0
    sroot['/ParentTreeNextKey'] = Integer(next_key + 1)
    return next_key


def add_to_parent_tree(pdf, sroot, key, value):
    """Add an entry to the ParentTree's /Nums flat array."""
    pt = sroot.get('/ParentTree')
    if pt is None:
        pt = pdf.make_indirect(Dictionary(Nums=Array()))
        sroot['/ParentTree'] = pt
    nums = pt.get('/Nums')
    if nums is None:
        nums = Array()
        pt['/Nums'] = nums
    nums.append(Integer(key))
    nums.append(value)


def main():
    pdf = pikepdf.open(src)

    changes = []
    needs_review = []
    links_with_contents = 0
    links_tagged = 0

    sroot = get_or_create_struct_tree_root(pdf)
    root_parent = get_or_create_root_parent(pdf, sroot)

    for page_num, page in enumerate(pdf.pages):
        page_obj = page.obj
        annots_ref = page_obj.get('/Annots')
        if annots_ref is None:
            continue

        # Normalise to an Array of annotation dictionaries (resolving indirects)
        annots = annots_ref if isinstance(annots_ref, Array) else Array([annots_ref])

        for annot_idx, annot_ref in enumerate(annots):
            try:
                annot = pdf.get_object(annot_ref.objgen) if hasattr(annot_ref, 'objgen') else annot_ref
            except Exception:
                continue
            if not isinstance(annot, Dictionary):
                continue
            if str(annot.get('/Subtype', '')) != '/Link':
                continue

            # ── (1) Ensure /Contents ─────────────────────────────────────
            existing = annot.get('/Contents')
            has_contents = existing is not None and str(existing).strip()
            if not has_contents:
                # Derive from URI
                desc = None
                a_dict = annot.get('/A')
                if a_dict is not None:
                    uri = a_dict.get('/URI')
                    if uri is not None:
                        desc = derive_description(uri)
                if not desc:
                    desc = '[Link on page %d — description required]' % (page_num + 1)
                    needs_review.append({
                        'page': page_num + 1,
                        'note': 'No URI found — placeholder set, review required'
                    })
                annot['/Contents'] = String(desc)
                changes.append({
                    'page': page_num + 1,
                    'annot_idx': annot_idx,
                    'set_to': desc[:80] + ('...' if len(desc) > 80 else '')
                })
            else:
                links_with_contents += 1

            # ── (2) Ensure /Link struct element with /OBJR ──────────────
            struct_parent = annot.get('/StructParent')
            if struct_parent is None:
                # Create /Link struct element with /OBJR child
                link_elem = pdf.make_indirect(Dictionary(
                    Type=Name('/StructElem'),
                    S=Name('/Link'),
                    P=root_parent,
                    Pg=page_obj,
                    K=Array(),
                ))
                # /OBJR child references the annotation
                objr = Dictionary(
                    Type=Name('/OBJR'),
                    Obj=annot,
                    Pg=page_obj,
                )
                link_elem['/K'].append(objr)

                # Append link_elem under root_parent
                rp_k = root_parent.get('/K')
                if rp_k is None:
                    root_parent['/K'] = Array([link_elem])
                elif isinstance(rp_k, Array):
                    rp_k.append(link_elem)
                else:
                    root_parent['/K'] = Array([rp_k, link_elem])

                # Add to ParentTree
                key = get_next_parent_tree_key(sroot)
                add_to_parent_tree(pdf, sroot, key, link_elem)
                annot['/StructParent'] = Integer(key)
                links_tagged += 1

    pdf.save(dst)
    pdf.close()

    result = 'FIXED' if (changes or links_tagged) else 'ALREADY_CORRECT'

    print(json.dumps({
        'input': src,
        'output': dst,
        'result': result,
        'links_fixed_contents': len(changes),
        'links_already_had_contents': links_with_contents,
        'links_tagged_in_struct_tree': links_tagged,
        'needs_review': needs_review,
        'changes': changes,
    }, indent=2))
    sys.exit(0)


if __name__ == '__main__':
    main()
