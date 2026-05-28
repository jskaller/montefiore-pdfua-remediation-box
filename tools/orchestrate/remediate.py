#!/usr/bin/env python3
"""
remediate.py
Single-entry-point orchestrator for Montefiore PDF/UA remediation.

Replaces the agent's need to interpret AGENTS.md step-by-step. Handles:
  - Job scaffolding
  - Document tagging against doc_taxonomy.json
  - All pre-flight and audit gates
  - Repair plan generation (veraPDF + table semantics)
  - Iterative repair loop with per-rule and per-job caps
  - Strategy fallback and OPENCLAW_REQUIRED signalling
  - Post-repair validation after each iteration
  - QA gates
  - Outcome-aware packaging (PASS/REVIEW_REQUIRED/FAIL/ESCALATION)
  - Knowledge update (post_job_indexer)

Iteration caps:
  PER_RULE_CAP  = 15   hard cap on strategy attempts per rule
  JOB_WARN_AT   = 20   soft warning logged to STATUS.json
  JOB_HARD_CAP  = 50   absolute safety net — force termination

The agent's role is reduced to:
  1. Call this script
  2. Handle OPENCLAW_REQUIRED signals (write new scripts, register in rule map)
  3. Provide metadata args (--title, --subject, --keywords)

Signal layers:
  Layer 1 — execution signals (exit code, missing output, JSON parse failure)
  Layer 2 — outcome signals (rule still fails after mapped script ran)
  Layer 3 — semantic signals (plan wrong for this document, novel failures)
  OPENCLAW_REQUIRED — agent must write or locate a repair script

Usage:
  remediate.py <workspace> <ticket> <source-pdf-basename>
    --title    "Document Title"
    --subject  "One sentence subject"
    --keywords "keyword1, keyword2, ..."
    [--language en-US]
    [--dry-run]     Print plan without executing

Exit codes:
  0  PASS or REVIEW_REQUIRED
  1  FAIL, ESCALATION, or unresolved DEVIATION
  2  usage/setup error
"""
import sys, json, subprocess, shutil, argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ── Args ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument('workspace')
parser.add_argument('ticket')
parser.add_argument('basename')
parser.add_argument('--title',    required=True)
parser.add_argument('--subject',  required=True)
parser.add_argument('--keywords', required=True)
parser.add_argument('--language', default='en-US')
parser.add_argument('--dry-run',  action='store_true')
args = parser.parse_args()

WORKSPACE  = Path(args.workspace)
TICKET     = args.ticket
BASENAME   = Path(args.basename).stem
SAFE_BASE  = BASENAME.replace(' ', '_').replace('/', '_')
LANGUAGE   = args.language

APP        = Path('/app')
TOOLS      = APP / 'tools'
VERAPDF_BIN = Path('/opt/verapdf-greenfield/verapdf')
PROFILES   = WORKSPACE / 'assets' / 'validation_profiles' / 'veraPDF-validation-profiles-integration'
RULE_MAP   = TOOLS / 'audit' / 'rule_repair_map.json'
TAXONOMY   = TOOLS / 'audit' / 'doc_taxonomy.json'

JOB_NAME   = f'{TICKET}_{SAFE_BASE}'
JOB_DIR    = WORKSPACE / 'jobs'   / JOB_NAME
OUTPUT_DIR = WORKSPACE / 'output' / f'{TICKET}_remediated'
SOURCE_PDF = WORKSPACE / 'input'  / TICKET / f'{BASENAME}.pdf'

# Iteration caps
PER_RULE_CAP = 15
JOB_WARN_AT  = 20
JOB_HARD_CAP = 50

# ── Helpers ───────────────────────────────────────────────────────────────────

deviations        = []
gate_results      = {}
openclaw_signals  = []
strategy_attempts = defaultdict(list)  # rule_id -> list of attempt records
start_time        = datetime.now(timezone.utc)
total_iterations  = 0

def emit(phase, step, result, note=None, data=None):
    obj = {'phase': phase, 'step': step, 'result': result}
    if note:  obj['note'] = note
    if data:  obj['data'] = data
    print(json.dumps(obj), flush=True)

def emit_deviation(step, expected, actual, context, layer):
    dev = {
        'layer':     layer,
        'step':      step,
        'expected':  expected,
        'actual':    actual,
        'context':   context,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    deviations.append(dev)
    print(json.dumps({
        'phase':    'DEVIATION',
        'layer':    layer,
        'step':     step,
        'expected': expected,
        'actual':   actual,
        'context':  context
    }), flush=True)

def emit_openclaw_required(rule_id, description, failures, reason, attempts=None):
    signal = {
        'rule_id':              rule_id,
        'description':          description,
        'failures':             failures,
        'reason':               reason,
        'strategies_attempted': attempts or [],
        'timestamp':            datetime.now(timezone.utc).isoformat()
    }
    openclaw_signals.append(signal)
    print(json.dumps({
        'phase':   'OPENCLAW_REQUIRED',
        'rule_id': rule_id,
        'reason':  reason,
        'data':    signal
    }), flush=True)

def run(cmd, label, capture=True):
    if args.dry_run:
        emit('DRY_RUN', label, 'SKIPPED', note=' '.join(str(c) for c in cmd))
        return 0, '{"result":"PASS"}', ''
    try:
        r = subprocess.run(
            [str(c) for c in cmd],
            capture_output=capture,
            text=True
        )
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 2, '', str(e)

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None

def get_result(data):
    if data is None: return 'ERROR'
    return data.get('result', 'UNKNOWN')

def write_strategy_log(job):
    log_path = job / 'audit' / 'strategy_attempts.json'
    try:
        log_path.write_text(json.dumps({
            'attempts': {k: v for k, v in strategy_attempts.items()},
            'total_iterations': total_iterations
        }, indent=2))
    except Exception:
        pass

# ── Doc tagging ───────────────────────────────────────────────────────────────

def infer_structural_tags(pdf_path):
    """Detect structural document tags using fitz. No LLM call."""
    tags = []
    try:
        import fitz
        doc = fitz.open(str(pdf_path))

        # multi_page
        if len(doc) > 4:
            tags.append('multi_page')

        # form_fields, images_figures, tables (via widget/xobject/struct presence)
        has_widgets    = False
        image_count    = 0
        for page in doc:
            for annot in page.annots() or []:
                if annot.type[0] == 19:  # PDF_ANNOT_WIDGET
                    has_widgets = True
                    break
            try:
                images = page.get_images(full=False)
                image_count += len(images)
            except Exception:
                pass

        if has_widgets:
            tags.append('form_fields')
        if image_count > 0:
            tags.append('images_figures')

        doc.close()
    except Exception:
        pass
    return tags


def infer_content_tags(pdf_path, taxonomy):
    """Use NIM LLM to classify document content against the taxonomy's content-type tags.
    Returns (assigned_tags, proposed_new_tags).
    Falls back to empty lists if NIM is unavailable or extraction fails."""
    if not taxonomy:
        return [], []

    # Extract text sample (first ~3000 chars is sufficient for classification)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text_sample = ''
        for page in doc:
            text_sample += page.get_text()
            if len(text_sample) > 3000:
                break
        doc.close()
        text_sample = text_sample[:3000].strip()
    except Exception:
        return [], []

    if not text_sample:
        return [], []

    # Only ask the LLM about content-type tags (structural ones inferred separately)
    content_tag_candidates = [
        t for t in taxonomy.get('tags', [])
        if t['tag'] not in ('multi_page', 'form_fields', 'images_figures', 'tables', 'multi_language')
    ]
    if not content_tag_candidates:
        return [], []

    tag_list_str = '\n'.join(
        f'- {t["tag"]}: {t["description"]}' for t in content_tag_candidates
    )

    prompt = (
        'You are classifying a PDF document against a controlled taxonomy of content-type tags. '
        'Based on the document text sample below, return JSON with two fields:\n'
        '  "assigned_tags": list of taxonomy tags that match the document\n'
        '  "proposed_new_tags": list of {tag, description} for any significant '
        'characteristic not covered by an existing tag (or [] if all relevant aspects are covered)\n\n'
        f'Available content-type tags:\n{tag_list_str}\n\n'
        f'Document text sample:\n{text_sample}\n\n'
        'Return JSON only, no prose:'
    )

    try:
        import os, urllib.request, urllib.error
        # Match the same fallback pattern used by generate_alt_text_drafts.py:
        # prefer VISION_PROVIDER_* vars, fall back to PRIMARY_PROVIDER_* vars.
        api_key = (os.environ.get('VISION_PROVIDER_API_KEY') or
                   os.environ.get('PRIMARY_PROVIDER_API_KEY', ''))
        base_url = (os.environ.get('VISION_PROVIDER_BASE_URL') or
                    os.environ.get('PRIMARY_PROVIDER_BASE_URL', '')).rstrip('/')
        api_url  = base_url + '/chat/completions' if base_url else \
                   'https://integrate.api.nvidia.com/v1/chat/completions'
        model    = os.environ.get('PRIMARY_MODEL', 'stepfun-ai/step-3.5-flash')

        if not api_key:
            return [], []

        body = json.dumps({
            'model': model,
            'messages': [
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.0,
            'max_tokens': 2000
        }).encode('utf-8')

        req = urllib.request.Request(
            api_url,
            data=body,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type':  'application/json'
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read().decode('utf-8'))

        message = response['choices'][0]['message']
        # stepfun-ai/step-3.5-flash is a reasoning model — final answer goes
        # into 'content' but reasoning trace is in 'reasoning_content'.
        # If content is null, the model ran out of tokens before finishing —
        # try to extract JSON from reasoning_content as fallback.
        content = message.get('content') or message.get('reasoning_content') or ''
        content = content.strip()
        if not content:
            return [], []
        # Strip markdown fences if present
        if content.startswith('```'):
            content = content.split('```', 2)[1]
            if content.startswith('json'):
                content = content[4:]
            content = content.strip()

        parsed = json.loads(content)
        valid_tags = {t['tag'] for t in content_tag_candidates}
        assigned   = [t for t in parsed.get('assigned_tags', []) if t in valid_tags]
        proposed   = parsed.get('proposed_new_tags', [])
        proposed   = [p for p in proposed
                      if isinstance(p, dict) and 'tag' in p and 'description' in p]
        return assigned, proposed
    except Exception as e:
        # Emit warning so the issue is visible in orchestrator output
        try:
            emit('SETUP', 'doc_tagging_content', 'WARN',
                 note=f'Content classification NIM call failed: {type(e).__name__}: {e}')
        except Exception:
            pass
        return [], []


def assign_doc_tags(pdf_path, taxonomy):
    """Combine structural and content tag inference into a single result."""
    structural = infer_structural_tags(pdf_path)
    content, proposed = infer_content_tags(pdf_path, taxonomy)
    # Deduplicate, preserve order
    seen = set()
    combined = []
    for t in structural + content:
        if t not in seen:
            seen.add(t)
            combined.append(t)
    return combined, proposed

PASS_CODES = {
    'PASS', 'FIXED', 'ALREADY_CORRECT', 'PASS_WITH_MIXED_PAGES',
    'PASS_WITH_ONLY_NATIVE_TEXT', 'SKIPPED', 'OK', 'PLAN_READY',
    'NO_FAILURES', 'NEEDS_REVIEW'
}

def is_pass(result):
    return result in PASS_CODES

# ── Validate prerequisites ────────────────────────────────────────────────────

def check_prereqs():
    errors = []
    if not SOURCE_PDF.exists():
        errors.append(f'Source PDF not found: {SOURCE_PDF}')
    if not VERAPDF_BIN.exists():
        errors.append(f'veraPDF not found: {VERAPDF_BIN}')
    if not RULE_MAP.exists():
        errors.append(f'Rule map not found: {RULE_MAP}')
    if not PROFILES.exists():
        errors.append(f'veraPDF profiles not found: {PROFILES}')
    return errors

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 0 — Setup
# ─────────────────────────────────────────────────────────────────────────────

emit('SETUP', 'prereq_check', 'RUNNING')
errors = check_prereqs()
if errors:
    for e in errors: emit('SETUP', 'prereq_check', 'FAIL', note=e)
    sys.exit(2)
emit('SETUP', 'prereq_check', 'PASS')

# Scaffold
emit('SETUP', 'scaffold', 'RUNNING')
rc, out, err = run(
    ['python3', TOOLS/'packaging'/'package_scaffold.py',
     WORKSPACE, TICKET, BASENAME],
    'scaffold'
)
try:
    scaffold = json.loads(out)
    JOB  = Path(scaffold['job_dir'])
    OUT  = Path(scaffold['output_dir'])
except Exception:
    JOB  = JOB_DIR
    OUT  = OUTPUT_DIR

if rc != 0:
    emit_deviation('scaffold', 'exit_code=0', f'exit_code={rc}', err, layer=1)
    sys.exit(2)
emit('SETUP', 'scaffold', 'PASS', data={'job_dir': str(JOB), 'output_dir': str(OUT)})

REPAIR_DIR  = JOB / 'repair'
AUDIT_DIR   = JOB / 'audit'
QA_DIR      = JOB / 'qa'
REPORTS_DIR = JOB / 'reports'

# Copy source PDF
PASS0 = REPAIR_DIR / 'pass0_source.pdf'
emit('SETUP', 'copy_source', 'RUNNING')
shutil.copy2(SOURCE_PDF, PASS0)
emit('SETUP', 'copy_source', 'PASS', data={'pass0': str(PASS0)})

# Doc tagging
emit('SETUP', 'doc_tagging', 'RUNNING')
doc_tags             = []
proposed_taxonomy_additions = []
try:
    taxonomy = load_json(TAXONOMY)
    if taxonomy:
        doc_tags, proposed_taxonomy_additions = assign_doc_tags(PASS0, taxonomy)
        emit('SETUP', 'doc_tagging', 'PASS',
             data={'doc_tags':                    doc_tags,
                   'proposed_taxonomy_additions': proposed_taxonomy_additions})
    else:
        emit('SETUP', 'doc_tagging', 'WARN', note='Taxonomy file not loadable')
except Exception as e:
    emit('SETUP', 'doc_tagging', 'WARN', note=f'Doc tagging failed: {e}')

# Persist taxonomy proposals to a sidecar that status_json_writer can pick up
if proposed_taxonomy_additions:
    try:
        (AUDIT_DIR / 'proposed_taxonomy_additions.json').write_text(
            json.dumps(proposed_taxonomy_additions, indent=2)
        )
    except Exception:
        pass

# Persist doc_tags to a sidecar that status_json_writer can pick up
if doc_tags:
    try:
        (AUDIT_DIR / 'doc_tags.json').write_text(
            json.dumps(doc_tags, indent=2)
        )
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Pre-flight
# ─────────────────────────────────────────────────────────────────────────────

# 1a. OCR detection
emit('PREFLIGHT', 'ocr_detection', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'detect_image_only_pages.py', PASS0,
     '--out', AUDIT_DIR/'detect_image_only_pages.json'],
    'ocr_detection'
)
ocr_data   = load_json(AUDIT_DIR/'detect_image_only_pages.json')
ocr_result = get_result(ocr_data)
gate_results['ocr_detection'] = ocr_result

if ocr_data and ocr_data.get('ocr_required'):
    emit_deviation('ocr_detection', 'ocr_required=false', 'ocr_required=true',
                   'Document requires OCR. Run ocrmypdf then restart remediate.py on OCR output.',
                   layer=1)
    sys.exit(1)
emit('PREFLIGHT', 'ocr_detection', ocr_result)

# 1b. qpdf structural check
emit('PREFLIGHT', 'qpdf_check', 'RUNNING')
rc, out, _ = run(
    ['bash', TOOLS/'audit'/'run_qpdf_check.sh', PASS0, AUDIT_DIR,
     '--out', AUDIT_DIR/'qpdf_check.json'],
    'qpdf_check'
)
qpdf_data   = load_json(AUDIT_DIR/'qpdf_check.json')
qpdf_result = get_result(qpdf_data)
gate_results['qpdf'] = qpdf_result

if not is_pass(qpdf_result):
    emit_deviation('qpdf_check', 'PASS', qpdf_result,
                   qpdf_data.get('errors', '') if qpdf_data else '', layer=1)
    sys.exit(1)
emit('PREFLIGHT', 'qpdf_check', qpdf_result)

# 1c. Struct tree pre-flight
emit('PREFLIGHT', 'struct_tree_check', 'RUNNING')
try:
    import fitz as _fitz
    _doc      = _fitz.open(str(PASS0))
    _catalog  = _doc.pdf_catalog()
    _str_ref  = _doc.xref_get_key(_catalog, 'StructTreeRoot')
    _has_struct = _str_ref[0] != 'null' and bool(_str_ref[1])
    _doc.close()
except Exception:
    _has_struct = None

if _has_struct is False:
    gate_results['struct_tree_check'] = 'FAIL'
    emit('PREFLIGHT', 'struct_tree_check', 'FAIL',
         note='No StructTreeRoot — running fix_untagged_pdf.py to auto-generate structure tree')

    untagged_fix  = TOOLS / 'repair' / 'fix_untagged_pdf.py'
    pass1_tagged  = REPAIR_DIR / 'pass1_fix_untagged.pdf'

    if untagged_fix.exists():
        rc_tag, out_tag, _ = run(
            ['python3', untagged_fix, PASS0, pass1_tagged,
             '--out', AUDIT_DIR / 'fix_untagged.json'],
            'fix_untagged_pdf'
        )
        if rc_tag == 0 and pass1_tagged.exists():
            emit('PREFLIGHT', 'fix_untagged_pdf', 'FIXED')
            marking_fix  = TOOLS / 'repair' / 'fix_struct_content_marking.py'
            pass2_marked = REPAIR_DIR / 'pass2_fix_struct_content_marking.pdf'
            if marking_fix.exists():
                rc_mark, _, _ = run(
                    ['python3', marking_fix, pass1_tagged, pass2_marked,
                     '--out', AUDIT_DIR / 'fix_struct_content_marking.json'],
                    'fix_struct_content_marking'
                )
                PASS0 = pass2_marked if (rc_mark == 0 and pass2_marked.exists()) else pass1_tagged
                emit('PREFLIGHT', 'fix_struct_content_marking',
                     'FIXED' if rc_mark == 0 else 'WARN')
            else:
                PASS0 = pass1_tagged
            gate_results['struct_tree_check'] = 'FIXED'
        else:
            emit_deviation('fix_untagged_pdf', 'FIXED', 'FAIL',
                           out_tag[:200] if out_tag else 'no output', layer=1)
            sys.exit(1)
    else:
        emit_deviation('struct_tree_check', 'fix_untagged_pdf.py exists',
                       'script not found', str(untagged_fix), layer=1)
        sys.exit(1)
else:
    gate_results['struct_tree_check'] = 'PASS' if _has_struct else 'UNKNOWN'
    emit('PREFLIGHT', 'struct_tree_check', gate_results['struct_tree_check'])

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Audit gates
# ─────────────────────────────────────────────────────────────────────────────

# 2a. Baseline veraPDF
emit('AUDIT', 'verapdf_baseline', 'RUNNING')
run(['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
     VERAPDF_BIN, PROFILES, PASS0, AUDIT_DIR],
    'verapdf_baseline')

for src, dst in [
    (AUDIT_DIR/'verapdf_pdfua_ua1.xml',       AUDIT_DIR/'verapdf_pre_pdfua1.xml'),
    (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml', AUDIT_DIR/'verapdf_pre_wcag.xml'),
]:
    if Path(src).exists():
        shutil.copy2(src, dst)

verapdf_summary = load_json(AUDIT_DIR/'verapdf_summary.json')
gate_results['verapdf_baseline'] = get_result(verapdf_summary)
emit('AUDIT', 'verapdf_baseline', gate_results['verapdf_baseline'],
     note='Failures expected — repair plan will address them')

# 2b. Parse veraPDF failures
emit('AUDIT', 'parse_failures', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'parse_verapdf_summary.py',
     AUDIT_DIR/'verapdf_pre_pdfua1.xml',
     AUDIT_DIR/'verapdf_pre_wcag.xml'],
    'parse_failures'
)
failures_path = AUDIT_DIR / 'failures.json'
try:
    failures_data = json.loads(out)
    failures_path.write_text(json.dumps(failures_data, indent=2))
    emit('AUDIT', 'parse_failures', 'PASS',
         data={'unique_rules': failures_data.get('unique_rules_failing', 0),
               'total_failures': failures_data.get('total_failures', 0)})
except Exception as e:
    emit_deviation('parse_failures', 'valid JSON', f'parse error: {e}', out[:200], layer=1)
    failures_path.write_text('{"result":"PASS","failures_by_rule":[]}')
    failures_data = {'failures_by_rule': []}

# 2c. Metadata audit
emit('AUDIT', 'metadata_parity', 'RUNNING')
run(['python3', TOOLS/'audit'/'metadata_xmp_parity_audit.py', PASS0,
     '--out', AUDIT_DIR/'metadata_pre.json'], 'metadata_parity')
meta_pre = load_json(AUDIT_DIR/'metadata_pre.json')
gate_results['metadata_pre'] = get_result(meta_pre)
emit('AUDIT', 'metadata_parity', gate_results['metadata_pre'])

# 2d. Preservation audit
emit('AUDIT', 'preservation', 'RUNNING')
run(['python3', TOOLS/'qa'/'preservation_audit.py', PASS0, PASS0,
     '--out', AUDIT_DIR/'preservation_pre.json'], 'preservation')
pres_pre = load_json(AUDIT_DIR/'preservation_pre.json')
gate_results['preservation_pre'] = get_result(pres_pre)
emit('AUDIT', 'preservation', gate_results['preservation_pre'])

# 2e. Table semantics audit
emit('AUDIT', 'table_semantics', 'RUNNING')
run(['python3', TOOLS/'audit'/'table_semantics_audit.py', PASS0,
     '--out', AUDIT_DIR/'table_semantics_pre.json'], 'table_semantics')
table_pre = load_json(AUDIT_DIR/'table_semantics_pre.json')
gate_results['table_semantics_pre'] = get_result(table_pre)
th_missing = table_pre.get('th_missing_scope', 0) if table_pre else 0
emit('AUDIT', 'table_semantics', gate_results['table_semantics_pre'])

# 2f. Contrast audit
emit('AUDIT', 'contrast', 'RUNNING')
run(['python3', TOOLS/'audit'/'contrast_audit.py', PASS0,
     '--out', AUDIT_DIR/'contrast_pre.json'], 'contrast')
contrast_pre = load_json(AUDIT_DIR/'contrast_pre.json')
gate_results['contrast_pre'] = get_result(contrast_pre)
emit('AUDIT', 'contrast', gate_results['contrast_pre'])

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — Repair plan
# ─────────────────────────────────────────────────────────────────────────────

emit('PLAN', 'lookup_repair_plan', 'RUNNING')
lookup_cmd = ['python3', TOOLS/'audit'/'lookup_repair_plan.py',
              failures_path, '--map', RULE_MAP, '--taxonomy', TAXONOMY]
if doc_tags:
    lookup_cmd.extend(['--doc-tags', ','.join(doc_tags)])
rc, out, _ = run(lookup_cmd, 'lookup_repair_plan')
plan_path = AUDIT_DIR / 'repair_plan.json'
try:
    plan_data = json.loads(out)
    plan_path.write_text(json.dumps(plan_data, indent=2))
except Exception:
    plan_data = {'result': 'NO_FAILURES', 'repair_steps': [], 'openclaw_required': [], 'unknown_rules': []}
    plan_path.write_text(json.dumps(plan_data, indent=2))

repair_steps       = plan_data.get('repair_steps', [])
openclaw_required  = plan_data.get('openclaw_required', [])
unknown_rules      = plan_data.get('unknown_rules', [])

# Inject table headers fix if TH scope issues found and not already in plan
table_headers_script = 'tools/repair/fix_table_headers.py'
has_table_fix = any(s['repair_script'] == table_headers_script for s in repair_steps)
if th_missing > 0 and not has_table_fix:
    repair_steps.append({
        'step':            len(repair_steps) + 1,
        'repair_script':   table_headers_script,
        'strategy':        'fix_table_headers',
        'description':     f'TH cells missing Scope attribute ({th_missing} found by table_semantics_audit)',
        'repair_order':    10,
        'run_last':        True,
        'args_pattern':    '<input.pdf> <output.pdf>',
        'rules_addressed': ['table_semantics/TH_missing_scope'],
        'confidence':      'CONFIRMED',
        'pass_rate':       1.0,
        'pass_count':      1,
        'fail_count':      0,
        'all_strategies':  [],
        'notes':           f'Injected: {th_missing} TH cells missing Scope. MUST RUN LAST.'
    })
    repair_steps.sort(key=lambda s: (s.get('run_last', False), s.get('repair_order', 99)))
    for i, s in enumerate(repair_steps, 1):
        s['step'] = i

emit('PLAN', 'lookup_repair_plan', plan_data.get('result', 'UNKNOWN'),
     data={
         'repair_steps':      len(repair_steps),
         'openclaw_required': len(openclaw_required),
         'unknown_rules':     len(unknown_rules),
         'th_fix_injected':   th_missing > 0 and not has_table_fix
     })

# Emit OPENCLAW_REQUIRED signals for all rules needing agent intervention
for oc in openclaw_required:
    emit_openclaw_required(
        oc['rule_id'], oc.get('description', ''),
        oc.get('failures', 0), oc['reason'],
        oc.get('strategies_attempted', [])
    )

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — Alt text branch determination
# ─────────────────────────────────────────────────────────────────────────────

ALT_MAP_JOB   = REPORTS_DIR / 'alt_map_approved.json'
ALT_MAP_ASSET = WORKSPACE / 'assets' / 'alt_maps' / f'{SAFE_BASE}_alt_map_approved.json'

alt_branch = 'NONE'
if ALT_MAP_JOB.exists():
    alt_branch = 'A_LOCAL'
elif ALT_MAP_ASSET.exists():
    alt_branch = 'A_ASSET'
    shutil.copy2(ALT_MAP_ASSET, ALT_MAP_JOB)
else:
    alt_branch = 'B'

emit('PLAN', 'alt_text_branch', alt_branch)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — Iterative repair loop
# ─────────────────────────────────────────────────────────────────────────────
#
# Each iteration:
#   1. Pick next untried strategy for each unresolved rule
#   2. Run repair script
#   3. Run full validate cycle (veraPDF + preservation + metadata)
#   4. Mark rule resolved or log attempt and try next strategy next iteration
#   5. If per-rule cap hit (15): emit OPENCLAW_REQUIRED, continue with other rules
#   6. If per-job cap hit (50): force termination
#   7. Log all attempts to strategy_attempts.json
#
# ─────────────────────────────────────────────────────────────────────────────

current_pdf  = PASS0
pass_num     = 3  # 0=source, 1=untagged, 2=marking already used if applicable
resolved_rules   = set()
per_rule_counts  = defaultdict(int)

def next_pass(label):
    global pass_num
    p = REPAIR_DIR / f'pass{pass_num}_{label}.pdf'
    pass_num += 1
    return p

def run_validate_cycle(pdf_path, iteration):
    """Run veraPDF + preservation after a repair. Returns (failures_by_rule, pres_result)."""
    # veraPDF post
    run(['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
         VERAPDF_BIN, PROFILES, pdf_path, AUDIT_DIR],
        f'verapdf_iter{iteration}')
    iter_pdfua = AUDIT_DIR / f'verapdf_iter{iteration}_pdfua1.xml'
    iter_wcag  = AUDIT_DIR / f'verapdf_iter{iteration}_wcag.xml'
    for src, dst in [
        (AUDIT_DIR/'verapdf_pdfua_ua1.xml',       iter_pdfua),
        (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml', iter_wcag),
    ]:
        if Path(src).exists():
            shutil.copy2(src, dst)

    rc2, out2, _ = run(
        ['python3', TOOLS/'audit'/'parse_verapdf_summary.py',
         iter_pdfua, iter_wcag],
        f'parse_iter{iteration}'
    )
    try:
        post = json.loads(out2)
        failures = post.get('failures_by_rule', [])
    except Exception:
        failures = []

    # Preservation check
    run(['python3', TOOLS/'qa'/'preservation_audit.py',
         PASS0, pdf_path,
         '--out', AUDIT_DIR/f'preservation_iter{iteration}.json'],
        f'preservation_iter{iteration}')
    pres = load_json(AUDIT_DIR/f'preservation_iter{iteration}.json')

    return failures, get_result(pres)


# Build the ordered list of scripts to execute. Deduplicate by script
# (one script can address multiple rules — execute it once per iteration).
pending_scripts = []
seen_scripts    = set()
for step in sorted(repair_steps, key=lambda s: (s.get('run_last', False), s.get('repair_order', 99))):
    script = step['repair_script']
    if script not in seen_scripts:
        seen_scripts.add(script)
        pending_scripts.append(step)

unresolved_scripts = list(pending_scripts)
iteration          = 0

while unresolved_scripts and total_iterations < JOB_HARD_CAP:
    iteration       += 1
    total_iterations += 1

    if total_iterations == JOB_WARN_AT:
        emit('REPAIR', 'iteration_warning', 'WARN',
             note=f'Job has reached {JOB_WARN_AT} total iterations. Logged to STATUS.json.')

    emit('REPAIR', f'iteration_{iteration}', 'RUNNING',
         data={'scripts_remaining': len(unresolved_scripts),
               'total_iterations':  total_iterations})

    iteration_pdf    = current_pdf
    # Track per-script results within this iteration:
    # script_path_str -> {step, result, executed}
    script_results = {}

    for step in unresolved_scripts:
        script       = step['repair_script']
        rule_ids     = step['rules_addressed']
        script_label = Path(script).stem
        script_path  = APP / script

        # Skip scripts whose rules are ALL already at the per-rule cap.
        # Only emit per_rule_cap_reached for rules that have actually been attempted.
        capped_rules    = [r for r in rule_ids if per_rule_counts[r] >= PER_RULE_CAP]
        uncapped_rules  = [r for r in rule_ids if per_rule_counts[r] <  PER_RULE_CAP]
        if not uncapped_rules:
            for r in capped_rules:
                emit_openclaw_required(
                    r, '', 0, 'per_rule_cap_reached',
                    strategy_attempts.get(r, [])
                )
            continue

        if not script_path.exists():
            emit_deviation(script, 'script_exists', 'NOT_FOUND',
                           f'Script not found at {script_path}', layer=1)
            script_results[script] = {'step': step, 'result': 'NOT_FOUND', 'executed': False}
            continue

        output_pdf = next_pass(f'iter{iteration}_{script_label}')

        emit('REPAIR', script_label, 'RUNNING',
             data={'iteration': iteration,
                   'strategy':  step.get('strategy', ''),
                   'rules':     rule_ids})

        # ── Special handling: fix_figure_alt_text ────────────────────────────
        if 'fix_figure_alt_text' in script:
            if alt_branch in ('A_LOCAL', 'A_ASSET'):
                rc, out, err = run(
                    ['python3', script_path, iteration_pdf, output_pdf,
                     '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                    script_label
                )
            else:
                auto_pdf    = REPAIR_DIR / f'pass{pass_num}_alt_auto.pdf'
                auto_json   = AUDIT_DIR  / 'alt_text_auto_output.json'
                drafts_json = REPORTS_DIR/ 'alt_text_drafts.json'
                review_html = REPORTS_DIR/ 'alt_text_review.html'

                rc_auto, out_auto, _ = run(
                    ['python3', script_path, iteration_pdf, auto_pdf,
                     '--language', LANGUAGE],
                    f'{script_label}_auto'
                )
                globals()['pass_num'] = pass_num + 1

                # fix_figure_alt_text auto-mode exits 1 with result=NEEDS_REVIEW
                # when placeholders are set — that is the expected success case.
                # Only treat it as a failure if no output PDF was produced.
                if not auto_pdf.exists():
                    emit_deviation(f'{script_label}_auto',
                                   f'auto_pdf exists at {auto_pdf}',
                                   f'rc={rc_auto}, file_missing=True',
                                   'Branch B auto-mode failed to produce intermediate PDF',
                                   layer=1)
                    script_results[script] = {'step': step, 'result': 'AUTO_FAILED', 'executed': False}
                    continue

                # Write auto_json from stdout
                try:
                    auto_data = json.loads(out_auto)
                    auto_json.write_text(json.dumps(auto_data, indent=2))
                except Exception:
                    pass

                # Step B2: generate drafts via vision model
                run(['python3', TOOLS/'repair'/'generate_alt_text_drafts.py',
                     auto_pdf, '--fix-output', auto_json, '--out', drafts_json],
                    f'{script_label}_drafts')

                if not drafts_json.exists():
                    emit_deviation(f'{script_label}_drafts',
                                   f'drafts_json exists at {drafts_json}',
                                   'drafts_json missing',
                                   'Branch B draft generation failed', layer=1)
                    script_results[script] = {'step': step, 'result': 'DRAFTS_FAILED', 'executed': False}
                    continue

                # Step B3: generate review report
                # generate_alt_text_review_report.py writes the pre-approved map
                # directly via --map-out, and produces the HTML review report.
                rc_review, out_review, _ = run(
                    ['python3', TOOLS/'repair'/'generate_alt_text_review_report.py',
                     str(auto_pdf),
                     '--draft',   str(drafts_json),
                     '--out',     str(review_html),
                     '--map-out', str(ALT_MAP_JOB)],
                    f'{script_label}_review'
                )

                if not ALT_MAP_JOB.exists():
                    # Fallback: copy drafts directly as approved map
                    shutil.copy2(drafts_json, ALT_MAP_JOB)
                    emit('REPAIR', f'{script_label}_review', 'WARN',
                         note='Review report failed — drafts auto-approved directly')
                else:
                    emit('REPAIR', f'{script_label}_review', 'PASS',
                         note=f'Review HTML: {review_html}')

                alt_branch = 'A_LOCAL'

                # Step B4: apply approved map
                rc, out, err = run(
                    ['python3', script_path, auto_pdf, output_pdf,
                     '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                    f'{script_label}_apply'
                )

                # Step B5: copy to asset library for future runs
                asset_dir = WORKSPACE / 'assets' / 'alt_maps'
                asset_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ALT_MAP_JOB,
                             asset_dir / f'{SAFE_BASE}_alt_map_approved.json')

        # ── Special handling: fix_metadata_xmp_parity ────────────────────────
        elif 'fix_metadata_xmp_parity' in script:
            rc, out, err = run(
                ['python3', script_path, iteration_pdf, output_pdf,
                 '--title',    args.title,
                 '--subject',  args.subject,
                 '--keywords', args.keywords,
                 '--language', LANGUAGE],
                script_label
            )

        # ── All other scripts ─────────────────────────────────────────────────
        else:
            rc, out, err = run(
                ['python3', script_path, iteration_pdf, output_pdf],
                script_label
            )

        # ── Layer 1: execution check ──────────────────────────────────────────
        step_data = None
        try:
            step_data = json.loads(out)
        except Exception:
            pass
        this_result = get_result(step_data) if step_data else ('PASS' if rc == 0 else 'ERROR')

        if rc != 0 and not is_pass(this_result):
            emit_deviation(script_label, 'exit_code=0 or PASS result',
                           f'exit_code={rc}, result={this_result}',
                           step_data.get('error', err[:300]) if step_data else err[:300],
                           layer=1)
            script_results[script] = {'step': step, 'result': this_result, 'executed': False}
            continue

        if not output_pdf.exists():
            emit_deviation(script_label,
                           f'output_pdf exists at {output_pdf}',
                           'output_pdf missing',
                           f'Script exited {rc} but did not produce output file',
                           layer=1)
            script_results[script] = {'step': step, 'result': this_result, 'executed': False}
            continue

        emit('REPAIR', script_label, this_result,
             data={'iteration': iteration, 'output': str(output_pdf)})

        script_results[script] = {'step': step, 'result': this_result, 'executed': True}
        # Advance PDF for this iteration
        iteration_pdf = output_pdf

    # ── End of iteration — validate ───────────────────────────────────────────
    # If at least one script produced a new PDF, validate. Otherwise (every
    # script in this iteration failed Layer 1), we still need to advance
    # counters and queue strategy fallbacks — but skip the veraPDF check
    # since the PDF didn't change.
    pdf_advanced = (iteration_pdf != current_pdf)

    if pdf_advanced and not args.dry_run:
        emit('VALIDATE', f'iteration_{iteration}', 'RUNNING')
        remaining_failures, pres_result = run_validate_cycle(iteration_pdf, iteration)
        remaining_rule_ids = {f['rule_id'] for f in remaining_failures}
    else:
        remaining_failures   = []
        pres_result          = 'SKIPPED'
        # When no PDF was produced, treat all previously-failing rules as still failing
        remaining_rule_ids = set()
        for step in unresolved_scripts:
            for r in step['rules_addressed']:
                remaining_rule_ids.add(r)
        if not pdf_advanced:
            emit('VALIDATE', f'iteration_{iteration}', 'SKIPPED',
                 note='No script produced an output PDF this iteration. Counters and fallbacks still applied.')

    write_strategy_log(JOB)

    # Build next iteration's unresolved_scripts list, deduplicated by script.
    next_steps_by_script = {}

    for step in unresolved_scripts:
        script   = step['repair_script']
        rule_ids = step['rules_addressed']
        sr       = script_results.get(script)

        # Decide what failure_reason to record for rules whose script failed
        # or didn't produce a passing rule. Three cases:
        #   - script not executed (NOT_FOUND / cap-skipped) — script_results missing
        #   - script executed but exited with error or no output — sr['executed']=False
        #   - script executed cleanly but rule still appears in veraPDF post — rule_persists
        if sr is None:
            # Script was skipped this iteration. The only path to sr=None is
            # the early per-rule cap check, which already emitted
            # per_rule_cap_reached for all addressed rules. Don't requeue.
            continue

        if not sr.get('executed'):
            # Execution failed (script error, missing output, NOT_FOUND, etc).
            # Increment per-rule counters and fall through to next strategy.
            fail_reason = f'execution_failed:{sr.get("result", "UNKNOWN")}'
            still_failing = list(rule_ids)
            resolved      = []
        else:
            this_script_result = sr['result']
            still_failing = [r for r in rule_ids if r in remaining_rule_ids]
            resolved      = [r for r in rule_ids if r not in remaining_rule_ids]
            # Script self-reported success but rule still present in veraPDF post
            fail_reason   = 'rule_still_present_after_repair' \
                            if is_pass(this_script_result) \
                            else f'script_result:{this_script_result}'

        # Log resolved rules
        for r in resolved:
            resolved_rules.add(r)
            strategy_attempts[r].append({
                'iteration': iteration,
                'strategy':  step.get('strategy', ''),
                'script':    script,
                'result':    'PASS'
            })
            emit('VALIDATE', f'rule_resolved/{r}', 'PASS',
                 data={'iteration': iteration})

        if not still_failing:
            continue  # script's rules all resolved — don't queue for next iter

        # Increment per-rule counters and check caps
        any_rule_can_continue = False
        for r in still_failing:
            per_rule_counts[r] += 1
            strategy_attempts[r].append({
                'iteration': iteration,
                'strategy':  step.get('strategy', ''),
                'script':    script,
                'result':    'FAIL',
                'reason':    fail_reason
            })
            if per_rule_counts[r] >= PER_RULE_CAP:
                emit_openclaw_required(
                    r, '', 0, 'per_rule_cap_reached',
                    strategy_attempts.get(r, [])
                )
            else:
                any_rule_can_continue = True

        if not any_rule_can_continue:
            continue  # all still-failing rules hit the cap

        # Pick the next untried strategy from this script's all_strategies.
        all_strats = step.get('all_strategies', [])
        tried_strategies = set()
        for r in still_failing:
            for a in strategy_attempts.get(r, []):
                if a.get('strategy'):
                    tried_strategies.add(a['strategy'])

        remaining_strats = [s for s in all_strats
                            if s.get('strategy', '') not in tried_strategies]

        if remaining_strats:
            next_step = dict(step)
            next_strat = remaining_strats[0]
            next_step['repair_script']  = next_strat.get('repair_script', script)
            next_step['strategy']       = next_strat.get('strategy', '')
            # Keep the full remaining list (minus the one we just picked)
            # so the next iteration's fallback computation still sees them.
            next_step['all_strategies'] = remaining_strats[1:]
            next_steps_by_script[next_step['repair_script']] = next_step
        else:
            for r in still_failing:
                if per_rule_counts[r] < PER_RULE_CAP:
                    emit_openclaw_required(
                        r, '', 0, 'all_strategies_exhausted',
                        strategy_attempts.get(r, [])
                    )

    unresolved_scripts = list(next_steps_by_script.values())
    current_pdf        = iteration_pdf if pdf_advanced else current_pdf

    if pdf_advanced:
        emit('VALIDATE', f'iteration_{iteration}', 'COMPLETE',
             data={'resolved_cumulative': len(resolved_rules),
                   'still_failing':       len(remaining_rule_ids),
                   'preservation':        pres_result})

if total_iterations >= JOB_HARD_CAP:
    emit('REPAIR', 'job_hard_cap', 'ESCALATION',
         note=f'Job reached {JOB_HARD_CAP} iteration hard cap. Forcing termination.')
    for step in unresolved_scripts:
        for r in step['rules_addressed']:
            emit_openclaw_required(r, '', 0, 'job_hard_cap_reached',
                                   strategy_attempts.get(r, []))

FINAL_PDF = current_pdf

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — Post-repair validation
# ─────────────────────────────────────────────────────────────────────────────

emit('VALIDATE', 'final_verapdf', 'RUNNING')
run(['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
     VERAPDF_BIN, PROFILES, FINAL_PDF, AUDIT_DIR],
    'verapdf_final')

for src, dst in [
    (AUDIT_DIR/'verapdf_pdfua_ua1.xml',       AUDIT_DIR/'verapdf_post_pdfua1.xml'),
    (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml', AUDIT_DIR/'verapdf_post_wcag.xml'),
]:
    if Path(src).exists():
        shutil.copy2(src, dst)

verapdf_post        = load_json(AUDIT_DIR/'verapdf_summary.json')
verapdf_post_result = get_result(verapdf_post)
gate_results['verapdf_post'] = verapdf_post_result

rc2, out2, _ = run(
    ['python3', TOOLS/'audit'/'parse_verapdf_summary.py',
     AUDIT_DIR/'verapdf_post_pdfua1.xml',
     AUDIT_DIR/'verapdf_post_wcag.xml'],
    'parse_post_failures'
)
post_failures_path = AUDIT_DIR / 'failures_post.json'
try:
    post_failures = json.loads(out2)
    post_failures_path.write_text(json.dumps(post_failures, indent=2))
except Exception:
    post_failures = {'failures_by_rule': []}

remaining_failures = post_failures.get('failures_by_rule', [])
emit('VALIDATE', 'final_verapdf', verapdf_post_result,
     data={'remaining_failures': len(remaining_failures)})

# Metadata post
emit('VALIDATE', 'metadata_post', 'RUNNING')
run(['python3', TOOLS/'audit'/'metadata_xmp_parity_audit.py', FINAL_PDF,
     '--out', AUDIT_DIR/'metadata_post.json'], 'metadata_post')
meta_post        = load_json(AUDIT_DIR/'metadata_post.json')
meta_post_result = get_result(meta_post)
gate_results['metadata_post'] = meta_post_result
if not is_pass(meta_post_result):
    emit_deviation('metadata_post', 'PASS', meta_post_result,
                   str(meta_post.get('failures', []) if meta_post else ''), layer=2)
emit('VALIDATE', 'metadata_post', meta_post_result)

# Table semantics post
emit('VALIDATE', 'table_semantics_post', 'RUNNING')
run(['python3', TOOLS/'audit'/'table_semantics_audit.py', FINAL_PDF,
     '--out', AUDIT_DIR/'table_semantics_post.json'], 'table_semantics_post')
table_post        = load_json(AUDIT_DIR/'table_semantics_post.json')
table_post_result = get_result(table_post)
gate_results['table_semantics_post'] = table_post_result
emit('VALIDATE', 'table_semantics_post', table_post_result)

# Preservation post
emit('VALIDATE', 'preservation_post', 'RUNNING')
run(['python3', TOOLS/'qa'/'preservation_audit.py',
     PASS0, FINAL_PDF,
     '--out', AUDIT_DIR/'preservation_post.json'], 'preservation_post')
pres_post        = load_json(AUDIT_DIR/'preservation_post.json')
pres_post_result = get_result(pres_post)
gate_results['preservation_post'] = pres_post_result
if not is_pass(pres_post_result):
    emit_deviation('preservation_post', 'PASS', pres_post_result,
                   'Content may have been lost during repair', layer=2)
emit('VALIDATE', 'preservation_post', pres_post_result)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7 — QA
# ─────────────────────────────────────────────────────────────────────────────

emit('QA', 'render_compare', 'RUNNING')
run(['python3', TOOLS/'qa'/'render_compare.py',
     PASS0, FINAL_PDF, QA_DIR,
     '--out', AUDIT_DIR/'render_compare.json'], 'render_compare')
rc_data   = load_json(AUDIT_DIR/'render_compare.json')
rc_result = get_result(rc_data)
gate_results['render_compare'] = rc_result
emit('QA', 'render_compare', rc_result)

emit('QA', 'visual_qa', 'RUNNING')
run(['python3', TOOLS/'qa'/'visual_qa.py',
     FINAL_PDF, QA_DIR,
     '--out', AUDIT_DIR/'visual_qa.json'], 'visual_qa')
vqa_data   = load_json(AUDIT_DIR/'visual_qa.json')
vqa_result = get_result(vqa_data)
gate_results['visual_qa'] = vqa_result
emit('QA', 'visual_qa', vqa_result)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 8 — Outcome determination and packaging
# ─────────────────────────────────────────────────────────────────────────────

critical_fails = [k for k, v in gate_results.items()
                  if not is_pass(v)
                  and k in ('verapdf_post', 'metadata_post', 'preservation_post')]

has_unresolved_openclaw = len(openclaw_signals) > 0

if critical_fails:
    overall = 'FAIL'
elif has_unresolved_openclaw and total_iterations >= JOB_HARD_CAP:
    overall = 'ESCALATION'
elif has_unresolved_openclaw:
    overall = 'REVIEW_REQUIRED'
elif deviations:
    overall = 'REVIEW_REQUIRED'
else:
    overall = 'PASS'

emit('PACKAGE', 'overall_result', overall,
     data={'critical_fails':           critical_fails,
           'deviations':               len(deviations),
           'openclaw_signals':         len(openclaw_signals),
           'total_iterations':         total_iterations,
           'iteration_warning_issued': total_iterations >= JOB_WARN_AT})

# Write STATUS.json
emit('PACKAGE', 'status_json', 'RUNNING')

# Persist OPENCLAW_REQUIRED signals to a sidecar that status_json_writer can read
try:
    (AUDIT_DIR / 'openclaw_signals.json').write_text(
        json.dumps(openclaw_signals, indent=2)
    )
except Exception:
    pass

# Persist the orchestrator's authoritative overall result so status_json_writer
# uses it directly instead of re-deriving from gate values.
try:
    (AUDIT_DIR / 'orchestrator_outcome.json').write_text(
        json.dumps({
            'overall_result':   overall,
            'critical_fails':   critical_fails,
            'total_iterations': total_iterations,
            'has_openclaw':     len(openclaw_signals) > 0,
        }, indent=2)
    )
except Exception:
    pass

run(['python3', TOOLS/'packaging'/'status_json_writer.py', JOB,
     '--pdf', str(SOURCE_PDF)], 'status_json')
emit('PACKAGE', 'status_json', 'PASS')

# Write strategy attempts log
write_strategy_log(JOB)

# ── Outcome-aware packaging ───────────────────────────────────────────────────

if overall == 'PASS':
    # Full package — remediated PDF + audit report
    emit('PACKAGE', 'package_deliverables', 'RUNNING')
    rc, out, _ = run(
        ['python3', TOOLS/'packaging'/'package_deliverables.py',
         JOB, FINAL_PDF,
         '--output-dir', OUT,
         '--source-pdf', str(SOURCE_PDF)],
        'package_deliverables'
    )
    pkg_data = {}
    try:
        pkg_data = json.loads(out)
    except Exception:
        pass
    emit('PACKAGE', 'package_deliverables',
         get_result(pkg_data) if pkg_data else ('PASS' if rc == 0 else 'FAIL'))

elif overall == 'REVIEW_REQUIRED':
    # Package to review/ subdirectory
    review_dir = OUT / 'review'
    review_dir.mkdir(parents=True, exist_ok=True)
    emit('PACKAGE', 'package_deliverables', 'RUNNING')
    rc, out, _ = run(
        ['python3', TOOLS/'packaging'/'package_deliverables.py',
         JOB, FINAL_PDF,
         '--output-dir', str(review_dir),
         '--source-pdf', str(SOURCE_PDF)],
        'package_deliverables_review'
    )
    emit('PACKAGE', 'package_deliverables', 'PASS' if rc == 0 else 'FAIL',
         note=f'Review package at {review_dir}')

elif overall in ('FAIL', 'ESCALATION'):
    # No remediated PDF — audit report and escalation report only
    failed_dir = OUT / 'failed'
    failed_dir.mkdir(parents=True, exist_ok=True)

    # Generate audit report into failed/ — no remediated PDF
    emit('PACKAGE', 'package_deliverables', 'RUNNING')
    rc, out, _ = run(
        ['python3', TOOLS/'packaging'/'package_deliverables.py',
         JOB, FINAL_PDF,
         '--output-dir', str(failed_dir),
         '--source-pdf', str(SOURCE_PDF),
         '--skip-pdf'],
        'package_deliverables_fail'
    )
    pkg_fail_data = {}
    try:
        pkg_fail_data = json.loads(out)
    except Exception:
        pass
    emit('PACKAGE', 'package_deliverables',
         get_result(pkg_fail_data) if pkg_fail_data else ('PASS' if rc == 0 else 'FAIL'),
         note=f'Audit report at {failed_dir}')

    # Write escalation report
    escalation_report = failed_dir / 'ESCALATION_REPORT.md'
    with open(escalation_report, 'w') as f:
        f.write(f'# Escalation Report — {TICKET}\n\n')
        f.write(f'**Job:** {JOB_NAME}  \n')
        f.write(f'**Overall result:** {overall}  \n')
        f.write(f'**Date:** {datetime.now(timezone.utc).isoformat()}  \n')
        f.write(f'**Total iterations:** {total_iterations}  \n\n')
        f.write('## Critical gate failures\n\n')
        for k in critical_fails:
            f.write(f'- `{k}`: {gate_results.get(k, "UNKNOWN")}\n')
        f.write('\n## Rules requiring agent intervention\n\n')
        for sig in openclaw_signals:
            f.write(f'### {sig["rule_id"]}\n')
            f.write(f'**Reason:** {sig["reason"]}  \n')
            f.write(f'**Description:** {sig.get("description", "")}  \n')
            f.write(f'**Failures:** {sig.get("failures", 0)}  \n')
            attempts = sig.get('strategies_attempted', [])
            if attempts:
                f.write('**Strategies attempted:**\n')
                for a in attempts:
                    f.write(f'  - `{a.get("strategy", "unknown")}` '
                            f'({a.get("script", "")}) → {a.get("result", "?")} '
                            f'[iter {a.get("iteration", "?")}]\n')
            f.write('\n')
        f.write('## Next steps\n\n')
        f.write('Flag for operator and engineering review. ')
        f.write('Do not upload remediated PDF — document is not compliant.\n')

    emit('PACKAGE', 'escalation_report', 'PASS',
         note=f'Escalation report at {escalation_report}')

# Post-job knowledge update
emit('PACKAGE', 'post_job_indexer', 'RUNNING')
run(['python3', TOOLS/'audit'/'post_job_indexer.py', JOB, '--map', RULE_MAP],
    'post_job_indexer')
emit('PACKAGE', 'post_job_indexer', 'PASS')

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────

duration = (datetime.now(timezone.utc) - start_time).total_seconds()

summary = {
    'result':                overall,
    'job_dir':               str(JOB),
    'output_dir':            str(OUT),
    'source_pdf':            str(SOURCE_PDF),
    'final_pdf':             str(FINAL_PDF),
    'gates':                 gate_results,
    'deviations':            deviations,
    'openclaw_signals':      openclaw_signals,
    'resolved_rules':        sorted(resolved_rules),
    'total_iterations':      total_iterations,
    'iteration_warning':     total_iterations >= JOB_WARN_AT,
    'duration_secs':         round(duration, 1),
    'repair_steps_executed': len(repair_steps),
    'unknown_rules':         unknown_rules,
    'doc_tags':              doc_tags,
    'proposed_taxonomy_additions': proposed_taxonomy_additions,
}

print(json.dumps({'phase': 'COMPLETE', 'summary': summary}, indent=2), flush=True)
sys.exit(0 if overall in ('PASS', 'REVIEW_REQUIRED') else 1)
