#!/usr/bin/env python3
"""
generate_alt_text_drafts.py
Uses the configured vision model to generate draft alt text for Figure
elements that received placeholder text during fix_figure_alt_text.py
auto-mode repair.

Reads the needs_review list from fix_figure_alt_text.py output, renders
each figure as a thumbnail, sends it to the vision model with a structured
prompt, and writes alt_map_draft.json to the job's audit directory.

Output is explicitly a DRAFT requiring human review via
generate_alt_text_review_report.py before fix_figure_alt_text.py applies it.

Usage:
  generate_alt_text_drafts.py <pdf> --fix-output <fix_figure_output.json>
                               --out <alt_map_draft.json>
                               [--instructions <alt_map_instructions.json>]
                               [--dpi 150]

Exit codes:
  0  success — alt_map_draft.json written
  1  partial — some figures failed, others succeeded
  2  error — could not proceed
"""
import sys, json, argparse, os, base64
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

try:
    import urllib.request
    import urllib.error
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'urllib unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--fix-output', required=True,
                    help='JSON output from fix_figure_alt_text.py (contains needs_review list)')
parser.add_argument('--out', required=True,
                    help='Write alt_map_draft.json here')
parser.add_argument('--instructions', default=None,
                    help='alt_map_instructions.json from a previous review cycle — '
                         'overrides vision model prompt for flagged figures')
parser.add_argument('--dpi', type=int, default=150,
                    help='DPI for thumbnail rendering (default: 150)')
args = parser.parse_args()

# ── Environment ───────────────────────────────────────────────────────────────

VISION_BASE_URL  = os.environ.get('VISION_PROVIDER_BASE_URL') or \
                   os.environ.get('PRIMARY_PROVIDER_BASE_URL', '')
VISION_API_KEY   = os.environ.get('VISION_PROVIDER_API_KEY') or \
                   os.environ.get('PRIMARY_PROVIDER_API_KEY', '')
VISION_MODEL     = os.environ.get('VISION_MODEL', '')

if not VISION_BASE_URL or not VISION_API_KEY or not VISION_MODEL:
    print(json.dumps({
        'result': 'ERROR',
        'error': (
            'Vision model not configured. Set VISION_PROVIDER_BASE_URL (or '
            'PRIMARY_PROVIDER_BASE_URL), VISION_PROVIDER_API_KEY (or '
            'PRIMARY_PROVIDER_API_KEY), and VISION_MODEL environment variables.'
        )
    }, indent=2))
    sys.exit(2)

# ── Load inputs ───────────────────────────────────────────────────────────────

try:
    fix_output = json.loads(Path(args.fix_output).read_text())
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Could not read fix output: {e}'}))
    sys.exit(2)

needs_review = fix_output.get('needs_review', [])
if not needs_review:
    output = json.dumps({
        'result': 'SKIPPED',
        'note':   'No figures in needs_review list — nothing to generate drafts for.',
        'figures': {}
    }, indent=2)
    print(output)
    Path(args.out).write_text(output)
    sys.exit(0)

# Load previous instructions if provided (resubmit cycle)
instructions = {}
if args.instructions:
    try:
        inst_data = json.loads(Path(args.instructions).read_text())
        instructions = {
            str(k): v.get('instruction', '')
            for k, v in inst_data.get('figures', {}).items()
            if v.get('instruction')
        }
    except Exception as e:
        print(json.dumps({'result': 'ERROR', 'error': f'Could not read instructions: {e}'}))
        sys.exit(2)

try:
    doc = fitz.open(args.pdf)
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Could not open PDF: {e}'}))
    sys.exit(2)

# ── Vision model call ─────────────────────────────────────────────────────────

def call_vision_model(image_b64: str, figure_index: int, instruction: str = '') -> str:
    """Send a rendered figure thumbnail to the vision model, return alt text string."""

    if instruction:
        user_content = (
            f'This is Figure {figure_index + 1} from a clinical healthcare PDF document. '
            f'Generate alt text following this instruction: {instruction}. '
            f'Return only the alt text string, nothing else.'
        )
    else:
        user_content = (
            f'This is Figure {figure_index + 1} from a clinical healthcare PDF document. '
            f'Write a concise, accurate alt text description for this image. '
            f'Requirements: describe what the image shows (chart type, subject, key values if '
            f'readable); do not describe style or decorative elements; do not identify any '
            f'people; keep under 150 characters where possible; if the image is decorative '
            f'(divider, background, purely ornamental) respond with exactly: DECORATIVE. '
            f'Return only the alt text string, nothing else.'
        )

    payload = json.dumps({
        'model': VISION_MODEL,
        'max_tokens': 300,
        'messages': [{
            'role': 'user',
            'content': [
                {
                    'type':       'image_url',
                    'image_url':  {'url': f'data:image/png;base64,{image_b64}'}
                },
                {
                    'type': 'text',
                    'text': user_content
                }
            ]
        }]
    }).encode('utf-8')

    endpoint = VISION_BASE_URL.rstrip('/') + '/chat/completions'
    req = urllib.request.Request(
        endpoint,
        data    = payload,
        headers = {
            'Content-Type':  'application/json',
            'Authorization': f'Bearer {VISION_API_KEY}',
        },
        method = 'POST'
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    return data['choices'][0]['message']['content'].strip()


def render_figure_thumbnail(doc, page_num: int, xref: int, dpi: int) -> str | None:
    """Render the bounding box of an image xref on a page, return base64 PNG."""
    try:
        page = doc[page_num]
        # Find the image on the page and get its bounding box
        for img_info in page.get_images(full=True):
            if img_info[0] == xref:
                name = img_info[7]
                bbox = page.get_image_bbox(name)
                if bbox and not bbox.is_empty:
                    mat  = fitz.Matrix(dpi / 72, dpi / 72)
                    clip = fitz.Rect(bbox)
                    pix  = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
                    return base64.b64encode(pix.tobytes('png')).decode('utf-8')
        # Fallback: render the full page at lower DPI
        mat = fitz.Matrix(72 / 72, 72 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return base64.b64encode(pix.tobytes('png')).decode('utf-8')
    except Exception:
        return None


# ── Process each figure ───────────────────────────────────────────────────────

# Build xref -> page map from doc
xref_to_page = {}
for page_num in range(len(doc)):
    for img_info in doc[page_num].get_images(full=True):
        xref = img_info[0]
        if xref not in xref_to_page:
            xref_to_page[xref] = page_num

results   = {}
errors    = []
generated = 0
skipped   = 0

for item in needs_review:
    fig_idx  = item.get('figure_index', 0)
    xref     = item.get('xref', 0)
    page_num = xref_to_page.get(xref, 0)
    inst     = instructions.get(str(fig_idx), '')

    # Render thumbnail
    thumb_b64 = render_figure_thumbnail(doc, page_num, xref, args.dpi)
    if thumb_b64 is None:
        errors.append({
            'figure_index': fig_idx,
            'error':        'Could not render thumbnail — figure skipped'
        })
        skipped += 1
        continue

    # Call vision model
    try:
        draft = call_vision_model(thumb_b64, fig_idx, inst)
        results[str(fig_idx)] = {
            'figure_index':   fig_idx,
            'page':           page_num + 1,
            'xref':           xref,
            'alt_text_draft': draft,
            'source':         'vision_model',
            'model':          VISION_MODEL,
            'instruction':    inst or None,
            'decorative':     draft.strip().upper() == 'DECORATIVE',
        }
        generated += 1
    except Exception as e:
        errors.append({'figure_index': fig_idx, 'error': str(e)})
        skipped += 1

doc.close()

overall = 'PASS' if not errors else ('PARTIAL' if generated > 0 else 'FAIL')

output = json.dumps({
    'result':          overall,
    'pdf':             args.pdf,
    'model':           VISION_MODEL,
    'figures_total':   len(needs_review),
    'figures_drafted': generated,
    'figures_skipped': skipped,
    'draft_note':      (
        'All alt text in this file is DRAFT output from a vision model. '
        'Human review and approval is required via generate_alt_text_review_report.py '
        'before these descriptions are applied to the document.'
    ),
    'figures':         results,
    'errors':          errors,
}, indent=2)

print(output)
Path(args.out).write_text(output)
sys.exit(0 if overall == 'PASS' else (1 if overall == 'PARTIAL' else 2))
