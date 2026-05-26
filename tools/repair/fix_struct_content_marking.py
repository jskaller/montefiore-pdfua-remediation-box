#!/usr/bin/env python3
"""
fix_struct_content_marking.py
─────────────────────────────
Montefiore PDF/UA Remediation — Repair Library
Fixes: PDF/UA-1 clause 7.1 / test 3
       "Content is neither marked as Artifact nor tagged as real content."

Stage 2 of the two-script untagged-PDF sequence:
  1. fix_untagged_pdf.py      — builds struct tree + injects MCID markers
  2. fix_struct_content_marking.py (THIS)
                              — verifies and hardens ParentTree connectivity

What this script does
─────────────────────
1.  Walk every page's content stream and locate all BDC operators that
    carry an MCID operand.
    Collect: page_index → list[mcid]

2.  Walk the struct tree and collect every leaf element that references
    an MCID.
    Collect: (page_index, mcid) → struct-element object-ref

3.  For each page that has MCIDs:
    a. Assign a unique /StructParents integer to the page's /Page dict.
    b. Build a ParentTree entry for that StructParents number.

4.  Write the completed /ParentTree into StructTreeRoot.

5.  Ensure /ParentTreeNextKey is set correctly.

6.  Tag any remaining untagged pages with /Artifact BMC markers.

Invocation
──────────
    python3 fix_struct_content_marking.py <input.pdf> <output.pdf>
        [--verbose] [--out results.json]

Exit codes
──────────
    0  — success
    1  — input not found / not a PDF
    2  — no struct tree present (run fix_untagged_pdf.py first)
    3  — pikepdf error
"""

import argparse
import json
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
        if c == b'%':
            while i < n and stream_bytes[i:i+1] not in (b'\r', b'\n'):
                i += 1
            continue
        if c == b'/':
            j = i + 1
            while j < n and stream_bytes[j:j+1] not in (
                b' ', b'\t', b'\r', b'\n', b'\x0c',
                b'/', b'<', b'>', b'[', b']', b'(', b')', b'%'
            ):
                j += 1
            yield ('name', stream_bytes[i+1:j].decode('latin-1'))
            i = j
            continue
        if c == b'(':
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
        if c == b'<':
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
        j = i
        while j < n and stream_bytes[j:j+1] not in (
            b' ', b'\t', b'\r', b'\n', b'\x0c',
            b'/', b'<', b'>', b'[', b']', b'(', b')'
        ):
            j += 1
        token = stream_bytes[i:j].decode('latin-1')
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


def build_number_tree(entries):
    """Build a flat /Nums number tree dictionary from {int: object} mapping."""
    nums = pikepdf.Array()
    for key in sorted(entries):
        nums.append(pikepdf.Integer(key))
        nums.append(entries[key])
    return pikepdf.Dictionary(Nums=nums)


def extract_page_mcids_with_pdf(pdf, page, verbose=False):
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


def collect_struct_mcid_map_with_pdf(pdf, verbose=False):
    """Walk struct tree → return {(page_idx, mcid): struct_elem_dict}."""
    mapping = {}

    try:
        sroot = pdf.Root['/StructTreeRoot']
    except Exception:
        return mapping

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
            try:
                iv = int(k)
                if pg is not None:
                    pi = pg_idx(pg)
                    if pi is not None:
                        mapping[(pi, iv)] = parent
                return
            except (TypeError, ValueError):
                pass
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
            if b'BMC' not in data and b'BDC' not in data:
                new_data = b'/Artifact BMC\n' + data + b'\nEMC\n'
                stream.write(new_data)
        except Exception as exc:
            if verbose:
                print(f'    [warn] stream wrap: {exc}')


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Fix PDF/UA-1 7.1/3: ParentTree/MCID connectivity'
    )
    ap.add_argument('input',  help='Input PDF (from fix_untagged_pdf.py)')
    ap.add_argument('output', help='Output PDF path')
    ap.add_argument('--verbose', '-v', action='store_true')
    ap.add_argument('--out', default=None,
                    help='Optional path to write results JSON for orchestrator')
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    v = args.verbose

    # Result accumulator — written to --out at end
    result = {
        'input': str(inp),
        'output': str(out),
        'result': 'PENDING',
    }

    def finish(status_code, **extra):
        result.update(extra)
        if status_code == 0:
            result['result'] = result.get('result', 'PASS')
        else:
            result['result'] = result.get('result', 'ERROR')
        # Always print JSON to stdout for orchestrator
        print(json.dumps(result, indent=2))
        if args.out:
            try:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(json.dumps(result, indent=2))
            except Exception as exc:
                print(f'  [warn] could not write --out file: {exc}', file=sys.stderr)
        sys.exit(status_code)

    if not inp.exists():
        result['result'] = 'ERROR'
        result['error'] = f'input not found: {inp}'
        finish(1)

    # ── open ──────────────────────────────────────────────────────────────
    try:
        pdf = pikepdf.open(inp)
    except pikepdf.PdfError as e:
        result['result'] = 'ERROR'
        result['error'] = f'pikepdf cannot open {inp}: {e}'
        finish(3)

    root = pdf.Root

    # ── guard: struct tree must exist ─────────────────────────────────────
    sroot = root.get('/StructTreeRoot')
    if sroot is None:
        result['result'] = 'ERROR'
        result['error'] = 'no /StructTreeRoot — run fix_untagged_pdf.py first'
        finish(2)

    # ── 1. collect MCIDs per page from content streams ────────────────────
    if v:
        print('Phase 1: scanning content streams for BDC/MCID markers …')
    page_mcids = {}
    for page_idx, page in enumerate(pdf.pages):
        mcids = extract_page_mcids_with_pdf(pdf, page, v)
        if mcids:
            page_mcids[page_idx] = mcids
            if v:
                print(f'  page {page_idx}: MCIDs {mcids}')

    total_mcids = sum(len(v2) for v2 in page_mcids.values())
    if v:
        print(f'  found {total_mcids} MCIDs across {len(page_mcids)} pages')

    # ── 2. collect struct-element map ────────────────────────────────────
    if v:
        print('Phase 2: walking struct tree …')
    struct_map = collect_struct_mcid_map_with_pdf(pdf, v)
    if v:
        print(f'  struct tree covers {len(struct_map)} (page,mcid) pairs')

    # ── 3. assign /StructParents to pages ─────────────────────────────────
    if v:
        print('Phase 3: assigning /StructParents to pages …')
    page_to_sp = {}
    sp_counter = 0
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
    if v:
        print('Phase 4: building ParentTree …')
    parent_tree_entries = {}
    unmatched_mcids = 0

    for page_idx, mcids in page_mcids.items():
        sp = page_to_sp.get(page_idx)
        if sp is None:
            continue
        max_mcid = max(mcids)
        arr = pikepdf.Array([None] * (max_mcid + 1))
        for mcid in mcids:
            key = (page_idx, mcid)
            elem = struct_map.get(key)
            if elem is None:
                unmatched_mcids += 1
                if v:
                    print(f'  [warn] no struct elem for page {page_idx} MCID {mcid}')
                continue
            try:
                obj_ref = pdf.make_indirect(elem)
                arr[mcid] = obj_ref
            except Exception as exc:
                if v:
                    print(f'  [warn] could not make indirect for ({page_idx},{mcid}): {exc}')
        parent_tree_entries[sp] = arr

    # ── 5. write ParentTree into StructTreeRoot ───────────────────────────
    if v:
        print('Phase 5: writing ParentTree …')
    pt_dict = build_number_tree(parent_tree_entries)
    pt_indirect = pdf.make_indirect(pt_dict)
    sroot['/ParentTree'] = pt_indirect
    sroot['/ParentTreeNextKey'] = pikepdf.Integer(sp_counter)

    # ── 6. mark untagged pages as Artifact ────────────────────────────────
    if v:
        print('Phase 6: wrapping untagged pages as Artifact …')
    artifact_pages = 0
    for page_idx, page in enumerate(pdf.pages):
        if page_idx in page_mcids:
            continue
        try:
            _wrap_page_as_artifact(pdf, page, v)
            artifact_pages += 1
        except Exception as exc:
            if v:
                print(f'  [warn] artifact wrap failed page {page_idx}: {exc}')

    # ── 7. ensure MarkInfo ────────────────────────────────────────────────
    if root.get('/MarkInfo') is None:
        root['/MarkInfo'] = pikepdf.Dictionary(Marked=True)
    else:
        root['/MarkInfo']['/Marked'] = True

    # ── save ──────────────────────────────────────────────────────────────
    if v:
        print(f'Saving → {out} …')
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(out)
    pdf.close()

    if v:
        print('Done. ✓')

    result['result'] = 'FIXED'
    result['total_mcids'] = total_mcids
    result['pages_with_mcids'] = len(page_mcids)
    result['pages_wrapped_as_artifact'] = artifact_pages
    result['unmatched_mcids'] = unmatched_mcids
    result['struct_tree_pairs_walked'] = len(struct_map)
    finish(0)


if __name__ == '__main__':
    main()
