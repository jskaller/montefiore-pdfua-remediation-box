#!/usr/bin/env python3
"""
fix_struct_content_marking.py
─────────────────────────────
Montefiore PDF/UA Remediation — Repair Library
Fixes: PDF/UA-1 clause 7.1 / test 3
       "Content is neither marked as Artifact nor tagged as real content."

Stage 2 of the two-script untagged-PDF sequence:
  1. fix_untagged_pdf.py      — builds a hollow struct tree skeleton
  2. fix_struct_content_marking.py (THIS)
                              — wires the struct tree to content streams
                                via MCID marking operators + correct
                                ParentTree / StructParents back-links

What this script does
─────────────────────
1.  Walk every page's content stream and locate all BDC operators that
    carry an MCID operand (written by fix_untagged_pdf.py).
    Collect: page_index → list[mcid]

2.  Walk the struct tree and collect every leaf element that references
    an MCID (via its /MCID entry or /K dict containing /MCID).
    Collect: (page_index, mcid) → struct-element object-ref

3.  For each page that has MCIDs:
    a. Assign a unique /StructParents integer to the page's /Page dict
       if not already present.
    b. Build a ParentTree entry for that StructParents number: an array
       whose i-th slot holds the indirect reference to the struct element
       owning MCID i on that page.

4.  Write the completed /ParentTree (a number tree) into
    StructTreeRoot /ParentTree.

5.  Ensure /ParentTreeNextKey is set correctly.

6.  Tag any remaining untagged content with /Artifact BDC markers so
    veraPDF doesn't see bare operators.

Invocation
──────────
    python3 fix_struct_content_marking.py <input.pdf> <output.pdf> [--verbose]

Exit codes
──────────
    0  — success
    1  — input not found / not a PDF
    2  — no struct tree present (run fix_untagged_pdf.py first)
    3  — pikepdf error
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import pikepdf


# ── helpers ──────────────────────────────────────────────────────────────────

def _tokens(stream_bytes: bytes):
    """Very small PDF content-stream tokeniser — yields (kind, value) pairs.
    kind ∈ {'name','int','real','str','op','ws'}
    """
    i = 0
    n = len(stream_bytes)
    while i < n:
        c = stream_bytes[i:i+1]
        if c in (b' ', b'\t', b'\r', b'\n', b'\x0c'):
            i += 1
            continue
        if c == b'%':                       # comment
            while i < n and stream_bytes[i:i+1] not in (b'\r', b'\n'):
                i += 1
            continue
        if c == b'/':                       # name
            j = i + 1
            while j < n and stream_bytes[j:j+1] not in (
                b' ', b'\t', b'\r', b'\n', b'\x0c',
                b'/', b'<', b'>', b'[', b']', b'(', b')', b'%'
            ):
                j += 1
            yield ('name', stream_bytes[i+1:j].decode('latin-1'))
            i = j
            continue
        if c == b'(':                       # literal string — skip
            depth, j = 1, i + 1
            while j < n and depth:
                ch = stream_bytes[j:j+1]
                if ch == b'\\':
                    j += 2
                elif ch == b'(':
                    depth += 1; j += 1
                elif ch == b')':
                    depth -= 1; j += 1
                else:
                    j += 1
            yield ('str', stream_bytes[i:j])
            i = j
            continue
        if c == b'<':                       # hex string or dict delimiter
            if stream_bytes[i:i+2] == b'<<':
                yield ('op', '<<'); i += 2; continue
            j = i + 1
            while j < n and stream_bytes[j:j+1] != b'>':
                j += 1
            yield ('str', stream_bytes[i:j+1]); i = j + 1
            continue
        if c == b'>':
            if stream_bytes[i:i+2] == b'>>':
                yield ('op', '>>'); i += 2; continue
            yield ('op', '>'); i += 1; continue
        if c in (b'[', b']'):
            yield ('op', c.decode()); i += 1; continue
        # number or operator
        j = i
        while j < n and stream_bytes[j:j+1] not in (
            b' ', b'\t', b'\r', b'\n', b'\x0c',
            b'/', b'<', b'>', b'[', b']', b'(', b')'
        ):
            j += 1
        token = stream_bytes[i:j].decode('latin-1')
        # classify
        try:
            int(token); yield ('int', token); i = j; continue
        except ValueError:
            pass
        try:
            float(token); yield ('real', token); i = j; continue
        except ValueError:
            pass
        yield ('op', token)
        i = j


def extract_page_mcids(page) -> list[int]:
    """Return sorted list of MCIDs found in BDC/BMC operators on this page."""
    mcids = []
    try:
        raw = page.obj.get('/Contents')
        if raw is None:
            return mcids
        # normalise to list of streams
        streams = []
        if isinstance(raw, pikepdf.Array):
            for ref in raw:
                streams.append(pdf.get_object(ref.objgen))
        else:
            streams.append(pdf.get_object(raw.objgen) if hasattr(raw, 'objgen') else raw)

        for stream in streams:
            try:
                data = stream.read_bytes()
            except Exception:
                continue
            tokens = list(_tokens(data))
            for idx, (kind, val) in enumerate(tokens):
                # looking for: /MCID <int>  anywhere inside a BDC property dict
                if kind == 'name' and val == 'MCID':
                    # next meaningful token should be the integer
                    if idx + 1 < len(tokens) and tokens[idx+1][0] == 'int':
                        mcids.append(int(tokens[idx+1][1]))
    except Exception:
        pass
    return sorted(set(mcids))


def collect_struct_mcid_map(pdf, verbose=False):
    """
    Walk the struct tree and build:
        (page_obj_ref, mcid) → struct_elem_objref

    Returns dict[(page_number_str, mcid_int)] = pikepdf.ObjectHelper ref
    We use page_number (0-based) as key since page ObjRef may not yet be
    stable in some PDFs.
    """
    try:
        root = pdf.Root
        sroot = root.get('/StructTreeRoot')
        if sroot is None:
            return {}
    except Exception:
        return {}

    mapping = {}   # (page_idx_int, mcid_int) → pikepdf indirect ref to struct elem

    def walk(elem, page_override=None):
        if not isinstance(elem, pikepdf.Dictionary):
            try:
                elem = pdf.get_object(elem.objgen)
            except Exception:
                return
        # determine which page this element belongs to
        pg = elem.get('/Pg') or page_override
        # /K can be: integer (MCID), dict, array, or absent
        k = elem.get('/K')
        if k is None:
            return
        _process_k(k, elem, pg)

    def _process_k(k, parent_elem, pg):
        if isinstance(k, pikepdf.Array):
            for item in k:
                _process_k(item, parent_elem, pg)
        elif isinstance(k, pikepdf.Dictionary):
            # marked content reference dict: {/Type /MCR, /Pg, /MCID}
            t = k.get('/Type')
            if t is not None and str(t) == '/MCR':
                mcid_val = k.get('/MCID')
                mcr_pg   = k.get('/Pg') or pg
                if mcid_val is not None and mcr_pg is not None:
                    page_idx = _page_idx(mcr_pg)
                    if page_idx is not None:
                        mapping[(page_idx, int(mcid_val))] = parent_elem
            else:
                # nested struct element
                walk(k, pg)
        elif isinstance(k, pikepdf.Object):
            # may be an MCID integer directly on the element's own page
            try:
                iv = int(k)
                if pg is not None:
                    page_idx = _page_idx(pg)
                    if page_idx is not None:
                        mapping[(page_idx, iv)] = parent_elem
            except (TypeError, ValueError):
                # indirect ref to child struct element
                try:
                    child = pdf.get_object(k.objgen)
                    walk(child, pg)
                except Exception:
                    pass

    # build page objref → index map
    _page_ref_to_idx = {}
    for i, p in enumerate(pdf.pages):
        try:
            _page_ref_to_idx[p.obj.objgen] = i
        except Exception:
            pass

    def _page_idx(pg_ref):
        try:
            return _page_ref_to_idx.get(pg_ref.objgen)
        except Exception:
            return None

    # start walk from /K of StructTreeRoot
    try:
        k = sroot.get('/K')
        if k is None:
            return mapping
        if isinstance(k, pikepdf.Array):
            for child in k:
                try:
                    child_obj = pdf.get_object(child.objgen)
                    walk(child_obj)
                except Exception:
                    pass
        else:
            try:
                child_obj = pdf.get_object(k.objgen)
                walk(child_obj)
            except Exception:
                pass
    except Exception as exc:
        if verbose:
            print(f"  [warn] struct walk error: {exc}")

    return mapping


def build_number_tree(entries: dict[int, pikepdf.Object]) -> pikepdf.Dictionary:
    """Build a flat /Nums number tree dictionary from {int: object} mapping."""
    nums = pikepdf.Array()
    for key in sorted(entries):
        nums.append(pikepdf.Integer(key))
        nums.append(entries[key])
    return pikepdf.Dictionary(Nums=nums)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Fix PDF/UA-1 7.1/3: ParentTree/MCID connectivity'
    )
    ap.add_argument('input',  help='Input PDF (from fix_untagged_pdf.py)')
    ap.add_argument('output', help='Output PDF path')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    if not inp.exists():
        print(f'ERROR: input not found: {inp}', file=sys.stderr)
        sys.exit(1)

    v = args.verbose

    # ── open ──────────────────────────────────────────────────────────────
    try:
        pdf = pikepdf.open(inp)
    except pikepdf.PdfError as e:
        print(f'ERROR: pikepdf cannot open {inp}: {e}', file=sys.stderr)
        sys.exit(3)

    root = pdf.Root

    # ── guard: struct tree must exist ─────────────────────────────────────
    sroot = root.get('/StructTreeRoot')
    if sroot is None:
        print(
            'ERROR: no /StructTreeRoot — run fix_untagged_pdf.py first',
            file=sys.stderr
        )
        sys.exit(2)

    # ── make pdf accessible inside helpers ────────────────────────────────
    # (collect_struct_mcid_map needs it as a closure; pass explicitly instead)

    # ── 1. collect MCIDs per page from content streams ────────────────────
    print('Phase 1: scanning content streams for BDC/MCID markers …')
    page_mcids: dict[int, list[int]] = {}   # page_idx → [mcid, …]
    for page_idx, page in enumerate(pdf.pages):
        mcids = extract_page_mcids_with_pdf(pdf, page, v)
        if mcids:
            page_mcids[page_idx] = mcids
            if v:
                print(f'  page {page_idx}: MCIDs {mcids}')

    total_mcids = sum(len(v2) for v2 in page_mcids.values())
    print(f'  found {total_mcids} MCIDs across {len(page_mcids)} pages')

    # ── 2. collect struct-element map ────────────────────────────────────
    print('Phase 2: walking struct tree …')
    struct_map = collect_struct_mcid_map_with_pdf(pdf, v)
    print(f'  struct tree covers {len(struct_map)} (page,mcid) pairs')

    # ── 3. assign /StructParents to pages ─────────────────────────────────
    print('Phase 3: assigning /StructParents to pages …')
    page_to_sp: dict[int, int] = {}   # page_idx → StructParents int
    sp_counter = 0
    # recycle any existing StructParents values to avoid gaps
    for page_idx, page in enumerate(pdf.pages):
        existing = page.obj.get('/StructParents')
        if existing is not None:
            try:
                page_to_sp[page_idx] = int(existing)
                sp_counter = max(sp_counter, int(existing) + 1)
            except Exception:
                pass

    for page_idx in sorted(page_mcids.keys()):
        if page_idx not in page_to_sp:
            page_to_sp[page_idx] = sp_counter
            sp_counter += 1
        pdf.pages[page_idx].obj['/StructParents'] = pikepdf.Integer(page_to_sp[page_idx])
        if v:
            print(f'  page {page_idx} → /StructParents {page_to_sp[page_idx]}')

    # ── 4. build ParentTree entries ───────────────────────────────────────
    print('Phase 4: building ParentTree …')
    parent_tree_entries: dict[int, pikepdf.Object] = {}

    for page_idx, mcids in page_mcids.items():
        sp = page_to_sp.get(page_idx)
        if sp is None:
            continue
        # build array: slot[mcid] = indirect ref to owning struct element
        max_mcid = max(mcids)
        arr = pikepdf.Array([None] * (max_mcid + 1))
        for mcid in mcids:
            key = (page_idx, mcid)
            elem = struct_map.get(key)
            if elem is None:
                if v:
                    print(f'  [warn] no struct elem for page {page_idx} MCID {mcid}')
                continue
            # make sure the struct element is an indirect object
            try:
                obj_ref = pdf.make_indirect(elem)
                arr[mcid] = obj_ref
            except Exception as exc:
                if v:
                    print(f'  [warn] could not make indirect for ({page_idx},{mcid}): {exc}')
        parent_tree_entries[sp] = arr

    # ── 5. write ParentTree into StructTreeRoot ───────────────────────────
    print('Phase 5: writing ParentTree …')
    pt_dict = build_number_tree(parent_tree_entries)
    pt_indirect = pdf.make_indirect(pt_dict)
    sroot['/ParentTree'] = pt_indirect
    sroot['/ParentTreeNextKey'] = pikepdf.Integer(sp_counter)

    # ── 6. mark any bare content as Artifact ──────────────────────────────
    # Pages that have no MCIDs at all must have their content streams fully
    # wrapped as Artifacts so veraPDF doesn't see bare operators.
    print('Phase 6: wrapping untagged pages as Artifact …')
    artifact_pages = 0
    for page_idx, page in enumerate(pdf.pages):
        if page_idx in page_mcids:
            continue   # already marked
        try:
            _wrap_page_as_artifact(pdf, page, v)
            artifact_pages += 1
        except Exception as exc:
            if v:
                print(f'  [warn] artifact wrap failed page {page_idx}: {exc}')

    if artifact_pages:
        print(f'  wrapped {artifact_pages} pages as Artifact')

    # ── 7. ensure MarkInfo ────────────────────────────────────────────────
    if root.get('/MarkInfo') is None:
        root['/MarkInfo'] = pikepdf.Dictionary(Marked=True)
    else:
        root['/MarkInfo']['/Marked'] = True

    # ── save ──────────────────────────────────────────────────────────────
    print(f'Saving → {out} …')
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(out)
    pdf.close()
    print('Done. ✓')
    print()
    print('Next step: run veraPDF to check clause 7.1/3:')
    print(f'  verapdf --flavour 1ua1 "{out}"')


# ── page-level helpers that need pdf in scope ─────────────────────────────────

def extract_page_mcids_with_pdf(pdf, page, verbose=False) -> list[int]:
    """Extract MCIDs from a page's content stream(s)."""
    mcids = []
    try:
        raw = page.obj.get('/Contents')
        if raw is None:
            return mcids
        if isinstance(raw, pikepdf.Array):
            streams = [pdf.get_object(ref.objgen) for ref in raw]
        else:
            try:
                streams = [pdf.get_object(raw.objgen)]
            except AttributeError:
                streams = [raw]

        for stream in streams:
            try:
                data = stream.read_bytes()
            except Exception:
                continue
            toks = list(_tokens(data))
            for idx, (kind, val) in enumerate(toks):
                if kind == 'name' and val == 'MCID':
                    if idx + 1 < len(toks) and toks[idx+1][0] == 'int':
                        mcids.append(int(toks[idx+1][1]))
    except Exception as exc:
        if verbose:
            print(f'  [warn] content scan error: {exc}')
    return sorted(set(mcids))


def collect_struct_mcid_map_with_pdf(pdf, verbose=False) -> dict:
    """Walk struct tree → return {(page_idx, mcid): struct_elem_dict}."""
    mapping = {}

    try:
        sroot = pdf.Root['/StructTreeRoot']
    except Exception:
        return mapping

    # page objgen → index
    page_idx_map = {}
    for i, p in enumerate(pdf.pages):
        try:
            page_idx_map[p.obj.objgen] = i
        except Exception:
            pass

    def pg_idx(ref):
        try:
            return page_idx_map.get(ref.objgen)
        except Exception:
            return None

    def walk(obj, page_hint=None):
        if not isinstance(obj, pikepdf.Dictionary):
            try:
                obj = pdf.get_object(obj.objgen)
            except Exception:
                return
        pg = obj.get('/Pg') or page_hint
        k  = obj.get('/K')
        if k is not None:
            process_k(k, obj, pg)

    def process_k(k, parent, pg):
        if isinstance(k, pikepdf.Array):
            for item in k:
                process_k(item, parent, pg)
        elif isinstance(k, pikepdf.Dictionary):
            typ = k.get('/Type')
            if typ is not None and str(typ) == '/MCR':
                mcid_v = k.get('/MCID')
                mcr_pg = k.get('/Pg') or pg
                if mcid_v is not None and mcr_pg is not None:
                    pi = pg_idx(mcr_pg)
                    if pi is not None:
                        mapping[(pi, int(mcid_v))] = parent
            else:
                walk(k, pg)
        else:
            # integer MCID directly
            try:
                iv = int(k)
                if pg is not None:
                    pi = pg_idx(pg)
                    if pi is not None:
                        mapping[(pi, iv)] = parent
                return
            except (TypeError, ValueError):
                pass
            # indirect ref to child struct elem
            try:
                child = pdf.get_object(k.objgen)
                walk(child, pg)
            except Exception:
                pass

    try:
        top_k = sroot.get('/K')
        if top_k is None:
            return mapping
        if isinstance(top_k, pikepdf.Array):
            for child in top_k:
                try:
                    walk(pdf.get_object(child.objgen))
                except Exception:
                    pass
        else:
            try:
                walk(pdf.get_object(top_k.objgen))
            except Exception:
                walk(top_k)
    except Exception as exc:
        if verbose:
            print(f'  [warn] struct walk top-level error: {exc}')

    return mapping


def _wrap_page_as_artifact(pdf, page, verbose=False):
    """Wrap the entire content stream of a page in /Artifact BMC … EMC."""
    raw = page.obj.get('/Contents')
    if raw is None:
        return

    if isinstance(raw, pikepdf.Array):
        streams = [(ref, pdf.get_object(ref.objgen)) for ref in raw]
    else:
        try:
            s = pdf.get_object(raw.objgen)
            streams = [(raw, s)]
        except AttributeError:
            streams = [(None, raw)]

    for ref, stream in streams:
        try:
            data = stream.read_bytes()
            # only wrap if not already marked
            if b'BMC' not in data and b'BDC' not in data:
                new_data = b'/Artifact BMC\n' + data + b'\nEMC\n'
                stream.write(new_data)
        except Exception as exc:
            if verbose:
                print(f'    [warn] stream wrap: {exc}')


if __name__ == '__main__':
    main()
