#!/usr/bin/env python3
"""
fix_table_tagging.py
Detects visually present tables and builds correct PDF/UA-1 table structure:
  Table > TR > TH | TD

Two-pass approach:
  Pass 1 — pdfplumber geometric detection, false-positive rejection by geometry
  Pass 2 — vision model confirmation and header-row identification
            (env: VISION_PROVIDER_BASE_URL, VISION_PROVIDER_API_KEY, VISION_MODEL)

For each confirmed table:
  - Identifies MCIDs whose content stream markers fall within the table bounding box
  - Removes those MCIDs from their current struct elements (P/H under Sect)
  - Builds Table > TR > TH | TD hierarchy with correct MCID ownership
  - Rebuilds ParentTree entries for affected pages

Designed to run AFTER fix_untagged_pdf.py + fix_struct_content_marking.py
and BEFORE fix_table_headers.py (which adds Scope attributes to TH cells).

Usage:
  fix_table_tagging.py <input.pdf> <output.pdf> [--out results.json] [--dpi 150]

Exit codes:
  0  FIXED or ALREADY_CORRECT
  1  PARTIAL (some tables processed, some failed)
  2  ERROR
"""
import sys, json, argparse, os, base64, re, shutil
from pathlib import Path
from collections import defaultdict

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

try:
    import pikepdf
    from pikepdf import Name, Dictionary, Array, Integer, String
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'pikepdf unavailable: {e}'}))
    sys.exit(2)

try:
    import pdfplumber
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'pdfplumber unavailable: {e}'}))
    sys.exit(2)

try:
    import urllib.request
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'urllib unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('input_pdf')
parser.add_argument('output_pdf')
parser.add_argument('--out', default=None)
parser.add_argument('--dpi', type=int, default=150)
args = parser.parse_args()

# ── Vision model environment ──────────────────────────────────────────────────

VISION_BASE_URL = (os.environ.get('VISION_PROVIDER_BASE_URL') or
                   os.environ.get('PRIMARY_PROVIDER_BASE_URL', ''))
VISION_API_KEY  = (os.environ.get('VISION_PROVIDER_API_KEY') or
                   os.environ.get('PRIMARY_PROVIDER_API_KEY', ''))
VISION_MODEL    = os.environ.get('VISION_MODEL', '')

VISION_AVAILABLE = bool(VISION_BASE_URL and VISION_API_KEY and VISION_MODEL)

# ── False-positive geometry thresholds ───────────────────────────────────────
# Reject pdfplumber detections that are obviously not tables before vision call.
# Thresholds are conservative — borderline cases go to vision.

MIN_TABLE_HEIGHT_PT = 25    # single ruled line false positive (< ~0.35 in)
MIN_TABLE_WIDTH_PT  = 60    # too narrow to be a real table
MIN_TABLE_ROWS      = 2     # pdfplumber row count; 1-row "table" is a ruled line
FULLPAGE_FRACTION   = 0.92  # bbox covers >92% of page in both dims = full-page FP

# ── Helpers ───────────────────────────────────────────────────────────────────

def emit(obj):
    print(json.dumps(obj), flush=True)


def render_region_b64(fitz_doc, page_num, bbox, dpi):
    """Render a bounding box region on page_num (0-based), return base64 PNG."""
    try:
        page = fitz_doc[page_num]
        # pdfplumber bbox is (x0, y0, x1, y1) in PDF user space (origin bottom-left).
        # fitz uses top-left origin. Convert: fitz_y = page_height - pdf_y
        h = page.rect.height
        x0, y0_pdf, x1, y1_pdf = bbox
        fitz_rect = fitz.Rect(x0, h - y1_pdf, x1, h - y0_pdf)
        # Clamp to page bounds
        fitz_rect = fitz_rect & page.rect
        if fitz_rect.is_empty:
            return None
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, clip=fitz_rect, alpha=False)
        return base64.b64encode(pix.tobytes('png')).decode('utf-8')
    except Exception:
        return None


def call_vision_table_confirm(image_b64, page_num):
    """
    Ask vision model to confirm this is a table and identify the header row.
    Returns dict: {'is_table': bool, 'header_row': int}  (header_row -1 = none)
    """
    prompt = (
        'You are inspecting a cropped region from a clinical healthcare PDF. '
        'Answer two questions about this image:\n'
        '1. Is this region a data table (grid of rows and columns with cell borders '
        'or clear column alignment)? Answer YES or NO.\n'
        '2. If YES, which row (0-based index) is the header row? '
        'If there is no header row, answer -1. '
        'If this is not a table, answer -1.\n\n'
        'Respond with ONLY a JSON object in this exact format, nothing else:\n'
        '{"is_table": true, "header_row": 0}'
    )
    payload = json.dumps({
        'model': VISION_MODEL,
        'max_tokens': 60,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/png;base64,{image_b64}'}},
                {'type': 'text', 'text': prompt}
            ]
        }]
    }).encode('utf-8')

    endpoint = VISION_BASE_URL.rstrip('/') + '/chat/completions'
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            'Content-Type':  'application/json',
            'Authorization': f'Bearer {VISION_API_KEY}',
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    raw = data['choices'][0]['message']['content'].strip()
    # Strip markdown fences if model wraps response
    raw = re.sub(r'^```[a-z]*\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    parsed = json.loads(raw)
    return {
        'is_table':   bool(parsed.get('is_table', False)),
        'header_row': int(parsed.get('header_row', -1)),
    }


def is_false_positive_by_geometry(bbox, page_width, page_height, plumb_table):
    """Return True if this bbox is almost certainly not a real table."""
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0

    if h < MIN_TABLE_HEIGHT_PT:
        return True, 'single_line'
    if w < MIN_TABLE_WIDTH_PT:
        return True, 'too_narrow'
    if (w / page_width > FULLPAGE_FRACTION and h / page_height > FULLPAGE_FRACTION):
        return True, 'full_page'

    # pdfplumber row count check: if only 1 row detected, it's a ruled line
    try:
        rows = plumb_table.extract()
        if rows is not None and len(rows) < MIN_TABLE_ROWS:
            return True, 'single_row'
    except Exception:
        pass

    return False, None


# ── Content stream MCID position extraction ───────────────────────────────────
# For each page, build: mcid → list of (x, y) text positions
# We extract text positions from the content stream by pairing BDC/MCID markers
# with the Td/Tm/T* operators that follow inside the BT/ET block.
# This gives us a spatial anchor per MCID so we can test bbox containment.

def _tokenize(data: bytes):
    """Minimal tokenizer returning (kind, value) — same approach as fix_struct_content_marking."""
    i = 0
    n = len(data)
    while i < n:
        c = data[i:i+1]
        if c in (b' ', b'\t', b'\r', b'\n', b'\x0c'):
            i += 1; continue
        if c == b'%':
            while i < n and data[i:i+1] not in (b'\r', b'\n'): i += 1
            continue
        if c == b'/':
            j = i + 1
            while j < n and data[j:j+1] not in (
                b' ',b'\t',b'\r',b'\n',b'\x0c',b'/',b'<',b'>',b'[',b']',b'(',b')',b'%'
            ): j += 1
            yield ('name', data[i+1:j].decode('latin-1')); i = j; continue
        if c == b'(':
            depth, j = 1, i+1
            while j < n and depth:
                ch = data[j:j+1]
                if ch == b'\\': j += 2
                elif ch == b'(': depth += 1; j += 1
                elif ch == b')': depth -= 1; j += 1
                else: j += 1
            yield ('str', data[i:j]); i = j; continue
        if c == b'<':
            if data[i:i+2] == b'<<': yield ('op','<<'); i+=2; continue
            j = i+1
            while j < n and data[j:j+1] != b'>': j += 1
            yield ('str', data[i:j+1]); i = j+1; continue
        if c == b'>':
            if data[i:i+2] == b'>>': yield ('op','>>'); i+=2; continue
            yield ('op','>'); i+=1; continue
        if c in (b'[', b']'): yield ('op', c.decode()); i+=1; continue
        j = i
        while j < n and data[j:j+1] not in (
            b' ',b'\t',b'\r',b'\n',b'\x0c',b'/',b'<',b'>',b'[',b']',b'(',b')'
        ): j += 1
        token = data[i:j].decode('latin-1')
        try: int(token); yield ('int', token); i = j; continue
        except ValueError: pass
        try: float(token); yield ('real', token); i = j; continue
        except ValueError: pass
        yield ('op', token); i = j


def extract_mcid_positions(pdf_pike, page_idx):
    """
    Returns dict: mcid (int) → (x, y) in PDF user space (pts, origin bottom-left).
    Extracts text matrix from Tm operator inside each BDC block.
    Falls back to (0,0) if no Tm found — better than nothing for bbox tests.
    """
    page = pdf_pike.pages[page_idx]
    raw = page.obj.get('/Contents')
    if raw is None:
        return {}

    if isinstance(raw, pikepdf.Array):
        streams = [pdf_pike.get_object(r.objgen) for r in raw]
    else:
        try:
            streams = [pdf_pike.get_object(raw.objgen)]
        except AttributeError:
            streams = [raw]

    mcid_pos = {}
    current_mcid = None
    in_bt = False
    tm_x = tm_y = 0.0
    last_td_x = last_td_y = 0.0

    for stream in streams:
        try:
            data = stream.read_bytes()
        except Exception:
            continue
        tokens = list(_tokenize(data))
        i = 0
        while i < len(tokens):
            kind, val = tokens[i]
            if kind == 'op':
                if val == 'BT':
                    in_bt = True
                    tm_x = tm_y = 0.0
                elif val == 'ET':
                    in_bt = False
                    current_mcid = None
                elif val == 'BDC':
                    # Previous tokens should be: /Tag <<... /MCID N ...>>
                    # Scan backwards for /MCID integer
                    j = i - 1
                    found_mcid = None
                    while j >= max(0, i-20):
                        if tokens[j] == ('name', 'MCID'):
                            if j+1 < len(tokens) and tokens[j+1][0] == 'int':
                                found_mcid = int(tokens[j+1][1])
                            break
                        j -= 1
                    if found_mcid is not None:
                        current_mcid = found_mcid
                elif val == 'EMC':
                    current_mcid = None
                elif val == 'Tm' and in_bt:
                    # 6 operands: a b c d e f — e=x, f=y
                    # look back for 6 numbers
                    nums = []
                    j = i - 1
                    while j >= 0 and len(nums) < 6:
                        if tokens[j][0] in ('int', 'real'):
                            nums.insert(0, float(tokens[j][1]))
                        else:
                            break
                        j -= 1
                    if len(nums) == 6:
                        tm_x, tm_y = nums[4], nums[5]
                        if current_mcid is not None and current_mcid not in mcid_pos:
                            mcid_pos[current_mcid] = (tm_x, tm_y)
                elif val == 'Td' and in_bt:
                    # 2 operands: tx ty — relative offset from current line origin
                    nums = []
                    j = i - 1
                    while j >= 0 and len(nums) < 2:
                        if tokens[j][0] in ('int', 'real'):
                            nums.insert(0, float(tokens[j][1]))
                        else:
                            break
                        j -= 1
                    if len(nums) == 2:
                        last_td_x += nums[0]
                        last_td_y += nums[1]
                        if current_mcid is not None and current_mcid not in mcid_pos:
                            mcid_pos[current_mcid] = (tm_x + last_td_x, tm_y + last_td_y)
            i += 1

    # Fill in any MCIDs we found via BDC but couldn't pin to a position
    # (use page-level fallback so we don't lose them entirely)
    return mcid_pos


def mcid_in_bbox(pos, bbox):
    """
    Test if a (x, y) position in PDF user space falls within a pdfplumber bbox.
    pdfplumber bbox: (x0, y0, x1, y1) with y measured from top (increasing downward).
    fitz/PDF user space: y increases upward.
    We convert pdfplumber to user space by: pdf_y0 = page_h - plumb_y1, etc.
    But we don't have page_h here — caller passes pre-converted bbox.
    """
    x, y = pos
    bx0, by0, bx1, by1 = bbox  # already in PDF user space (y up)
    return bx0 <= x <= bx1 and by0 <= y <= by1


# ── Struct tree helpers ───────────────────────────────────────────────────────

def get_kids_xrefs(xref, doc):
    kids = doc.xref_get_key(xref, 'K')
    if kids[0] == 'array':
        return [int(r) for r in re.findall(r'(\d+)\s+0\s+R', kids[1])]
    elif kids[0] == 'xref':
        return [int(kids[1].split()[0])]
    return []


def collect_mcid_to_elem(pdf_pike):
    """
    Walk struct tree, return:
      (page_idx, mcid) → (elem_dict, parent_dict, kid_position_in_parent_K)
    Only leaf elements (those with integer MCID in K) are returned.
    """
    result = {}

    page_idx_map = {}
    for i, p in enumerate(pdf_pike.pages):
        try:
            page_idx_map[p.obj.objgen] = i
        except Exception:
            pass

    def pg_idx(ref):
        try:
            return page_idx_map.get(ref.objgen)
        except Exception:
            return None

    def walk(obj, page_hint=None, parent=None, parent_k_idx=None):
        if not isinstance(obj, pikepdf.Dictionary):
            try:
                obj = pdf_pike.get_object(obj.objgen)
            except Exception:
                return
        pg = obj.get('/Pg') or page_hint
        k  = obj.get('/K')
        if k is None:
            return
        process_k(k, obj, pg, parent, parent_k_idx)

    def process_k(k, parent_elem, pg, grandparent, gp_k_idx):
        if isinstance(k, pikepdf.Array):
            for idx, item in enumerate(k):
                process_k(item, parent_elem, pg, grandparent, gp_k_idx)
        elif isinstance(k, pikepdf.Dictionary):
            typ = k.get('/Type')
            if typ is not None and str(typ) == '/MCR':
                mcid_v = k.get('/MCID')
                mcr_pg = k.get('/Pg') or pg
                if mcid_v is not None and mcr_pg is not None:
                    pi = pg_idx(mcr_pg)
                    if pi is not None:
                        result[(pi, int(mcid_v))] = (parent_elem, pg)
            else:
                walk(k, pg, parent_elem, None)
        else:
            # Try integer MCID
            try:
                iv = int(k)
                if pg is not None:
                    pi = pg_idx(pg)
                    if pi is not None:
                        result[(pi, iv)] = (parent_elem, pg)
                return
            except (TypeError, ValueError):
                pass
            # Indirect child struct elem
            try:
                child = pdf_pike.get_object(k.objgen)
                walk(child, pg, parent_elem, None)
            except Exception:
                pass

    try:
        sroot = pdf_pike.Root['/StructTreeRoot']
        top_k = sroot.get('/K')
        if top_k is None:
            return result
        if isinstance(top_k, pikepdf.Array):
            for child in top_k:
                try:
                    walk(pdf_pike.get_object(child.objgen))
                except Exception:
                    pass
        else:
            try:
                walk(pdf_pike.get_object(top_k.objgen))
            except Exception:
                walk(top_k)
    except Exception:
        pass

    return result


def remove_mcid_from_parent(parent_elem, mcid_int):
    """
    Remove an integer MCID from parent_elem's /K array.
    If /K becomes empty after removal, leaves it empty
    (caller is responsible for pruning empty elements if desired).
    """
    k = parent_elem.get('/K')
    if k is None:
        return
    if isinstance(k, pikepdf.Array):
        new_k = pikepdf.Array([
            item for item in k
            if not (isinstance(item, pikepdf.Object) and
                    _safe_int(item) == mcid_int)
        ])
        parent_elem['/K'] = new_k
    else:
        try:
            if int(k) == mcid_int:
                parent_elem['/K'] = pikepdf.Array()
        except (TypeError, ValueError):
            pass


def _safe_int(obj):
    try:
        return int(obj)
    except (TypeError, ValueError):
        return None


def collect_objr_to_elem(pdf_pike):
    """
    Walk struct tree, find OBJR nodes (object references — typically for
    annotations). Return dict: annot_StructParent_int → parent_struct_elem.

    OBJR structure: Dictionary(Type=/OBJR, Obj=<annot_ref>, Pg=<page_ref>)
    The annotation referenced by Obj has /StructParent N, where N is the
    ParentTree key that should map back to the OBJR's containing struct element.
    """
    result = {}  # struct_parent_int → struct_elem

    def walk(obj, parent_elem=None):
        if not isinstance(obj, pikepdf.Dictionary):
            try:
                obj = pdf_pike.get_object(obj.objgen)
            except Exception:
                return
        k = obj.get('/K')
        if k is None:
            return
        process_k(k, obj)

    def process_k(k, parent_elem):
        if isinstance(k, pikepdf.Array):
            for item in k:
                process_k(item, parent_elem)
        elif isinstance(k, pikepdf.Dictionary):
            typ = k.get('/Type')
            if typ is not None and str(typ) == '/OBJR':
                # Found an OBJR — get the referenced annotation's StructParent
                annot_ref = k.get('/Obj')
                if annot_ref is not None:
                    try:
                        annot = pdf_pike.get_object(annot_ref.objgen)
                        sp = annot.get('/StructParent')
                        if sp is not None:
                            try:
                                result[int(sp)] = parent_elem
                            except Exception:
                                pass
                    except Exception:
                        pass
            else:
                walk(k, parent_elem)
        else:
            try:
                child = pdf_pike.get_object(k.objgen)
                walk(child, parent_elem)
            except Exception:
                pass

    try:
        sroot = pdf_pike.Root['/StructTreeRoot']
        top_k = sroot.get('/K')
        if top_k is None:
            return result
        if isinstance(top_k, pikepdf.Array):
            for child in top_k:
                try:
                    walk(pdf_pike.get_object(child.objgen))
                except Exception:
                    pass
        else:
            try:
                walk(pdf_pike.get_object(top_k.objgen))
            except Exception:
                walk(top_k)
    except Exception:
        pass

    return result


def build_parent_tree(pdf_pike):
    """
    Rebuild ParentTree from scratch by walking all leaf struct elements
    and collecting BOTH:
      - (page_idx, mcid) → elem mappings (for content stream MCID references)
      - annot_StructParent → elem mappings (for annotation OBJR references)

    The ParentTree number tree must contain both kinds of entries:
      - Per-page entries keyed by /StructParents (an array of struct elems)
      - Per-annotation entries keyed by /StructParent (a single struct elem)

    Without OBJR preservation, link annotations lose their struct element
    parent reference and veraPDF 7.18.5 fails.
    """
    sroot = pdf_pike.Root['/StructTreeRoot']

    page_idx_map = {}
    for i, p in enumerate(pdf_pike.pages):
        try:
            page_idx_map[p.obj.objgen] = i
        except Exception:
            pass

    page_to_sp = {}
    for i, p in enumerate(pdf_pike.pages):
        sp = p.obj.get('/StructParents')
        if sp is not None:
            try:
                page_to_sp[i] = int(sp)
            except Exception:
                pass

    # ── Collect MCID-based mappings (content stream items) ────────────────
    mapping = collect_mcid_to_elem(pdf_pike)
    by_page = defaultdict(dict)  # page_idx → {mcid: elem}
    for (pi, mcid), (elem, pg) in mapping.items():
        by_page[pi][mcid] = elem

    # ── Collect OBJR-based mappings (annotations) ─────────────────────────
    objr_mapping = collect_objr_to_elem(pdf_pike)  # struct_parent_int → elem

    # Track the max StructParent used so we don't collide with annot keys
    all_sp_keys = set(page_to_sp.values()) | set(objr_mapping.keys())
    sp_counter = max(all_sp_keys, default=-1) + 1

    # Assign StructParents to pages that don't have one
    for pi in sorted(by_page.keys()):
        if pi not in page_to_sp:
            page_to_sp[pi] = sp_counter
            pdf_pike.pages[pi].obj['/StructParents'] = Integer(sp_counter)
            sp_counter += 1

    # ── Build per-page MCID arrays ────────────────────────────────────────
    pt_entries = {}
    for pi, mcid_map in by_page.items():
        sp = page_to_sp.get(pi)
        if sp is None or not mcid_map:
            continue
        max_mcid = max(mcid_map.keys())
        arr = pikepdf.Array([pikepdf.Null()] * (max_mcid + 1))
        for mcid, elem in mcid_map.items():
            try:
                arr[mcid] = pdf_pike.make_indirect(elem)
            except Exception:
                pass
        pt_entries[sp] = arr

    # ── Add OBJR entries (single struct elem per StructParent key) ────────
    for struct_parent_int, elem in objr_mapping.items():
        if struct_parent_int in pt_entries:
            # Collision with a page StructParents key — should not normally happen
            # since StructParent (annotation) and StructParents (page) use separate
            # integer spaces, but skip rather than overwrite if it does.
            continue
        try:
            pt_entries[struct_parent_int] = pdf_pike.make_indirect(elem)
        except Exception:
            pass

    # ── Emit number tree ──────────────────────────────────────────────────
    nums = pikepdf.Array()
    for key in sorted(pt_entries):
        nums.append(Integer(key))
        nums.append(pt_entries[key])
    sroot['/ParentTree'] = pdf_pike.make_indirect(
        pikepdf.Dictionary(Nums=nums))
    sroot['/ParentTreeNextKey'] = Integer(sp_counter)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    issues  = []
    tables_confirmed  = 0
    tables_rejected   = 0
    tables_tagged     = 0
    tables_failed     = 0

    # ── Open PDF ──────────────────────────────────────────────────────────
    try:
        fitz_doc = fitz.open(args.input_pdf)
    except Exception as e:
        out = {'result': 'ERROR', 'error': f'Cannot open PDF: {e}'}
        print(json.dumps(out))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        sys.exit(2)

    # Guard: needs struct tree
    catalog = fitz_doc.pdf_catalog()
    struct_ref = fitz_doc.xref_get_key(catalog, 'StructTreeRoot')
    if struct_ref[0] == 'null' or not struct_ref[1]:
        fitz_doc.close()
        out = {
            'result': 'SKIPPED',
            'note': 'No StructTreeRoot — run fix_untagged_pdf.py first'
        }
        print(json.dumps(out))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        shutil.copy2(args.input_pdf, args.output_pdf)
        sys.exit(0)

    fitz_doc.close()

    # ── pdfplumber: detect table candidates ───────────────────────────────
    candidates = []   # list of {page_num_0based, bbox_plumb, bbox_userspace, plumb_table, rows}
    try:
        with pdfplumber.open(args.input_pdf) as plumb:
            for page_obj in plumb.pages:
                pg0 = page_obj.page_number - 1   # 0-based
                pw  = page_obj.width
                ph  = page_obj.height

                if not page_obj.chars:
                    continue   # image-only page — skip

                for plumb_table in page_obj.find_tables():
                    bbox = plumb_table.bbox   # (x0, y0, x1, y1) top-left origin

                    fp, reason = is_false_positive_by_geometry(bbox, pw, ph, plumb_table)
                    if fp:
                        tables_rejected += 1
                        issues.append({
                            'page': pg0 + 1,
                            'type': 'false_positive_rejected',
                            'reason': reason,
                            'bbox': list(bbox)
                        })
                        continue

                    # Convert pdfplumber bbox (top-left origin) to PDF user space
                    # (bottom-left origin) for position comparison later
                    x0, y0_top, x1, y1_top = bbox
                    bbox_us = (x0, ph - y1_top, x1, ph - y0_top)

                    # Extract rows for header heuristic fallback
                    try:
                        rows = plumb_table.extract() or []
                    except Exception:
                        rows = []

                    candidates.append({
                        'page_0':    pg0,
                        'bbox_plumb': list(bbox),
                        'bbox_us':   bbox_us,
                        'rows':      rows,
                        'page_w':    pw,
                        'page_h':    ph,
                    })
    except Exception as e:
        out = {'result': 'ERROR', 'error': f'pdfplumber failed: {e}'}
        print(json.dumps(out))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        sys.exit(2)

    if not candidates:
        out = {
            'result': 'ALREADY_CORRECT',
            'note': ('No table candidates found after geometric filtering. '
                     f'{tables_rejected} false positive(s) rejected.'),
            'tables_rejected': tables_rejected,
        }
        print(json.dumps(out, indent=2))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        shutil.copy2(args.input_pdf, args.output_pdf)
        sys.exit(0)

    # ── Vision model: confirm candidates and identify header rows ─────────
    fitz_doc = fitz.open(args.input_pdf)
    confirmed = []   # candidates with is_table=True + header_row

    for cand in candidates:
        pg0  = cand['page_0']
        bbox = cand['bbox_plumb']
        rows = cand['rows']

        if VISION_AVAILABLE:
            img_b64 = render_region_b64(fitz_doc, pg0, bbox, args.dpi)
            if img_b64:
                try:
                    verdict = call_vision_table_confirm(img_b64, pg0)
                except Exception as e:
                    # Vision call failed — fall back to heuristic
                    verdict = {
                        'is_table':   True,
                        'header_row': 0 if rows else -1,
                        'fallback':   f'vision_error: {e}'
                    }
            else:
                verdict = {
                    'is_table':   True,
                    'header_row': 0 if rows else -1,
                    'fallback':   'render_failed'
                }
        else:
            # No vision — heuristic only (first row = header if rows exist)
            verdict = {
                'is_table':   True,
                'header_row': 0 if rows else -1,
                'fallback':   'no_vision_model'
            }

        if not verdict['is_table']:
            tables_rejected += 1
            issues.append({
                'page': pg0 + 1,
                'type': 'rejected_by_vision',
                'bbox': bbox
            })
            continue

        tables_confirmed += 1
        cand['header_row'] = verdict['header_row']
        cand['vision_note'] = verdict.get('fallback', 'vision_confirmed')
        confirmed.append(cand)

    fitz_doc.close()

    if not confirmed:
        out = {
            'result': 'ALREADY_CORRECT',
            'note': 'All candidates rejected as non-tables by vision model.',
            'tables_rejected': tables_rejected,
            'issues': issues,
        }
        print(json.dumps(out, indent=2))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        shutil.copy2(args.input_pdf, args.output_pdf)
        sys.exit(0)

    # ── pikepdf: restructure struct tree ──────────────────────────────────
    try:
        pdf_pike = pikepdf.open(args.input_pdf)
    except Exception as e:
        out = {'result': 'ERROR', 'error': f'pikepdf cannot open PDF: {e}'}
        print(json.dumps(out))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        sys.exit(2)

    sroot = pdf_pike.Root['/StructTreeRoot']

    # Collect existing (page_idx, mcid) → elem mapping
    mcid_to_elem = collect_mcid_to_elem(pdf_pike)

    # Build per-page MCID position map
    page_mcid_positions = {}   # page_idx → {mcid: (x, y) in user space}
    pages_needed = {c['page_0'] for c in confirmed}
    for pi in pages_needed:
        page_mcid_positions[pi] = extract_mcid_positions(pdf_pike, pi)

    # Process each confirmed table
    for cand in confirmed:
        pg0        = cand['page_0']
        bbox_us    = cand['bbox_us']
        rows       = cand['rows']
        header_row = cand['header_row']
        mcid_positions = page_mcid_positions.get(pg0, {})

        # Find MCIDs whose text position falls within this table's bbox
        table_mcids = []
        for mcid, pos in mcid_positions.items():
            if (pg0, mcid) in mcid_to_elem and mcid_in_bbox(pos, bbox_us):
                table_mcids.append(mcid)
        table_mcids.sort()

        if not table_mcids:
            # No MCIDs resolved to this bbox — cannot tag, skip
            tables_failed += 1
            issues.append({
                'page': pg0 + 1,
                'type': 'no_mcids_in_bbox',
                'bbox': cand['bbox_plumb'],
                'note': 'Vision confirmed table but no MCIDs found within bounding box'
            })
            continue

        # Determine row structure from pdfplumber row data
        # Map MCIDs to rows by splitting table_mcids evenly across row count
        # (pdfplumber rows give us the logical row count)
        num_rows = max(len(rows), 1)
        mcids_per_row = max(1, len(table_mcids) // num_rows)
        row_mcid_groups = []
        for r in range(num_rows):
            start = r * mcids_per_row
            end   = start + mcids_per_row if r < num_rows - 1 else len(table_mcids)
            group = table_mcids[start:end]
            if group:
                row_mcid_groups.append(group)

        # Get the page ref for struct element construction
        page_pg_ref = pdf_pike.pages[pg0].obj

        # Remove these MCIDs from their current struct parents
        for mcid in table_mcids:
            key = (pg0, mcid)
            if key in mcid_to_elem:
                parent_elem, _ = mcid_to_elem[key]
                try:
                    remove_mcid_from_parent(parent_elem, mcid)
                except Exception:
                    pass

        # Find the Document element to hang the Table off
        # Walk to first Sect on this page and use its parent, or use doc_elem directly
        doc_elem = None
        try:
            top_k = sroot.get('/K')
            if isinstance(top_k, pikepdf.Array) and len(top_k) > 0:
                doc_elem = pdf_pike.get_object(top_k[0].objgen)
        except Exception:
            pass

        if doc_elem is None:
            tables_failed += 1
            issues.append({
                'page': pg0 + 1,
                'type': 'no_document_element',
                'note': 'Cannot find Document struct element to attach Table'
            })
            continue

        # Find or create a Sect for this page to contain the Table
        # Prefer to insert Table directly under an existing Sect on the same page
        parent_sect = None
        try:
            doc_k = doc_elem.get('/K')
            if isinstance(doc_k, pikepdf.Array):
                for child_ref in doc_k:
                    try:
                        child = pdf_pike.get_object(child_ref.objgen)
                        s_type = str(child.get('/S', '')).strip('/')
                        if s_type == 'Sect':
                            pg = child.get('/Pg')
                            if pg is not None:
                                try:
                                    if pdf_pike.pages[pg0].obj.objgen == pg.objgen:
                                        parent_sect = child
                                        break
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        if parent_sect is None:
            # Create a new Sect for this table
            parent_sect = pdf_pike.make_indirect(Dictionary(
                Type=Name('/StructElem'),
                S=Name('/Sect'),
                P=pdf_pike.make_indirect(doc_elem),
                K=pikepdf.Array(),
                Pg=page_pg_ref,
            ))
            try:
                doc_elem['/K'].append(parent_sect)
            except Exception:
                tables_failed += 1
                continue

        # Build Table > TR > TH|TD
        table_elem = pdf_pike.make_indirect(Dictionary(
            Type=Name('/StructElem'),
            S=Name('/Table'),
            P=pdf_pike.make_indirect(parent_sect),
            K=pikepdf.Array(),
            Pg=page_pg_ref,
        ))
        try:
            parent_sect['/K'].append(table_elem)
        except Exception:
            tables_failed += 1
            continue

        for row_idx, row_mcids in enumerate(row_mcid_groups):
            is_header_row = (row_idx == header_row)
            cell_tag = '/TH' if is_header_row else '/TD'

            tr_elem = pdf_pike.make_indirect(Dictionary(
                Type=Name('/StructElem'),
                S=Name('/TR'),
                P=table_elem,
                K=pikepdf.Array(),
                Pg=page_pg_ref,
            ))
            table_elem['/K'].append(tr_elem)

            for mcid in row_mcids:
                cell_elem = pdf_pike.make_indirect(Dictionary(
                    Type=Name('/StructElem'),
                    S=Name(cell_tag),
                    P=tr_elem,
                    K=pikepdf.Array([Integer(mcid)]),
                    Pg=page_pg_ref,
                ))
                tr_elem['/K'].append(cell_elem)

                # Update our local map so ParentTree rebuild is accurate
                mcid_to_elem[(pg0, mcid)] = (cell_elem, page_pg_ref)

        tables_tagged += 1

    # ── Rebuild ParentTree ────────────────────────────────────────────────
    try:
        build_parent_tree(pdf_pike)
    except Exception as e:
        issues.append({'type': 'parent_tree_rebuild_warning', 'note': str(e)})

    # ── Save ──────────────────────────────────────────────────────────────
    try:
        pdf_pike.save(args.output_pdf)
        pdf_pike.close()
    except Exception as e:
        out = {'result': 'ERROR', 'error': f'Save failed: {e}'}
        print(json.dumps(out))
        if args.out: Path(args.out).write_text(json.dumps(out, indent=2))
        sys.exit(2)

    # ── Result ────────────────────────────────────────────────────────────
    if tables_tagged == 0:
        result = 'FAIL'
        exit_code = 2
    elif tables_failed > 0:
        result = 'PARTIAL'
        exit_code = 1
    else:
        result = 'FIXED'
        exit_code = 0

    out = {
        'input':             args.input_pdf,
        'output':            args.output_pdf,
        'result':            result,
        'tables_confirmed':  tables_confirmed,
        'tables_tagged':     tables_tagged,
        'tables_rejected':   tables_rejected,
        'tables_failed':     tables_failed,
        'vision_available':  VISION_AVAILABLE,
        'issues':            issues,
        'note': (
            'Table struct elements built. Run fix_table_headers.py next to '
            'add Scope attributes to TH cells, then veraPDF to validate.'
        )
    }
    print(json.dumps(out, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
