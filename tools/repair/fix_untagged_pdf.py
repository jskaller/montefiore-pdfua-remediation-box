#!/usr/bin/env python3
"""
fix_untagged_pdf.py
Auto-generates a complete tagged PDF structure for untagged PDFs that have
a native text layer.

Uses pikepdf.parse_content_stream / unparse_content_stream for token-level
manipulation rather than byte-level regex. This eliminates entire classes of
bugs around marker detection in complex content streams.

Algorithm:
  Per page content stream:
    1. Parse into instruction list
    2. Walk instructions, tracking marked-content depth
    3. At depth 0 (outside any existing BDC/BMC):
         a. BT...ET groups become structural P/H/L/Figure with new MCID
         b. Everything else between BT/ET groups becomes Artifact
    4. At depth > 0 (inside existing PlacedPDF/ActualText/etc): leave untouched
    5. Rebuild stream from modified instruction list

Output:
  1. Struct tree: Document > Sect+ > H | P | L | Figure
  2. Each H gets its own Sect (PDF/UA 7.4.4)
  3. ParentTree mapping each page's MCIDs to struct elements
  4. /Tabs /S on pages with annotations

Usage:
  fix_untagged_pdf.py <input.pdf> <output.pdf> [--out results.json]
"""
import sys, json, re, argparse, shutil
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': 'PyMuPDF unavailable: %s' % e}))
    sys.exit(2)

try:
    import pikepdf
    from pikepdf import Name, Dictionary, Array, Integer, String, Boolean
    from pikepdf import ContentStreamInstruction, Operator
    from pikepdf._core import _ObjectList
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': 'pikepdf unavailable: %s' % e}))
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
        'note': 'Document already has a structure tree'
    }, indent=2)
    print(result)
    if args.out:
        Path(args.out).write_text(result)
    shutil.copy2(args.input_pdf, args.output_pdf)
    sys.exit(0)

# ── Block classification (from fitz, for tag hints) ──────────────────────────

BULLET_CHARS = {'•', '·', '◦', '▪', '▸', '→', '-', '–', '*'}

def span_text(span):
    if 'text' in span:
        return span['text']
    return ''.join(c.get('c', '') for c in span.get('chars', []))

def block_text(block):
    return ''.join(
        span_text(s)
        for line in block.get('lines', [])
        for s in line.get('spans', [])
    ).strip()

def classify_block(block):
    if block['type'] != 0:
        return 'Figure'
    lines = block.get('lines', [])
    if not lines:
        return None
    spans = lines[0].get('spans', [])
    if not spans:
        return None
    size = spans[0].get('size', 12)
    flags = spans[0].get('flags', 0)
    is_bold = bool(flags & 16)
    text = block_text(block)
    if not text:
        return None
    if text[0] in BULLET_CHARS or re.match(r'^\d+[\.\)]\s', text) or re.match(r'^[a-z][\.\)]\s', text):
        return 'L'
    if size >= 18 or (size >= 16 and is_bold): return 'H'
    if size >= 14 or (size >= 13 and is_bold): return 'H'
    if size >= 12 and is_bold and len(text) < 120: return 'H'
    return 'P'

page_block_tags = []
for page in doc:
    rawdict = page.get_text('rawdict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    tags = []
    blocks = rawdict.get('blocks', [])
    i = 0
    while i < len(blocks):
        tag = classify_block(blocks[i])
        if tag is None:
            i += 1
            continue
        if tag == 'L':
            j = i + 1
            while j < len(blocks) and classify_block(blocks[j]) == 'L':
                j += 1
            tags.append('L')
            i = j
        else:
            tags.append(tag)
            i += 1
    page_block_tags.append(tags)

doc.close()

# ── Stream processing using parse_content_stream ─────────────────────────────

def make_bdc(tag_name, mcid):
    """Build a /Tag <</MCID N>> BDC instruction."""
    return ContentStreamInstruction(
        _ObjectList([Name('/' + tag_name), Dictionary(MCID=Integer(mcid))]),
        Operator('BDC')
    )

def make_artifact_bmc():
    """Build /Artifact BMC instruction."""
    return ContentStreamInstruction(
        _ObjectList([Name('/Artifact')]),
        Operator('BMC')
    )

def make_emc():
    """Build EMC instruction."""
    return ContentStreamInstruction(
        _ObjectList([]),
        Operator('EMC')
    )

# Graphics state operators - q/Q for save/restore graphics state
# These should be kept WITH adjacent content (e.g. q before BT, Q after ET)
# But for simplicity we just artifact anything outside BT/ET at depth 0

def process_instructions(instructions, start_mcid, tag_iter, xobject_types=None):
    """
    Walk parsed instructions, inject structural markers, return new list.
    
    Rules:
    - Track marked-content depth from BDC/BMC/EMC instructions
    - At depth 0:
      - BT...ET group: wrap with /Tag <</MCID N>> BDC ... EMC
      - Do operator referencing an /Image XObject: wrap as Figure BDC ... EMC
      - Do operator referencing a /Form XObject or unknown: wrap as Artifact
      - Anything else: wrap as Artifact
    - At depth > 0: pass through untouched (inside existing PlacedPDF/etc)
    
    xobject_types: dict mapping xobject name string (e.g. '/Im0') to
                   'Image' or 'Form'. Built from page Resources before call.
    
    Returns: (new_instructions_list, mcid_count)
    """
    if xobject_types is None:
        xobject_types = {}

    out = []
    depth = 0
    i = 0
    mcid = start_mcid
    
    # Pending artifact buffer: instructions at depth 0 that aren't BT/ET groups
    artifact_buf = []
    
    def flush_artifact():
        nonlocal artifact_buf
        if artifact_buf:
            out.append(make_artifact_bmc())
            out.extend(artifact_buf)
            out.append(make_emc())
            artifact_buf = []
    
    while i < len(instructions):
        instr = instructions[i]
        op = str(instr.operator)
        
        if op == 'BDC' or op == 'BMC':
            if depth == 0:
                # Top-level marked content begins
                # Check the tag - if it's not a real PDF/UA structural tag,
                # treat it as artifact (e.g. /PlacedPDF, /OC, custom property tags)
                tag = list(instr.operands)[0] if instr.operands else None
                tag_str = str(tag) if tag else ''
                # Real-content tags that should be preserved as-is
                REAL_CONTENT_TAGS = {
                    '/Artifact', '/ReversedChars', '/Clip',
                }
                # If existing tag is Artifact or other safe BMC tag, pass through
                if tag_str in REAL_CONTENT_TAGS:
                    flush_artifact()
                    out.append(instr)
                    depth += 1
                    i += 1
                    # Skip through to matching EMC, passing all through
                    while i < len(instructions) and depth > 0:
                        inner = instructions[i]
                        inner_op = str(inner.operator)
                        if inner_op == 'BDC' or inner_op == 'BMC':
                            depth += 1
                        elif inner_op == 'EMC':
                            depth -= 1
                        out.append(inner)
                        i += 1
                    continue
                else:
                    # Non-PDF/UA marker (PlacedPDF, OC, etc.) - reclassify as Artifact
                    # Find matching EMC, then re-emit as /Artifact BMC ... EMC
                    flush_artifact()
                    # Find matching EMC index
                    j = i + 1
                    inner_depth = 1
                    while j < len(instructions) and inner_depth > 0:
                        jop = str(instructions[j].operator)
                        if jop in ('BDC', 'BMC'):
                            inner_depth += 1
                        elif jop == 'EMC':
                            inner_depth -= 1
                        j += 1
                    # j is now one past the matching EMC
                    # Emit as Artifact wrapping everything between BDC and EMC,
                    # but strip any nested BDC/BMC/EMC inside (since they may
                    # reference resources that don't make sense; simpler to drop them).
                    # Actually safer: keep inner BDC/BMC/EMC structure intact.
                    out.append(make_artifact_bmc())
                    # Emit the inner instructions (skip the original BDC at i and EMC at j-1)
                    out.extend(instructions[i+1:j-1])
                    out.append(make_emc())
                    i = j
                    continue
            else:
                # Nested marker inside existing block - just track depth
                out.append(instr)
                depth += 1
                i += 1
                continue
        
        if op == 'EMC':
            # Should not hit this at depth 0 (no opening BDC/BMC)
            # But just in case, pass through
            out.append(instr)
            depth = max(0, depth - 1)
            i += 1
            continue

        # ── Bare Do at depth 0 — image or form XObject ────────────────────
        if op == 'Do' and depth == 0:
            xobj_name = str(list(instr.operands)[0]) if instr.operands else ''
            subtype = xobject_types.get(xobj_name, 'Unknown')
            if subtype == 'Image':
                # Real bitmap image — wrap as Figure with placeholder Alt
                flush_artifact()
                out.append(make_bdc('Figure', mcid))
                out.append(instr)
                out.append(make_emc())
                mcid += 1
            else:
                # Form XObject or unknown — treat as Artifact
                artifact_buf.append(instr)
            i += 1
            continue
        
        if op == 'BT' and depth == 0:
            # Found a top-level text block - find matching ET
            bt_idx = i
            j = i + 1
            while j < len(instructions) and str(instructions[j].operator) != 'ET':
                j += 1
            if j < len(instructions):
                # Have BT...ET - flush pending artifact, wrap text block with MCID
                flush_artifact()
                tag = next(tag_iter, 'P')
                if tag not in ('H', 'P', 'L', 'Figure'):
                    tag = 'P'
                out.append(make_bdc(tag, mcid))
                out.extend(instructions[bt_idx:j+1])  # BT through ET inclusive
                out.append(make_emc())
                mcid += 1
                i = j + 1
                continue
            else:
                # BT without ET - shouldn't happen but pass through as artifact
                artifact_buf.append(instr)
                i += 1
                continue
        
        # Everything else at depth 0: accumulate as artifact
        if depth == 0:
            artifact_buf.append(instr)
        else:
            out.append(instr)
        i += 1
    
    # Flush any remaining artifact at end
    flush_artifact()
    
    return out, mcid - start_mcid

# ── Phase 1: PyMuPDF clean save ──────────────────────────────────────────────

tmp = args.output_pdf + '.tmp_pass1.pdf'
doc2 = fitz.open(args.input_pdf)
doc2.save(tmp, garbage=4, deflate=True)
doc2.close()

# ── Phase 2: pikepdf - process streams and build struct tree ─────────────────

pdf = pikepdf.open(tmp)

if '/MarkInfo' not in pdf.Root:
    pdf.Root['/MarkInfo'] = pdf.make_indirect(Dictionary())
pdf.Root['/MarkInfo']['/Marked'] = Boolean(True)

if '/Lang' not in pdf.Root:
    pdf.Root['/Lang'] = String('en-US')

# Struct tree skeleton
struct_tree = pdf.make_indirect(Dictionary(
    Type=Name('/StructTreeRoot'),
    K=Array(),
    ParentTree=pdf.make_indirect(Dictionary(Nums=Array())),
    ParentTreeNextKey=Integer(0)
))
pdf.Root['/StructTreeRoot'] = struct_tree

doc_elem = pdf.make_indirect(Dictionary(
    Type=Name('/StructElem'),
    S=Name('/Document'),
    P=struct_tree,
    K=Array()
))
struct_tree['/K'].append(doc_elem)

def make_elem(parent, tag, page_ref):
    elem = pdf.make_indirect(Dictionary(
        Type=Name('/StructElem'),
        S=Name('/' + tag),
        P=parent,
        K=Array(),
        Pg=page_ref,
    ))
    parent['/K'].append(elem)
    return elem

# ── Phase 3: per-page processing ─────────────────────────────────────────────

sp_counter = 0
parent_tree_entries = {}
tags_created = {}
total_mcids = 0

for page_num, page in enumerate(pdf.pages):
    page_obj = page.obj
    fitz_tags = page_block_tags[page_num] if page_num < len(page_block_tags) else []
    
    raw = page_obj.get('/Contents')
    if raw is None:
        continue
    
    # Get list of stream objects for this page
    if isinstance(raw, Array):
        streams = [pdf.get_object(s.objgen) if hasattr(s, 'objgen') else s for s in raw]
    else:
        try:
            streams = [pdf.get_object(raw.objgen)]
        except AttributeError:
            streams = [raw]
    
    # Parse all instructions across all streams in order
    all_instructions = []
    stream_boundaries = []   # (start_idx, end_idx, stream_obj)
    for stream in streams:
        start = len(all_instructions)
        try:
            instrs = pikepdf.parse_content_stream(stream)
            all_instructions.extend(instrs)
        except Exception as e:
            # If parse fails, fall back to treating stream as opaque
            continue
        stream_boundaries.append((start, len(all_instructions), stream))
    
    # Build xobject type map for this page so process_instructions can
    # distinguish /Image from /Form XObjects at bare Do operators.
    # Must be built BEFORE bt_count/image_do_count so the image count is accurate.
    xobject_types = {}
    try:
        resources = page_obj.get('/Resources')
        if resources is not None:
            xobj_dict = resources.get('/XObject')
            if xobj_dict is not None:
                for name, ref in xobj_dict.items():
                    try:
                        xobj = pdf.get_object(ref.objgen)
                        subtype = str(xobj.get('/Subtype', '')).strip('/')
                        xobject_types['/' + name.lstrip('/')] = subtype
                    except Exception:
                        pass
    except Exception:
        pass

    # Count BT instructions (text blocks) and image Do operators.
    # Both consume MCIDs; only BT blocks consume from tag_iter.
    bt_count = sum(1 for instr in all_instructions if str(instr.operator) == 'BT')
    image_do_count = sum(
        1 for instr in all_instructions
        if str(instr.operator) == 'Do'
        and xobject_types.get(
            str(list(instr.operands)[0]) if instr.operands else '', 'Unknown'
        ) == 'Image'
    )

    if bt_count == 0 and image_do_count == 0:
        # No text or images — mark all streams as Artifact
        for stream in streams:
            try:
                instrs = pikepdf.parse_content_stream(stream)
                if instrs:
                    new_instrs = [make_artifact_bmc()] + list(instrs) + [make_emc()]
                    stream.write(pikepdf.unparse_content_stream(new_instrs))
            except Exception:
                pass
        continue
    
    # Assign StructParents
    sp = sp_counter
    sp_counter += 1
    page_obj['/StructParents'] = Integer(sp)
    
    # Build tag sequence (for text blocks only — image Do operators
    # are classified as Figure directly, not via tag_iter)
    tag_seq = list(fitz_tags)
    while len(tag_seq) < bt_count:
        tag_seq.append('P')
    tag_iter = iter(tag_seq)

    # Process all instructions across page
    new_all, mcids_on_page = process_instructions(
        all_instructions, total_mcids, tag_iter, xobject_types)
    
    # Now we need to write new instructions back to streams.
    # Simplest correct approach: write all new instructions into the first stream,
    # clear the others. This is safe because content streams are concatenated.
    if streams:
        first_stream = streams[0]
        first_stream.write(pikepdf.unparse_content_stream(new_all))
        # Clear other streams
        for stream in streams[1:]:
            stream.write(b'')
    
    # Tabs=/S for pages with annotations
    if page_obj.get('/Annots') is not None:
        page_obj['/Tabs'] = Name('/S')
    
    # Reconstruct the actual tag sequence from the processed instructions.
    # process_instructions may have injected Figure MCIDs from bare Do operators
    # that don't appear in tag_seq (which only covers BT/ET text blocks).
    # Walk new_all to extract the MCID→tag mapping in order.
    used_tags = []
    j = 0
    while j < len(new_all):
        instr = new_all[j]
        op = str(instr.operator)
        if op == 'BDC':
            ops = list(instr.operands)
            if len(ops) >= 2:
                tag_name = str(ops[0]).lstrip('/')
                props = ops[1]
                try:
                    mcid_val = int(props['/MCID'])
                    used_tags.append((mcid_val, tag_name))
                except Exception:
                    pass
        j += 1
    used_tags.sort(key=lambda x: x[0])
    page_mcid_map = {}
    current_sect = make_elem(doc_elem, 'Sect', page_obj)
    
    for mcid_val, tag in used_tags:
        if tag == 'H':
            current_sect = make_elem(doc_elem, 'Sect', page_obj)
            elem = make_elem(current_sect, 'H', page_obj)
            elem['/K'] = Array([Integer(mcid_val)])
            page_mcid_map[mcid_val] = elem
            tags_created['H'] = tags_created.get('H', 0) + 1
        elif tag == 'L':
            l_elem  = make_elem(current_sect, 'L', page_obj)
            li      = make_elem(l_elem, 'LI', page_obj)
            lbody   = make_elem(li, 'LBody', page_obj)
            lbody['/K'] = Array([Integer(mcid_val)])
            page_mcid_map[mcid_val] = lbody
            tags_created['L'] = tags_created.get('L', 0) + 1
        elif tag == 'Figure':
            fig = make_elem(current_sect, 'Figure', page_obj)
            # Intentionally set /Alt to empty string. Per ALT_TEXT_RULE.md,
            # the placeholder "[Figure N — alt text required]" is supposed to
            # be set by fix_figure_alt_text.py auto mode, NOT by this script.
            # Empty /Alt causes veraPDF 7.3 to fire, which triggers the
            # alt text repair pipeline (drafts → review → manual mode).
            # If we set a placeholder here, veraPDF accepts it as non-empty
            # and the alt text path never runs.
            fig['/Alt'] = String('')
            fig['/K'] = Array([Integer(mcid_val)])
            page_mcid_map[mcid_val] = fig
            tags_created['Figure'] = tags_created.get('Figure', 0) + 1
        else:
            elem = make_elem(current_sect, 'P', page_obj)
            elem['/K'] = Array([Integer(mcid_val)])
            page_mcid_map[mcid_val] = elem
            tags_created['P'] = tags_created.get('P', 0) + 1
    
    page_mcid_base = total_mcids
    total_mcids += mcids_on_page
    
    if page_mcid_map:
        max_mcid = max(page_mcid_map.keys())
        pt_array = Array([None] * (max_mcid - page_mcid_base + 1))
        for m, elem in page_mcid_map.items():
            pt_array[m - page_mcid_base] = pdf.make_indirect(elem)
        parent_tree_entries[sp] = pt_array

# ── Phase 4: write ParentTree ────────────────────────────────────────────────

nums = Array()
for key in sorted(parent_tree_entries):
    nums.append(Integer(key))
    nums.append(parent_tree_entries[key])
struct_tree['/ParentTree'] = pdf.make_indirect(Dictionary(Nums=nums))
struct_tree['/ParentTreeNextKey'] = Integer(sp_counter)

# ── Save ─────────────────────────────────────────────────────────────────────

pdf.save(args.output_pdf)
pdf.close()
Path(tmp).unlink(missing_ok=True)

result_obj = {
    'input': args.input_pdf,
    'output': args.output_pdf,
    'result': 'FIXED',
    'pages_processed': len(page_block_tags),
    'total_mcids': total_mcids,
    'elements_created': tags_created,
    'note': (
        'Structure tree generated using token-level content stream parsing. '
        'Run fix_struct_content_marking.py to verify ParentTree connectivity, '
        'then veraPDF to confirm.'
    )
}

result_str = json.dumps(result_obj, indent=2)
print(result_str)
if args.out:
    Path(args.out).write_text(result_str)
sys.exit(0)
