#!/usr/bin/env python3
"""
table_semantics_audit.py
Audits table structure for PDF/UA-1 compliance using two complementary passes:

Pass 1 — pdfplumber (geometric): detects visually present tables on each page
  from borders, lines, and character alignment. Identifies tables that exist
  visually but are absent from the structure tree (untagged tables).

Pass 2 — PyMuPDF (struct tree): walks the PDF structure tree to validate that
  tagged tables have correct TH Scope attributes and resolvable header chains.

The delta between Pass 1 and Pass 2 is the primary diagnostic output.
A page where pdfplumber finds more tables than the struct tree contains has
untagged tables that will fail veraPDF — run fix_table_headers.py after
manually tagging those tables.

NOTE on spanning tables: A table that spans multiple pages has no Pg attribute
on the Table struct element itself — only its TR/TH/TD children carry page
refs. This script handles spanning tables by walking children to determine
page coverage, and applies a global count guard to avoid false untagged
detections when a single struct tree table is visually present on N pages.

Usage: table_semantics_audit.py <pdf> [--out results.json]
"""
import sys, json, re, argparse
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except Exception:
    PDFPLUMBER_AVAILABLE = False

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--out', default=None, help='Write JSON output to this file in addition to stdout')
parser.add_argument('--no-pdfplumber', action='store_true',
                    help='Skip geometric pass (struct tree audit only)')
args = parser.parse_args()

pdf_path = args.pdf
issues = []

# ---------------------------------------------------------------------------
# Pass 1: pdfplumber geometric table detection
# ---------------------------------------------------------------------------

pdfplumber_results = {}
pdfplumber_ran = False
untagged_table_pages = []

if PDFPLUMBER_AVAILABLE and not args.no_pdfplumber:
    pdfplumber_ran = True
    try:
        with pdfplumber.open(pdf_path) as plumb_doc:
            for page_obj in plumb_doc.pages:
                page_num = page_obj.page_number  # 1-based

                # Skip image-only pages — pdfplumber cannot detect tables
                # on pages without a native text layer.
                has_text = len(page_obj.chars) > 0
                if not has_text:
                    pdfplumber_results[page_num] = {
                        'skipped': True,
                        'reason': 'image_only_page',
                        'visual_tables': 0
                    }
                    continue

                visual_tables = page_obj.find_tables()
                pdfplumber_results[page_num] = {
                    'skipped': False,
                    'visual_tables': len(visual_tables),
                    'table_bboxes': [
                        {
                            'x0': round(t.bbox[0], 1),
                            'y0': round(t.bbox[1], 1),
                            'x1': round(t.bbox[2], 1),
                            'y1': round(t.bbox[3], 1)
                        }
                        for t in visual_tables
                    ]
                }
    except Exception as e:
        pdfplumber_ran = False
        issues.append({
            'type': 'pdfplumber_error',
            'note': f'pdfplumber pass failed: {e} — struct tree audit continues'
        })

# ---------------------------------------------------------------------------
# Pass 2: PyMuPDF struct tree audit
# ---------------------------------------------------------------------------

doc = fitz.open(pdf_path)
tables_found = 0
th_cells_found = 0
th_missing_scope = 0

# Count struct tree tables per page for delta calculation
struct_tables_by_page = {}  # page_num (1-based) -> count

catalog = doc.pdf_catalog()
struct_tree_ref = doc.xref_get_key(catalog, 'StructTreeRoot')

if struct_tree_ref[0] == 'null' or not struct_tree_ref[1]:
    # No struct tree — pdfplumber results still valid as untagged table detection
    untagged_summary = {}
    total_visual = 0
    if pdfplumber_ran:
        for pg, data in pdfplumber_results.items():
            if not data.get('skipped') and data['visual_tables'] > 0:
                untagged_table_pages.append(pg)
                total_visual += data['visual_tables']
        untagged_summary = {
            'total_visual_tables_found': total_visual,
            'untagged_table_pages': untagged_table_pages,
            'note': 'Document has no struct tree — all visual tables are untagged'
        }

    output = json.dumps({
        'pdf': pdf_path,
        'result': 'FAIL' if untagged_table_pages else 'SKIPPED',
        'reason': 'No StructTreeRoot — document not tagged',
        'pdfplumber_ran': pdfplumber_ran,
        'pdfplumber_geometric': pdfplumber_results if pdfplumber_ran else None,
        'untagged_tables': untagged_summary if pdfplumber_ran else None,
    }, indent=2)
    print(output)
    if args.out:
        Path(args.out).write_text(output)
    sys.exit(1)


def get_kids_xrefs(xref, doc):
    kids = doc.xref_get_key(xref, 'K')
    if kids[0] == 'array':
        return [int(r) for r in re.findall(r'(\d+)\s+0\s+R', kids[1])]
    elif kids[0] == 'xref':
        return [int(kids[1].split()[0])]
    return []


def get_page_number_for_xref(xref, doc):
    """Attempt to find the 1-based page number for a struct element via Pg."""
    try:
        pg_ref = doc.xref_get_key(xref, 'Pg')
        if pg_ref[0] == 'xref':
            pg_xref = int(pg_ref[1].split()[0])
            for i in range(len(doc)):
                if doc[i].xref == pg_xref:
                    return i + 1
    except Exception:
        pass
    return None


def get_pages_for_table(table_xref, doc):
    """
    Return a set of 1-based page numbers covered by a Table struct element.

    Single-page tables have a Pg attribute on the Table element itself.
    Spanning tables do not — their Pg lives on TR/TH/TD children.
    This function handles both cases by first checking the direct Pg
    attribute and falling back to child-walking when Pg is absent.
    """
    pages = set()

    direct_pg = get_page_number_for_xref(table_xref, doc)
    if direct_pg is not None:
        pages.add(direct_pg)
        return pages

    # No direct Pg — walk children up to 5 levels deep to collect page refs
    def collect_pages(xref, depth=0):
        if depth > 5:
            return
        try:
            pg = get_page_number_for_xref(xref, doc)
            if pg is not None:
                pages.add(pg)
            for kid_xref in get_kids_xrefs(xref, doc):
                collect_pages(kid_xref, depth + 1)
        except Exception:
            pass

    collect_pages(table_xref)
    return pages


def walk_for_type(xref, doc, target_types):
    try:
        s_type = doc.xref_get_key(xref, 'S')
        clean = s_type[1].strip('/').strip() if s_type[0] != 'null' else ''
        if clean in target_types:
            yield xref, clean
        for kid_xref in get_kids_xrefs(xref, doc):
            yield from walk_for_type(kid_xref, doc, target_types)
    except Exception:
        return


struct_root_xref = int(struct_tree_ref[1].split()[0])
spanning_tables = []  # track tables with no direct Pg (spanning tables)

for xref, s_type in walk_for_type(struct_root_xref, doc, {'Table', 'TH', 'TD'}):
    if s_type == 'Table':
        tables_found += 1
        table_pages = get_pages_for_table(xref, doc)
        if table_pages:
            for pg in table_pages:
                struct_tables_by_page[pg] = struct_tables_by_page.get(pg, 0) + 1
            # Flag spanning tables (cover more than one page) for reporting
            if len(table_pages) > 1:
                spanning_tables.append({
                    'xref': xref,
                    'pages': sorted(table_pages)
                })
        # If no pages found at all, table_pages is empty — global count
        # guard below will prevent false untagged detection

    elif s_type == 'TH':
        th_cells_found += 1
        attrs = doc.xref_get_key(xref, 'A')
        scope_present = False
        if attrs[0] != 'null':
            if attrs[0] == 'xref':
                # Resolve indirect attribute dictionary
                try:
                    target_xref = int(attrs[1].split()[0])
                    obj_str = doc.xref_object(target_xref)
                    scope_present = '/Scope' in obj_str
                except Exception:
                    scope_present = False
            else:
                scope_present = 'Scope' in attrs[1]
        if not scope_present:
            th_missing_scope += 1
            pg = get_page_number_for_xref(xref, doc)
            issues.append({
                'xref': xref,
                'page': pg,
                'type': 'TH_missing_scope',
                'note': 'TH cell has no Scope attribute — run fix_table_headers.py'
            })

# ---------------------------------------------------------------------------
# Cross-reference: geometric vs struct tree
# ---------------------------------------------------------------------------

delta_by_page = {}
untagged_table_pages = []
total_visual_tables = 0

if pdfplumber_ran:
    all_pages = set(list(pdfplumber_results.keys()) + list(struct_tables_by_page.keys()))
    for pg in sorted(all_pages):
        plumb_data = pdfplumber_results.get(pg, {})
        if plumb_data.get('skipped'):
            continue
        visual = plumb_data.get('visual_tables', 0)
        tagged = struct_tables_by_page.get(pg, 0)
        delta = visual - tagged
        total_visual_tables += visual
        delta_by_page[pg] = {
            'visual_tables': visual,
            'struct_tree_tables': tagged,
            'delta': delta
        }

    # ── Global count guard ────────────────────────────────────────────────
    # A spanning table is visually present on N pages but is one element in
    # the struct tree with no Pg on the Table node. After child-walking,
    # struct_tables_by_page may still undercount if child Pg refs are absent.
    # Guard: if total struct tables >= 1 AND the only per-page deltas are on
    # pages that are fully explained by spanning_tables page coverage, do not
    # flag them as untagged — report as spanning instead.
    spanning_pages = set()
    for st in spanning_tables:
        for pg in st['pages']:
            spanning_pages.add(pg)

    for pg in sorted(delta_by_page.keys()):
        delta = delta_by_page[pg]['delta']
        if delta <= 0:
            continue

        # Check if this delta is explained by a spanning table
        if pg in spanning_pages:
            # Spanning table accounts for this visual detection — not untagged
            delta_by_page[pg]['spanning_table_note'] = (
                'Visual table on this page is part of a struct tree table '
                'that spans multiple pages — not an untagged table.'
            )
            continue

        # Check global count: if struct has enough tables overall and this
        # page's visual tables appear to be continuations of a spanning table
        # (i.e. tables_found >= 1 and all visual appearances are on pages
        # where spanning tables were detected), suppress false positive.
        if tables_found >= 1 and total_visual_tables <= tables_found * len(doc):
            # Remaining check: are the untagged pages contiguous with
            # pages that DO have struct table registrations?
            flagged_pages_set = {p for p, d in delta_by_page.items() if d['delta'] > 0}
            struct_pages_set = set(struct_tables_by_page.keys())
            # If no struct pages registered but tables_found > 0, it means
            # ALL table elements had no direct Pg and child-walking also
            # found no page refs — very unusual, flag conservatively.
            if not struct_pages_set and tables_found > 0:
                delta_by_page[pg]['spanning_table_note'] = (
                    'Table struct element found but page reference unresolvable. '
                    'Likely a spanning table — verify manually.'
                )
                continue

        untagged_table_pages.append(pg)
        issues.append({
            'page': pg,
            'type': 'untagged_tables_detected',
            'visual_tables': delta_by_page[pg]['visual_tables'],
            'struct_tree_tables': delta_by_page[pg]['struct_tree_tables'],
            'delta': delta,
            'note': (
                f'Page {pg}: pdfplumber found {delta_by_page[pg]["visual_tables"]} '
                f'visual table(s), struct tree has {delta_by_page[pg]["struct_tree_tables"]}. '
                f'{delta} table(s) appear untagged — manual tagging required '
                f'before fix_table_headers.py can repair header scope.'
            )
        })

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

# Classify issues by type so we can distinguish fixable struct failures
# from manual-only gaps (untagged visual tables).
#
# PASS            — no issues at all
# REVIEW_REQUIRED — untagged visual tables detected but th_missing_scope == 0
#                   and no other struct issues; veraPDF may still pass, but
#                   tables need manual tagging before full compliance.
# FAIL            — TH cells missing Scope, or other struct tree failures
#                   that repair scripts can address.

th_scope_issues    = [i for i in issues if i.get('type') == 'TH_missing_scope']
untagged_issues    = [i for i in issues if i.get('type') == 'untagged_tables_detected']
other_issues       = [i for i in issues
                      if i.get('type') not in ('TH_missing_scope', 'untagged_tables_detected')]

if not issues:
    result = 'PASS'
elif th_scope_issues or other_issues:
    result = 'FAIL'
else:
    # Only untagged_tables_detected issues — manual gap, not an auto-repair failure
    result = 'REVIEW_REQUIRED'

output_obj = {
    'pdf':                      pdf_path,
    'result':                   result,
    # Struct tree summary
    'struct_tree_tables_found': tables_found,
    'th_cells_found':           th_cells_found,
    'th_missing_scope':         th_missing_scope,
    'spanning_tables':          spanning_tables,
    # Geometric summary
    'pdfplumber_ran':           pdfplumber_ran,
    'total_visual_tables':      total_visual_tables if pdfplumber_ran else None,
    'untagged_table_pages':     untagged_table_pages if pdfplumber_ran else None,
    # Delta detail (per page)
    'page_delta':               delta_by_page if pdfplumber_ran else None,
    # All issues (struct + genuinely untagged)
    'issues':                   issues[:50],
    'issue_count':              len(issues)
}

output = json.dumps(output_obj, indent=2)
print(output)

if args.out:
    Path(args.out).write_text(output)

sys.exit(0 if result in ('PASS', 'REVIEW_REQUIRED') else 1)
