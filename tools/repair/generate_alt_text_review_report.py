#!/usr/bin/env python3
"""
generate_alt_text_review_report.py
Generates a self-contained interactive HTML review report from
alt_map_draft.json and the source PDF's figure thumbnails.

The report allows a reviewer to:
  - See each figure thumbnail alongside its draft alt text
  - Flag individual figures with a correction instruction
  - Click Accept all  -> downloads alt_map_approved.json
  - Click Resubmit    -> downloads alt_map_instructions.json for re-processing

No action (closing the browser without clicking) = case closed.
The pre-generated alt_map_approved.json already written alongside the
report represents the accepted state if no reviewer action is taken.

Usage:
  generate_alt_text_review_report.py <pdf>
    --draft   <alt_map_draft.json>
    --out     <alt_text_review.html>
    --map-out <alt_map_approved.json>
    [--dpi 120]

Exit codes:
  0  report written successfully
  2  error
"""
import sys, json, argparse, base64
from pathlib import Path

try:
    import fitz
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'PyMuPDF unavailable: {e}'}))
    sys.exit(2)

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--draft',   required=True,  help='alt_map_draft.json from generate_alt_text_drafts.py')
parser.add_argument('--out',     required=True,  help='Output HTML report path')
parser.add_argument('--map-out', required=True,  help='Pre-generated alt_map_approved.json path')
parser.add_argument('--dpi',     type=int, default=120)
args = parser.parse_args()

try:
    draft_data = json.loads(Path(args.draft).read_text())
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Could not read draft: {e}'}))
    sys.exit(2)

try:
    doc = fitz.open(args.pdf)
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Could not open PDF: {e}'}))
    sys.exit(2)

figures    = draft_data.get('figures', {})
pdf_name   = Path(args.pdf).name
model_name = draft_data.get('model', 'vision model')

# ── Render thumbnails ─────────────────────────────────────────────────────────

def render_thumb(doc, page_num: int, xref: int, dpi: int) -> str:
    try:
        page = doc[page_num - 1]
        for img_info in page.get_images(full=True):
            if img_info[0] == xref:
                bbox = page.get_image_bbox(img_info[7])
                if bbox and not bbox.is_empty:
                    mat = fitz.Matrix(dpi / 72, dpi / 72)
                    pix = page.get_pixmap(matrix=mat, clip=fitz.Rect(bbox), alpha=False)
                    return base64.b64encode(pix.tobytes('png')).decode()
        mat = fitz.Matrix(72 / 72, 72 / 72)
        pix = doc[page_num - 1].get_pixmap(matrix=mat, alpha=False)
        return base64.b64encode(pix.tobytes('png')).decode()
    except Exception:
        return ''

# Build figure list with thumbnails
fig_list = []
for idx_str, fig in sorted(figures.items(), key=lambda x: int(x[0])):
    thumb = render_thumb(doc, fig.get('page', 1), fig.get('xref', 0), args.dpi)
    fig_list.append({
        'idx':        idx_str,
        'page':       fig.get('page', '?'),
        'alt':        fig.get('alt_text_draft', ''),
        'source':     fig.get('source', 'vision_model'),
        'decorative': fig.get('decorative', False),
        'model':      fig.get('model', model_name),
        'thumb_b64':  thumb,
    })

doc.close()

# ── Pre-generate alt_map_approved.json ────────────────────────────────────────
# This is the operative document if reviewer takes no action.

pre_approved = {
    'reviewer':    None,
    'reviewed_at': None,
    'action':      'accepted',
    'note':        'Pre-generated. Represents accepted state if no reviewer action taken.',
    'figures':     {
        f['idx']: {
            'flagged':     False,
            'alt_text':    None if f['decorative'] else f['alt'],
            'decorative':  f['decorative'],
            'instruction': None,
        }
        for f in fig_list
    }
}
Path(args.map_out).write_text(json.dumps(pre_approved, indent=2))

# ── Build HTML ────────────────────────────────────────────────────────────────

def badge(source: str, decorative: bool) -> str:
    if decorative:
        return '<span class="badge badge-decor">auto: decorative</span>'
    if source == 'vision_model':
        return '<span class="badge badge-draft">vision draft</span>'
    if source == 'existing':
        return '<span class="badge badge-existing">existing</span>'
    return '<span class="badge badge-placeholder">placeholder</span>'

def fig_card(f: dict) -> str:
    idx       = f['idx']
    thumb_src = f'data:image/png;base64,{f["thumb_b64"]}' if f['thumb_b64'] else ''
    thumb_html = (
        f'<img src="{thumb_src}" alt="Figure {idx} thumbnail" '
        f'style="max-width:100%;max-height:100%;object-fit:contain;">'
        if thumb_src else
        '<span style="font-size:11px;color:var(--c-muted)">No preview</span>'
    )
    alt_display = f['alt'] if not f['decorative'] else 'Decorative — will be artifacted'
    alt_style   = 'color:var(--c-muted);font-style:italic;' if f['decorative'] else 'font-style:italic;'

    return f'''
<div class="fig-card" id="card-{idx}" data-idx="{idx}" data-decorative="{'1' if f['decorative'] else '0'}">
  <div class="fig-inner">
    <div class="fig-thumb">{thumb_html}</div>
    <div class="fig-body">
      <div class="fig-top">
        <span class="fig-title">Figure {int(idx)+1} &middot; p.{f['page']}</span>
        {badge(f['source'], f['decorative'])}
      </div>
      <div class="alt-label">Alt text</div>
      <div class="alt-text" style="{alt_style}">{alt_display}</div>
      <div class="instruction-row">
        <input type="checkbox" id="f{idx}" onchange="toggleFlag('{idx}',this)">
        <label for="f{idx}">Flag</label>
        <input type="text" id="inst{idx}"
               placeholder="Instruction for agent e.g. &quot;replace with: …&quot; or &quot;not decorative&quot;"
               onfocus="autoCheck('{idx}')" oninput="autoCheck('{idx}')" />
      </div>
    </div>
  </div>
</div>'''

cards_html = '\n'.join(fig_card(f) for f in fig_list)

# Embed fig_list data as JSON for the JS
fig_data_json = json.dumps([{
    'idx':        f['idx'],
    'alt':        f['alt'],
    'decorative': f['decorative'],
} for f in fig_list])

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alt text review — {pdf_name}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --c-bg:       #ffffff;
  --c-surface:  #f5f5f3;
  --c-border:   rgba(0,0,0,0.12);
  --c-border2:  rgba(0,0,0,0.22);
  --c-text:     #1a1a1a;
  --c-muted:    #6b6b6b;
  --c-hint:     #9b9b9b;
  --c-green:    #0F6E56;
  --c-green-bg: #EAF3DE;
  --c-amber:    #854F0B;
  --c-amber-bg: #FAEEDA;
  --c-amber-bd: #BA7517;
  --c-red:      #A32D2D;
  --c-red-bg:   #FCEBEB;
  --radius:     8px;
  --radius-lg:  12px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
  color: var(--c-text);
  background: var(--c-bg);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --c-bg:       #1a1a1a;
    --c-surface:  #242424;
    --c-border:   rgba(255,255,255,0.1);
    --c-border2:  rgba(255,255,255,0.2);
    --c-text:     #e8e8e8;
    --c-muted:    #a0a0a0;
    --c-hint:     #666;
    --c-green-bg: #0a2018;
    --c-amber-bg: #1e1408;
    --c-red-bg:   #1e0a0a;
  }}
}}
body {{ max-width: 820px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
.page-header {{ margin-bottom: 1.5rem; }}
.page-header h1 {{ font-size: 17px; font-weight: 500; }}
.page-header p  {{ font-size: 13px; color: var(--c-muted); margin-top: 4px; }}
.no-action-note {{
  font-size: 12px; color: var(--c-muted);
  background: var(--c-surface);
  border-radius: var(--radius); padding: 8px 12px; margin-bottom: 1rem;
  border-left: 3px solid var(--c-border2);
}}
.reviewer-row {{
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px;
  background: var(--c-surface);
  border: 0.5px solid var(--c-border);
  border-radius: var(--radius); margin-bottom: 1rem;
}}
.reviewer-row label {{ font-size: 13px; color: var(--c-muted); }}
.reviewer-row input {{ font-size: 13px; padding: 4px 10px; border-radius: var(--radius); border: 0.5px solid var(--c-border2); background: var(--c-bg); color: var(--c-text); width: 200px; }}
.flag-count {{ font-size: 12px; color: var(--c-muted); margin-left: auto; }}
.figures {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 1.5rem; }}
.fig-card {{ border: 0.5px solid var(--c-border); border-radius: var(--radius-lg); background: var(--c-bg); overflow: hidden; transition: border-color 0.15s; }}
.fig-card.flagged {{ border-color: var(--c-amber-bd); }}
.fig-inner {{ display: grid; grid-template-columns: 120px 1fr; }}
.fig-thumb {{ background: var(--c-surface); border-right: 0.5px solid var(--c-border); display: flex; align-items: center; justify-content: center; min-height: 110px; padding: 10px; }}
.fig-body {{ padding: 10px 14px; display: flex; flex-direction: column; gap: 7px; }}
.fig-top {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
.fig-title {{ font-size: 13px; font-weight: 500; }}
.badge {{ font-size: 11px; padding: 2px 7px; border-radius: 99px; font-weight: 500; }}
.badge-draft {{ background: var(--c-amber-bg); color: var(--c-amber); }}
.badge-placeholder {{ background: var(--c-red-bg); color: var(--c-red); }}
.badge-existing {{ background: var(--c-green-bg); color: var(--c-green); }}
.badge-decor {{ background: var(--c-surface); color: var(--c-muted); border: 0.5px solid var(--c-border2); }}
.alt-label {{ font-size: 11px; color: var(--c-muted); }}
.alt-text {{ font-size: 13px; line-height: 1.5; background: var(--c-surface); border-radius: var(--radius); padding: 6px 10px; }}
.instruction-row {{ display: flex; align-items: center; gap: 8px; border-top: 0.5px solid var(--c-border); padding-top: 7px; }}
.instruction-row input[type=checkbox] {{ width: 14px; height: 14px; flex-shrink: 0; cursor: pointer; accent-color: var(--c-amber-bd); }}
.instruction-row label {{ font-size: 12px; color: var(--c-muted); cursor: pointer; white-space: nowrap; }}
.instruction-row input[type=text] {{
  flex: 1; font-size: 12px; padding: 4px 9px;
  border-radius: var(--radius); border: 0.5px solid var(--c-border);
  background: var(--c-surface); color: var(--c-muted);
  font-family: inherit; transition: border-color 0.15s, background 0.15s, color 0.15s;
}}
.instruction-row input[type=text]:focus,
.instruction-row input[type=text].active {{
  outline: none; border-color: var(--c-amber-bd);
  background: var(--c-bg); color: var(--c-text);
}}
.footer {{ display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding-top: 1rem; border-top: 0.5px solid var(--c-border); }}
.footer-note {{ font-size: 12px; color: var(--c-muted); }}
.btn-row {{ display: flex; gap: 8px; }}
.btn {{ font-size: 13px; padding: 7px 16px; border-radius: var(--radius); border: 0.5px solid var(--c-border2); background: var(--c-bg); color: var(--c-text); cursor: pointer; font-weight: 500; font-family: inherit; }}
.btn:hover {{ background: var(--c-surface); }}
.btn:disabled {{ opacity: 0.4; cursor: not-allowed; background: var(--c-bg) !important; }}
.btn-accept {{ border-color: var(--c-green); color: var(--c-green); }}
.btn-accept:hover {{ background: var(--c-green-bg); }}
.btn-resub {{ border-color: var(--c-amber-bd); color: var(--c-amber); }}
.btn-resub:hover {{ background: var(--c-amber-bg); }}
</style>
</head>
<body>
<h2 class="sr-only" style="position:absolute;left:-9999px">Alt text review report for {pdf_name}</h2>

<div class="page-header">
  <h1>Alt text review — {pdf_name}</h1>
  <p>{len(fig_list)} figure{"s" if len(fig_list) != 1 else ""} &middot; model: {model_name} &middot; <span id="flag-count">0 flagged</span></p>
</div>

<div class="no-action-note">
  If you close this page without clicking a button, the pre-generated
  <strong>alt_map_approved.json</strong> already written alongside this report
  will be used as-is. Changes are only saved when you click Accept all or Resubmit flagged.
</div>

<div class="reviewer-row">
  <label for="rv">Reviewer</label>
  <input type="text" id="rv" placeholder="Name or initials — required to download" />
  <span class="flag-count" id="flag-summary">nothing flagged</span>
</div>

<div class="figures">
{cards_html}
</div>

<div class="footer">
  <span class="footer-note" id="footer-note">Ready — click Accept all or flag figures that need correction</span>
  <div class="btn-row">
    <button class="btn btn-resub" id="btn-resub" disabled onclick="doResubmit()">Resubmit flagged</button>
    <button class="btn btn-accept" onclick="doAccept()">Accept all</button>
  </div>
</div>

<script>
const FIG_DATA = {fig_data_json};
const flagged = {{}};

function autoCheck(idx) {{
  const inst = document.getElementById('inst'+idx);
  const cb   = document.getElementById('f'+idx);
  if (inst.value.trim() && !cb.checked) {{ cb.checked = true; flagged[idx] = true; }}
  inst.classList.toggle('active', cb.checked);
  document.getElementById('card-'+idx).classList.toggle('flagged', !!flagged[idx]);
  updateSummary();
}}

function toggleFlag(idx, cb) {{
  flagged[idx] = cb.checked;
  document.getElementById('inst'+idx).classList.toggle('active', cb.checked);
  document.getElementById('card-'+idx).classList.toggle('flagged', cb.checked);
  if (cb.checked) document.getElementById('inst'+idx).focus();
  updateSummary();
}}

function updateSummary() {{
  const n = Object.values(flagged).filter(Boolean).length;
  document.getElementById('flag-count').textContent = n + ' flagged';
  document.getElementById('flag-summary').textContent = n === 0 ? 'nothing flagged' : n + ' flagged';
  document.getElementById('footer-note').textContent = n === 0
    ? 'Ready — click Accept all or flag figures that need correction'
    : n + ' flagged \u00b7 ' + (FIG_DATA.length - n) + ' will be accepted as-is';
  document.getElementById('btn-resub').disabled = n === 0;
}}

function getReviewer() {{
  const v = document.getElementById('rv').value.trim();
  if (!v) {{ document.getElementById('rv').focus(); document.getElementById('rv').style.borderColor='var(--c-red)'; return null; }}
  return v;
}}

function dl(name, obj) {{
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{{type:'application/json'}}));
  a.download = name; a.click();
}}

function buildFigures(onlyFlagged) {{
  const out = {{}};
  FIG_DATA.forEach(f => {{
    if (onlyFlagged && !flagged[f.idx]) return;
    const inst = (document.getElementById('inst'+f.idx)||{{}}).value || '';
    out[f.idx] = {{
      flagged:     !!flagged[f.idx],
      alt_text:    f.decorative ? null : f.alt,
      decorative:  f.decorative,
      instruction: inst.trim() || null,
    }};
  }});
  return out;
}}

function doAccept() {{
  const r = getReviewer(); if (!r) return;
  dl('alt_map_approved.json', {{
    reviewer: r, reviewed_at: new Date().toISOString(),
    action: 'accepted', figures: buildFigures(false)
  }});
}}

function doResubmit() {{
  const r = getReviewer(); if (!r) return;
  dl('alt_map_instructions.json', {{
    reviewer: r, reviewed_at: new Date().toISOString(),
    action: 'resubmit', figures: buildFigures(true)
  }});
}}
</script>
</body>
</html>'''

Path(args.out).write_text(html)

output = json.dumps({
    'result':          'PASS',
    'report':          args.out,
    'pre_approved_map': args.map_out,
    'figures_in_report': len(fig_list),
    'note':            (
        'Open the HTML report in a browser to review. '
        'If no action is taken, alt_map_approved.json is used as-is.'
    )
}, indent=2)
print(output)
sys.exit(0)
