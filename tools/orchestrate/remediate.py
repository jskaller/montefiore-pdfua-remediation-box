#!/usr/bin/env python3
"""
remediate.py
Single-entry-point orchestrator for Montefiore PDF/UA remediation.

Replaces the agent's need to interpret AGENTS.md step-by-step. Handles:
  - Job scaffolding
  - All pre-flight and audit gates
  - Repair plan generation (veraPDF + table semantics)
  - Repair step execution with Layer 1 + Layer 2 signal detection
  - Post-repair validation
  - QA gates
  - Packaging
  - Knowledge update (post_job_indexer)

The agent's role is reduced to:
  1. Call this script
  2. Execute any DEVIATION steps it surfaces
  3. Provide metadata args (--title, --subject, --keywords)

Signal layers:
  Layer 1 — execution signals (exit code, missing output, JSON parse failure)
             detected automatically, agent never sees successful steps
  Layer 2 — outcome signals (rule still fails after mapped script ran)
             detected by targeted re-check after each repair
  Layer 3 — semantic signals (plan wrong for this document, novel failures)
             surfaced to agent with full context for reasoning

Usage:
  remediate.py <workspace> <ticket> <source-pdf-basename>
    --title    "Document Title"
    --subject  "One sentence subject"
    --keywords "keyword1, keyword2, ..."
    [--language en-US]
    [--dry-run]     Print plan without executing

  workspace     /app/workspace
  ticket        MM-TEST
  basename      "Montefiore ROI form instructions-English"

Output:
  Streams JSON progress lines to stdout:
    {"phase": "...", "step": "...", "result": "...", "note": "..."}

  Final output:
    {"result": "PASS|REVIEW_REQUIRED|FAIL|DEVIATION",
     "job_dir": "...", "output_dir": "...",
     "deliverables": {...},
     "deviations": [...],
     "gates": {...}}

Exit codes:
  0  PASS or REVIEW_REQUIRED
  1  FAIL or unresolved DEVIATION
  2  usage/setup error
"""
import sys, json, subprocess, shutil, argparse, hashlib, re
from pathlib import Path
from datetime import datetime, timezone

# ── Args ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument('workspace')
parser.add_argument('ticket')
parser.add_argument('basename')
parser.add_argument('--title',       required=True)
parser.add_argument('--subject',     required=True)
parser.add_argument('--keywords',    required=True)
parser.add_argument('--language',    default='en-US')
parser.add_argument('--dry-run',     action='store_true')
args = parser.parse_args()

WORKSPACE   = Path(args.workspace)
TICKET      = args.ticket
BASENAME    = Path(args.basename).stem
SAFE_BASE   = BASENAME.replace(' ', '_').replace('/', '_')
LANGUAGE    = args.language

APP         = Path('/app')
TOOLS       = APP / 'tools'
VERAPDF_BIN = Path('/opt/verapdf/arlington-pdf-model-checker')
PROFILES    = WORKSPACE / 'assets' / 'validation_profiles' / 'veraPDF-validation-profiles-integration'
RULE_MAP    = TOOLS / 'audit' / 'rule_repair_map.json'

JOB_NAME    = f'{TICKET}_{SAFE_BASE}'
JOB_DIR     = WORKSPACE / 'jobs'   / JOB_NAME
OUTPUT_DIR  = WORKSPACE / 'output' / f'{TICKET}_remediated'
SOURCE_PDF  = WORKSPACE / 'input'  / TICKET / f'{BASENAME}.pdf'

# ── Helpers ───────────────────────────────────────────────────────────────────

deviations = []
gate_results = {}
start_time = datetime.now(timezone.utc)

def emit(phase, step, result, note=None, data=None):
    """Stream a progress line to stdout."""
    obj = {'phase': phase, 'step': step, 'result': result}
    if note:  obj['note'] = note
    if data:  obj['data'] = data
    print(json.dumps(obj), flush=True)

def emit_deviation(step, expected, actual, context, layer):
    """Surface a deviation to the agent."""
    dev = {
        'layer':    layer,
        'step':     step,
        'expected': expected,
        'actual':   actual,
        'context':  context,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    deviations.append(dev)
    print(json.dumps({'phase': 'DEVIATION', 'layer': layer, 'step': step,
                      'expected': expected, 'actual': actual,
                      'context': context}), flush=True)

def run(cmd, label, capture=True):
    """Run a shell command. Returns (exit_code, stdout, stderr)."""
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

# ── Result codes that count as PASS ──────────────────────────────────────────

PASS_CODES = {
    'PASS', 'FIXED', 'ALREADY_CORRECT', 'PASS_WITH_MIXED_PAGES',
    'PASS_WITH_ONLY_NATIVE_TEXT', 'SKIPPED', 'OK', 'PLAN_READY',
    'NO_FAILURES', 'NEEDS_REVIEW'
}

def is_pass(result):
    return result in PASS_CODES

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
scaffold = load_json(Path('/dev/stdin').read_text() if False else None) or load_json(
    # parse from stdout since scaffold writes JSON to stdout
    None
)
# Parse scaffold JSON from stdout
try:
    scaffold = json.loads(out)
    JOB_DIR_ACTUAL    = Path(scaffold['job_dir'])
    OUTPUT_DIR_ACTUAL = Path(scaffold['output_dir'])
except Exception:
    JOB_DIR_ACTUAL    = JOB_DIR
    OUTPUT_DIR_ACTUAL = OUTPUT_DIR

if rc != 0:
    emit_deviation('scaffold', 'exit_code=0', f'exit_code={rc}', err, layer=1)
    sys.exit(2)
emit('SETUP', 'scaffold', 'PASS', data={'job_dir': str(JOB_DIR_ACTUAL), 'output_dir': str(OUTPUT_DIR_ACTUAL)})

JOB   = JOB_DIR_ACTUAL
OUT   = OUTPUT_DIR_ACTUAL
REPAIR_DIR = JOB / 'repair'
AUDIT_DIR  = JOB / 'audit'
QA_DIR     = JOB / 'qa'
REPORTS_DIR= JOB / 'reports'

# Copy source PDF
PASS0 = REPAIR_DIR / 'pass0_source.pdf'
emit('SETUP', 'copy_source', 'RUNNING')
shutil.copy2(SOURCE_PDF, PASS0)
emit('SETUP', 'copy_source', 'PASS', data={'pass0': str(PASS0)})

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
ocr_data = load_json(AUDIT_DIR/'detect_image_only_pages.json')
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
qpdf_data = load_json(AUDIT_DIR/'qpdf_check.json')
qpdf_result = get_result(qpdf_data)
gate_results['qpdf'] = qpdf_result

if not is_pass(qpdf_result):
    emit_deviation('qpdf_check', 'PASS', qpdf_result,
                   qpdf_data.get('errors', '') if qpdf_data else '',
                   layer=1)
    sys.exit(1)
emit('PREFLIGHT', 'qpdf_check', qpdf_result)

# 1c. Struct tree pre-flight — detect untagged PDFs early
emit('PREFLIGHT', 'struct_tree_check', 'RUNNING')
try:
    import fitz as _fitz
    _doc = _fitz.open(str(PASS0))
    _catalog = _doc.pdf_catalog()
    _str_ref = _doc.xref_get_key(_catalog, 'StructTreeRoot')
    _has_struct = _str_ref[0] != 'null' and bool(_str_ref[1])
    _doc.close()
except Exception as _e:
    _has_struct = None

if _has_struct is False:
    gate_results['struct_tree_check'] = 'FAIL'
    emit('PREFLIGHT', 'struct_tree_check', 'FAIL',
         note='No StructTreeRoot — running fix_untagged_pdf.py to auto-generate structure tree')

    # Auto-fix: generate basic structure tree before continuing
    untagged_fix = TOOLS / 'repair' / 'fix_untagged_pdf.py'
    pass1_tagged = REPAIR_DIR / 'pass1_fix_untagged.pdf'

    if untagged_fix.exists():
        rc_tag, out_tag, _ = run(
            ['python3', untagged_fix, PASS0, pass1_tagged,
             '--out', AUDIT_DIR / 'fix_untagged.json'],
            'fix_untagged_pdf'
        )
        tag_data = None
        try:
            tag_data = json.loads(out_tag)
        except Exception:
            pass

        if rc_tag == 0 and pass1_tagged.exists():
            emit('PREFLIGHT', 'fix_untagged_pdf', 'FIXED',
                 note='Basic structure tree generated. Running content marking pass.')

            # Second pass: connect struct tree to content streams via MCIDs
            marking_fix  = TOOLS / 'repair' / 'fix_struct_content_marking.py'
            pass2_marked = REPAIR_DIR / 'pass2_fix_struct_content_marking.pdf'
            pass_num = 3  # next pass number for repair chain

            if marking_fix.exists():
                rc_mark, out_mark, _ = run(
                    ['python3', marking_fix, pass1_tagged, pass2_marked,
                     '--out', AUDIT_DIR / 'fix_struct_content_marking.json'],
                    'fix_struct_content_marking'
                )
                if rc_mark == 0 and pass2_marked.exists():
                    PASS0 = pass2_marked
                    emit('PREFLIGHT', 'fix_struct_content_marking', 'FIXED',
                         note='Content streams marked with MCID tags. ParentTree built.')
                else:
                    # Fall back to just the struct tree pass
                    PASS0 = pass1_tagged
                    emit('PREFLIGHT', 'fix_struct_content_marking', 'WARN',
                         note='Content marking failed — using hollow struct tree')
            else:
                PASS0 = pass1_tagged
                emit('PREFLIGHT', 'fix_struct_content_marking', 'WARN',
                     note='fix_struct_content_marking.py not found — skipped')

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
    gate_results['struct_tree_check'] = 'PASS'
    emit('PREFLIGHT', 'struct_tree_check', 'PASS' if _has_struct else 'UNKNOWN')

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Audit gates (all run before any repair)
# ─────────────────────────────────────────────────────────────────────────────

# 2a. Baseline veraPDF
emit('AUDIT', 'verapdf_baseline', 'RUNNING')
rc, out, _ = run(
    ['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
     VERAPDF_BIN, PROFILES, PASS0, AUDIT_DIR],
    'verapdf_baseline'
)
# Save pre-repair copies immediately
for src, dst in [
    (AUDIT_DIR/'verapdf_pdfua_ua1.xml',        AUDIT_DIR/'verapdf_pre_pdfua1.xml'),
    (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml',  AUDIT_DIR/'verapdf_pre_wcag.xml'),
]:
    if Path(src).exists():
        shutil.copy2(src, dst)

verapdf_summary = load_json(AUDIT_DIR/'verapdf_summary.json')
verapdf_result  = get_result(verapdf_summary)
gate_results['verapdf_baseline'] = verapdf_result
# Non-compliant is expected here — we log it but don't stop
emit('AUDIT', 'verapdf_baseline', verapdf_result,
     note='Failures expected — repair plan will address them')

# 2b. Parse veraPDF failures
emit('AUDIT', 'parse_failures', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'parse_verapdf_summary.py',
     AUDIT_DIR/'verapdf_pre_pdfua1.xml',
     AUDIT_DIR/'verapdf_pre_wcag.xml'],
    'parse_failures'
)
# Write failures.json from stdout
failures_path = AUDIT_DIR / 'failures.json'
try:
    failures_data = json.loads(out)
    failures_path.write_text(json.dumps(failures_data, indent=2))
    emit('AUDIT', 'parse_failures', 'PASS',
         data={'unique_rules': failures_data.get('unique_rules_failing', 0),
               'total_failures': failures_data.get('total_failures', 0)})
except Exception as e:
    emit_deviation('parse_failures', 'valid JSON', f'parse error: {e}', out[:200], layer=1)
    # Non-fatal — continue with empty failures
    failures_path.write_text('{"result":"PASS","failures_by_rule":[]}')
    failures_data = {'failures_by_rule': []}

# 2c. Metadata audit
emit('AUDIT', 'metadata_parity', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'metadata_xmp_parity_audit.py', PASS0,
     '--out', AUDIT_DIR/'metadata_pre.json'],
    'metadata_parity'
)
meta_pre = load_json(AUDIT_DIR/'metadata_pre.json')
gate_results['metadata_pre'] = get_result(meta_pre)
emit('AUDIT', 'metadata_parity', get_result(meta_pre))

# 2d. Preservation audit
emit('AUDIT', 'preservation', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'qa'/'preservation_audit.py',
     PASS0, PASS0,
     '--out', AUDIT_DIR/'preservation_pre.json'],
    'preservation'
)
pres_pre = load_json(AUDIT_DIR/'preservation_pre.json')
gate_results['preservation_pre'] = get_result(pres_pre)
if pres_pre and not is_pass(get_result(pres_pre)):
    emit_deviation('preservation', 'PASS', get_result(pres_pre),
                   'Source PDF may have native text issues', layer=1)
emit('AUDIT', 'preservation', get_result(pres_pre))

# 2e. Table semantics audit
emit('AUDIT', 'table_semantics', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'table_semantics_audit.py', PASS0,
     '--out', AUDIT_DIR/'table_semantics_pre.json'],
    'table_semantics'
)
table_pre = load_json(AUDIT_DIR/'table_semantics_pre.json')
gate_results['table_semantics_pre'] = get_result(table_pre)
emit('AUDIT', 'table_semantics', get_result(table_pre))

# Extract TH scope issue count for repair plan
th_missing = 0
if table_pre:
    th_missing = table_pre.get('th_missing_scope', 0)

# 2f. Contrast audit
emit('AUDIT', 'contrast', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'contrast_audit.py', PASS0,
     '--out', AUDIT_DIR/'contrast_pre.json'],
    'contrast'
)
contrast_pre = load_json(AUDIT_DIR/'contrast_pre.json')
gate_results['contrast_pre'] = get_result(contrast_pre)
emit('AUDIT', 'contrast', get_result(contrast_pre))

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — removed; plan generation moved inside iterative repair loop
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — Alt text check (before repair loop)
# ─────────────────────────────────────────────────────────────────────────────

# Determine alt text branch before entering repair loop
BASENAME_SAFE = SAFE_BASE
ALT_MAP_JOB   = REPORTS_DIR / 'alt_map_approved.json'
ALT_MAP_ASSET = WORKSPACE / 'assets' / 'alt_maps' / f'{BASENAME_SAFE}_alt_map_approved.json'

alt_branch = 'NONE'
if ALT_MAP_JOB.exists():
    alt_branch = 'A_LOCAL'
elif ALT_MAP_ASSET.exists():
    alt_branch = 'A_ASSET'
    shutil.copy2(ALT_MAP_ASSET, ALT_MAP_JOB)
else:
    alt_branch = 'B'

emit('PLAN', 'alt_text_branch', alt_branch,
     note='Branch A: map exists, apply directly. Branch B: generate drafts, auto-approve, apply.')

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — Iterative repair loop
#
# Each iteration:
#   1. Run veraPDF (PDF/UA-1 + WCAG only) on current_pdf
#   2. Check termination conditions (clean / stuck / regression / no-plan /
#      max-iterations)
#   3. Build repair plan from current failures
#   4. Execute repair steps → advance current_pdf
#
# Termination states:
#   PASS        — veraPDF reports zero failures
#   STUCK       — failing rule set identical to prior iteration (no progress)
#   REGRESSION  — failure count increased vs prior iteration
#   NO_PLAN     — remaining rules have no mapped repair script
#   MAX_ITER    — hit MAX_ITERATIONS without resolving all failures
# ─────────────────────────────────────────────────────────────────────────────

MAX_ITERATIONS      = 5
current_pdf         = PASS0
pass_num            = 1          # global monotone counter; never resets
terminal_state      = None
prior_failing_rules = None       # set of rule_id strings from last iteration
prior_failure_count = None       # int; used for regression detection
iteration           = 0

# Pre-initialize loop-output variables; overwritten on first build_plan call.
# Guards against NameError in the summary if loop terminates before build_plan
# is reached (e.g. PASS or REGRESSION on iteration 1).
repair_steps       = []
manual_escalations = []
unknown_rules      = []
failing_rules      = set()
failures_data      = {'failures_by_rule': [], 'total_failures': 0}

def next_pass(iter_num, label):
    """Return next sequential output path, namespaced by iteration."""
    global pass_num
    p = REPAIR_DIR / f'pass{iter_num}_{pass_num}_{label}.pdf'
    pass_num += 1
    return p

def run_verapdf_for_loop(input_pdf, iter_num):
    """
    Run the three veraPDF profiles against input_pdf, save XMLs under
    audit/ with iteration suffix, parse only PDF/UA-1 + WCAG results.
    Returns (failing_rules: set[str], failures_data: dict, failure_count: int).
    """
    rc_v, _, _ = run(
        ['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
         VERAPDF_BIN, PROFILES, input_pdf, AUDIT_DIR],
        f'verapdf_iter{iter_num}'
    )

    # Snapshot XMLs with iteration suffix so each pass is preserved
    for src, dst in [
        (AUDIT_DIR/'verapdf_pdfua_ua1.xml',
         AUDIT_DIR/f'verapdf_iter{iter_num}_pdfua1.xml'),
        (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml',
         AUDIT_DIR/f'verapdf_iter{iter_num}_wcag.xml'),
        (AUDIT_DIR/'verapdf_iso32000_tagged.xml',
         AUDIT_DIR/f'verapdf_iter{iter_num}_iso32000.xml'),
    ]:
        if Path(src).exists():
            shutil.copy2(src, dst)

    # Parse only PDF/UA-1 + WCAG — ISO-32000-1 is not gated in the loop
    pdfua_xml = AUDIT_DIR / f'verapdf_iter{iter_num}_pdfua1.xml'
    wcag_xml  = AUDIT_DIR / f'verapdf_iter{iter_num}_wcag.xml'

    rc_p, out_p, _ = run(
        ['python3', TOOLS/'audit'/'parse_verapdf_summary.py',
         pdfua_xml, wcag_xml],
        f'parse_failures_iter{iter_num}'
    )
    try:
        fd = json.loads(out_p)
    except Exception:
        fd = {'failures_by_rule': [], 'total_failures': 0}

    failures_path_iter = AUDIT_DIR / f'failures_iter{iter_num}.json'
    failures_path_iter.write_text(json.dumps(fd, indent=2))

    rules = {f['rule_id'] for f in fd.get('failures_by_rule', [])}
    count = fd.get('total_failures', 0)
    return rules, fd, count, failures_path_iter

def build_plan(failures_path_iter, iter_num):
    """
    Call lookup_repair_plan.py and inject TH fix if needed.
    Returns (repair_steps, manual_escalations, unknown_rules).
    """
    plan_path_iter = AUDIT_DIR / f'repair_plan_iter{iter_num}.json'
    rc_l, out_l, _ = run(
        ['python3', TOOLS/'audit'/'lookup_repair_plan.py',
         failures_path_iter, '--map', RULE_MAP],
        f'lookup_repair_plan_iter{iter_num}'
    )
    try:
        pd = json.loads(out_l)
        plan_path_iter.write_text(json.dumps(pd, indent=2))
    except Exception:
        pd = {'result': 'NO_FAILURES', 'repair_steps': [],
              'manual_escalations': [], 'unknown_rules': []}
        plan_path_iter.write_text(json.dumps(pd, indent=2))

    steps      = pd.get('repair_steps', [])
    manual_esc = pd.get('manual_escalations', [])
    unknown    = pd.get('unknown_rules', [])

    # Inject TH fix if table semantics pre-audit found missing scope
    table_headers_script = 'tools/repair/fix_table_headers.py'
    has_table_fix = any(s['repair_script'] == table_headers_script for s in steps)
    if th_missing > 0 and not has_table_fix:
        steps.append({
            'step':            len(steps) + 1,
            'repair_script':   table_headers_script,
            'repair_order':    10,
            'run_last':        True,
            'args_pattern':    '<input.pdf> <output.pdf>',
            'rules_addressed': ['table_semantics/TH_missing_scope'],
            'confidence':      'CONFIRMED',
            'notes':           f'Injected: {th_missing} TH cells missing Scope. MUST RUN LAST.'
        })
        steps.sort(key=lambda s: (s.get('run_last', False), s.get('repair_order', 99)))
        for i, s in enumerate(steps, 1):
            s['step'] = i

    return steps, manual_esc, unknown

def execute_repair_step(step, input_pdf, iter_num):
    """
    Run a single repair step. Returns output_pdf path on success, or
    input_pdf if the step failed (so the chain stays valid).
    Emits deviations and progress lines. Handles alt-text and metadata
    special cases identically to the original single-pass logic.
    """
    script   = step['repair_script']
    rules    = step['rules_addressed']
    conf     = step['confidence']

    script_path = APP / script
    if not script_path.exists():
        emit_deviation(script, 'script_exists', 'NOT_FOUND',
                       f'Script not found at {script_path}', layer=1)
        return input_pdf

    script_label = Path(script).stem
    output_pdf   = next_pass(iter_num, script_label)

    emit('REPAIR', script_label, 'RUNNING',
         data={'iteration': iter_num, 'rules': rules, 'confidence': conf})

    # ── Special handling: fix_figure_alt_text ────────────────────────────────
    if 'fix_figure_alt_text' in script:
        if alt_branch in ('A_LOCAL', 'A_ASSET'):
            rc, out, err = run(
                ['python3', script_path, input_pdf, output_pdf,
                 '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                script_label
            )
            review_html = REPORTS_DIR / 'alt_text_review.html'
            draft_json  = REPORTS_DIR / 'alt_text_drafts.json'
            try:
                approved = json.loads(ALT_MAP_JOB.read_text())
                draft = {
                    'result': 'PASS',
                    'pdf': str(input_pdf),
                    'model': 'approved_map',
                    'figures_total':   len(approved.get('figures', {})),
                    'figures_drafted': len(approved.get('figures', {})),
                    'figures_skipped': 0,
                    'figures': {
                        idx: {
                            'figure_index':  int(idx),
                            'page':          1,
                            'xref':          0,
                            'alt_text_draft': entry.get('alt_text', ''),
                            'source':        'approved_map',
                            'model':         'approved_map',
                            'instruction':   entry.get('instruction'),
                            'decorative':    entry.get('decorative', False),
                        }
                        for idx, entry in approved.get('figures', {}).items()
                    }
                }
                draft_json.write_text(json.dumps(draft, indent=2))
                run(
                    ['python3', TOOLS/'repair'/'generate_alt_text_review_report.py',
                     input_pdf,
                     '--draft', draft_json,
                     '--out', review_html,
                     '--map-out', REPORTS_DIR / 'alt_map_pre_approved.json'],
                    f'{script_label}_review_html'
                )
                emit('REPAIR', f'{script_label}_review_html', 'PASS',
                     note=f'Review HTML: {review_html}')
            except Exception as e:
                emit('REPAIR', f'{script_label}_review_html', 'WARN',
                     note=f'Could not generate review HTML: {e}')
        else:
            # Branch B — auto → drafts → auto-approve → apply
            auto_pdf    = REPAIR_DIR / f'pass{iter_num}_{pass_num}_alt_auto.pdf'
            auto_json   = AUDIT_DIR  / 'alt_text_auto_output.json'
            drafts_json = REPORTS_DIR / 'alt_text_drafts.json'
            review_html = REPORTS_DIR / 'alt_text_review.html'

            rc1, out1, _ = run(
                ['python3', script_path, input_pdf, auto_pdf,
                 '--language', LANGUAGE],
                f'{script_label}_auto'
            )
            auto_data = None
            try:
                auto_data = json.loads(out1)
                auto_json.write_text(json.dumps(auto_data, indent=2))
            except Exception:
                pass

            rc2, out2, _ = run(
                ['python3', TOOLS/'repair'/'generate_alt_text_drafts.py',
                 auto_pdf, '--fix-output', auto_json, '--out', drafts_json],
                f'{script_label}_drafts'
            )
            rc3, out3, _ = run(
                ['python3', TOOLS/'repair'/'generate_alt_text_review_report.py',
                 drafts_json, review_html],
                f'{script_label}_review'
            )
            if drafts_json.exists():
                shutil.copy2(drafts_json, ALT_MAP_JOB)
                emit('REPAIR', f'{script_label}_auto_approve', 'PASS',
                     note='Drafts auto-approved.')
            rc, out, err = run(
                ['python3', script_path, auto_pdf, output_pdf,
                 '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                f'{script_label}_manual'
            )
            asset_dir = WORKSPACE / 'assets' / 'alt_maps'
            asset_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ALT_MAP_JOB,
                         asset_dir / f'{BASENAME_SAFE}_alt_map_approved.json')

    # ── Special handling: fix_metadata_xmp_parity ────────────────────────────
    elif 'fix_metadata_xmp_parity' in script:
        rc, out, err = run(
            ['python3', script_path, input_pdf, output_pdf,
             '--title',    args.title,
             '--subject',  args.subject,
             '--keywords', args.keywords,
             '--language', LANGUAGE],
            script_label
        )

    # ── All other repair scripts ──────────────────────────────────────────────
    else:
        rc, out, err = run(
            ['python3', script_path, input_pdf, output_pdf],
            script_label
        )

    # ── Layer 1: execution signal ─────────────────────────────────────────────
    step_data = None
    try:
        step_data = json.loads(out)
    except Exception:
        pass
    step_result = get_result(step_data) if step_data else ('PASS' if rc == 0 else 'ERROR')

    if rc != 0 and not is_pass(step_result):
        emit_deviation(
            script_label,
            'exit_code=0 or PASS result',
            f'exit_code={rc}, result={step_result}',
            (step_data.get('error', '') if step_data else err[:300]),
            layer=1
        )
        return input_pdf   # keep chain valid; don't advance

    if not output_pdf.exists():
        emit_deviation(
            script_label,
            f'output_pdf exists at {output_pdf}',
            'output_pdf missing',
            f'Script exited {rc} but did not produce output file',
            layer=1
        )
        return input_pdf

    emit('REPAIR', script_label, step_result,
         data={'iteration': iter_num, 'rules': rules, 'output': str(output_pdf)})
    return output_pdf


# ── Main loop ─────────────────────────────────────────────────────────────────

while iteration < MAX_ITERATIONS:
    iteration += 1
    emit('ITERATE', 'start', 'RUNNING', data={'iteration': iteration})

    # Step 1 — validate current state
    failing_rules, failures_data, failure_count, failures_path_iter = \
        run_verapdf_for_loop(current_pdf, iteration)

    # Keep a canonical failures.json pointing at the most recent results
    (AUDIT_DIR / 'failures.json').write_text(
        json.dumps(failures_data, indent=2))

    # Step 2 — termination checks (order matters)

    # 2a. Clean pass
    if not failing_rules:
        terminal_state = 'PASS'
        emit('ITERATE', 'terminal', terminal_state,
             data={'iteration': iteration, 'failure_count': 0})
        break

    # 2b. Regression (more failures than last iteration)
    if prior_failure_count is not None and failure_count > prior_failure_count:
        terminal_state = 'REGRESSION'
        emit('ITERATE', 'terminal', terminal_state,
             data={
                 'iteration':      iteration,
                 'failure_count':  failure_count,
                 'prior_count':    prior_failure_count,
                 'delta':          failure_count - prior_failure_count,
             })
        # Surface every new/worsened rule as a Layer 2 deviation
        new_rules = failing_rules - (prior_failing_rules or set())
        for r in sorted(new_rules):
            emit_deviation(
                f'iterate/{r}',
                'failure count non-increasing',
                f'regression: {r} appeared or worsened at iteration {iteration}',
                f'prior_count={prior_failure_count} current_count={failure_count}',
                layer=2
            )
        break

    # 2c. Stuck (same exact rule set, no regression — genuine stall)
    if failing_rules == prior_failing_rules:
        terminal_state = 'STUCK'
        emit('ITERATE', 'terminal', terminal_state,
             data={
                 'iteration':     iteration,
                 'failure_count': failure_count,
                 'stuck_rules':   sorted(failing_rules),
             })
        break

    # Step 3 — build plan from current failures
    repair_steps, manual_escalations, unknown_rules = \
        build_plan(failures_path_iter, iteration)

    # On first iteration, surface manual escalations and unknown rules
    # (was previously done in the now-removed Phase 3)
    if iteration == 1:
        for esc in manual_escalations:
            emit('PLAN', 'manual_escalation', 'ESCALATE',
                 note=f"{esc['rule_id']}: {esc.get('notes','')}")
        for ur in unknown_rules:
            emit('PLAN', 'unknown_rule', 'AGENT_REASONING_REQUIRED',
                 note=f"{ur['rule_id']}: {ur.get('description','')} ({ur.get('failures',0)} failures)")

    # 2d. No plan (all remaining rules unknown/manual, nothing to run)
    if not repair_steps:
        terminal_state = 'NO_PLAN'
        emit('ITERATE', 'terminal', terminal_state,
             data={
                 'iteration':          iteration,
                 'failure_count':      failure_count,
                 'unknown_rules':      [r['rule_id'] for r in unknown_rules],
                 'manual_escalations': [r['rule_id'] for r in manual_escalations],
             })
        break

    emit('ITERATE', 'plan_ready', 'RUNNING',
         data={
             'iteration':     iteration,
             'failure_count': failure_count,
             'prior_count':   prior_failure_count,
             'repair_steps':  len(repair_steps),
             'rules_failing': sorted(failing_rules),
             'rules_cleared': sorted((prior_failing_rules or set()) - failing_rules),
         })

    # Step 4 — execute repair steps
    for step in repair_steps:
        current_pdf = execute_repair_step(step, current_pdf, iteration)

    prior_failing_rules = failing_rules
    prior_failure_count = failure_count

else:
    # Exhausted MAX_ITERATIONS without breaking
    terminal_state = 'MAX_ITER'
    emit('ITERATE', 'terminal', terminal_state,
         data={'iteration': iteration, 'failure_count': prior_failure_count})

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — Post-loop validation gates
#
# veraPDF was run inside the loop on every iteration; no second run here.
# We report the loop's terminal state as the veraPDF gate result and run
# the remaining four audit gates against the final PDF.
# ─────────────────────────────────────────────────────────────────────────────

FINAL_PDF = current_pdf
emit('VALIDATE', 'final_pdf', 'INFO',
     data={'path': str(FINAL_PDF), 'terminal_state': terminal_state,
           'iterations': iteration})

# Translate loop terminal_state → verapdf_post gate result
_TERMINAL_TO_GATE = {
    'PASS':       'PASS',
    'STUCK':      'REVIEW_REQUIRED',
    'REGRESSION': 'FAIL',
    'NO_PLAN':    'REVIEW_REQUIRED',
    'MAX_ITER':   'REVIEW_REQUIRED',
}
verapdf_post_result = _TERMINAL_TO_GATE.get(terminal_state, 'FAIL')
gate_results['verapdf_post'] = verapdf_post_result

# Surface any remaining failures as Layer 2 deviations
remaining_failures = failures_data.get('failures_by_rule', []) \
    if terminal_state != 'PASS' else []
for failure in remaining_failures:
    rule_id = failure.get('rule_id', 'unknown')
    emit_deviation(
        f'verapdf_post/{rule_id}',
        'rule resolved',
        f'rule still failing ({failure.get("failures", 0)} failures) '
        f'at loop exit ({terminal_state})',
        f'Loop terminated with {terminal_state} after {iteration} iteration(s).',
        layer=2
    )

emit('VALIDATE', 'verapdf_post', verapdf_post_result,
     data={'terminal_state': terminal_state,
           'iterations': iteration,
           'remaining_failures': len(remaining_failures)})

# 6b. Post-repair metadata audit
emit('VALIDATE', 'metadata_post', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'metadata_xmp_parity_audit.py', FINAL_PDF,
     '--out', AUDIT_DIR/'metadata_post.json'],
    'metadata_post'
)
meta_post = load_json(AUDIT_DIR/'metadata_post.json')
meta_post_result = get_result(meta_post)
gate_results['metadata_post'] = meta_post_result

if not is_pass(meta_post_result):
    emit_deviation('metadata_post', 'PASS', meta_post_result,
                   str(meta_post.get('failures', []) if meta_post else ''), layer=2)
emit('VALIDATE', 'metadata_post', meta_post_result)

# 6c. Post-repair table semantics
emit('VALIDATE', 'table_semantics_post', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'table_semantics_audit.py', FINAL_PDF,
     '--out', AUDIT_DIR/'table_semantics_post.json'],
    'table_semantics_post'
)
table_post = load_json(AUDIT_DIR/'table_semantics_post.json')
table_post_result = get_result(table_post)
gate_results['table_semantics_post'] = table_post_result

if not is_pass(table_post_result):
    emit_deviation('table_semantics_post', 'PASS', table_post_result,
                   f"TH missing scope: {table_post.get('th_missing_scope',0) if table_post else 'unknown'}",
                   layer=2)
emit('VALIDATE', 'table_semantics_post', table_post_result)

# 6d. Post-repair preservation
emit('VALIDATE', 'preservation_post', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'qa'/'preservation_audit.py',
     PASS0, FINAL_PDF,
     '--out', AUDIT_DIR/'preservation_post.json'],
    'preservation_post'
)
pres_post = load_json(AUDIT_DIR/'preservation_post.json')
pres_post_result = get_result(pres_post)
gate_results['preservation_post'] = pres_post_result

if not is_pass(pres_post_result):
    emit_deviation('preservation_post', 'PASS', pres_post_result,
                   'Native text may have been lost during repair', layer=2)
emit('VALIDATE', 'preservation_post', pres_post_result)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 7 — QA gates
# ─────────────────────────────────────────────────────────────────────────────

# 7a. Render compare
emit('QA', 'render_compare', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'qa'/'render_compare.py',
     PASS0, FINAL_PDF, QA_DIR,
     '--out', AUDIT_DIR/'render_compare.json'],
    'render_compare'
)
rc_data = load_json(AUDIT_DIR/'render_compare.json')
rc_result = get_result(rc_data)
gate_results['render_compare'] = rc_result
emit('QA', 'render_compare', rc_result)

# 7b. Visual QA
emit('QA', 'visual_qa', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'qa'/'visual_qa.py',
     FINAL_PDF, QA_DIR,
     '--out', AUDIT_DIR/'visual_qa.json'],
    'visual_qa'
)
vqa_data = load_json(AUDIT_DIR/'visual_qa.json')
vqa_result = get_result(vqa_data)
gate_results['visual_qa'] = vqa_result
emit('QA', 'visual_qa', vqa_result)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 8 — Packaging
# ─────────────────────────────────────────────────────────────────────────────

# Determine overall result before packaging
has_deviations = len(deviations) > 0
verapdf_passed = is_pass(verapdf_post_result)
critical_fails = [k for k, v in gate_results.items()
                  if not is_pass(v) and k in
                  ('verapdf_post', 'metadata_post', 'preservation_post')]

if critical_fails:
    overall = 'FAIL'
elif has_deviations:
    overall = 'REVIEW_REQUIRED'
else:
    overall = 'PASS'

emit('PACKAGE', 'overall_result', overall,
     data={'critical_fails': critical_fails, 'deviations': len(deviations)})

# 8a. Write STATUS.json
emit('PACKAGE', 'status_json', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'packaging'/'status_json_writer.py', JOB,
     '--pdf', str(SOURCE_PDF)],
    'status_json'
)
emit('PACKAGE', 'status_json', 'PASS' if rc == 0 else 'FAIL')

# 8b. Package deliverables
emit('PACKAGE', 'package_deliverables', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'packaging'/'package_deliverables.py',
     JOB, FINAL_PDF,
     '--output-dir', OUT,
     '--source-pdf', str(SOURCE_PDF)],
    'package_deliverables'
)
pkg_data = None
try:
    pkg_data = json.loads(out)
except Exception:
    pass

pkg_result = get_result(pkg_data) if pkg_data else ('PASS' if rc == 0 else 'FAIL')
emit('PACKAGE', 'package_deliverables', pkg_result,
     data=pkg_data.get('deliverables') if pkg_data else None)

# 8c. Post-job knowledge update
emit('PACKAGE', 'post_job_indexer', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'post_job_indexer.py', JOB,
     '--map', RULE_MAP],
    'post_job_indexer'
)
emit('PACKAGE', 'post_job_indexer', 'PASS' if rc == 0 else 'WARN')

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────

duration = (datetime.now(timezone.utc) - start_time).total_seconds()

deliverables = {}
if pkg_data and 'deliverables' in pkg_data:
    deliverables = pkg_data['deliverables']

summary = {
    'result':        overall,
    'job_dir':       str(JOB),
    'output_dir':    str(OUT),
    'source_pdf':    str(SOURCE_PDF),
    'final_pdf':     str(FINAL_PDF),
    'deliverables':  deliverables,
    'gates':         gate_results,
    'deviations':    deviations,
    'duration_secs': round(duration, 1),
    'iterations':               iteration,
    'terminal_state':           terminal_state,
    'repair_steps_executed':    len(repair_steps),
    'unknown_rules':            unknown_rules,
    'manual_escalations':       manual_escalations,
}

print(json.dumps({'phase': 'COMPLETE', 'summary': summary}, indent=2), flush=True)
sys.exit(0 if overall in ('PASS', 'REVIEW_REQUIRED') else 1)
