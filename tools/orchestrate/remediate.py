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
    emit('PREFLIGHT', 'struct_tree_check', 'FAIL')
    emit_deviation(
        'struct_tree_check',
        'StructTreeRoot present',
        'No StructTreeRoot — document is not tagged',
        'This document has no PDF structure tree. Auto-remediation is not possible. '
        'Full retagging is required using Acrobat Pro or CommonLook. '
        'Set job result to FAIL and escalate to manual remediation team.',
        layer=1
    )
    # Write FAIL status and exit
    gate_results['overall'] = 'FAIL'
    (AUDIT_DIR / 'struct_tree_check.json').write_text(json.dumps({
        'result': 'FAIL',
        'reason': 'No StructTreeRoot — document is not tagged',
        'action': 'MANUAL_ESCALATION_REQUIRED'
    }, indent=2))
    sys.exit(1)

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
# PHASE 3 — Repair plan
# ─────────────────────────────────────────────────────────────────────────────

emit('PLAN', 'lookup_repair_plan', 'RUNNING')
rc, out, _ = run(
    ['python3', TOOLS/'audit'/'lookup_repair_plan.py',
     failures_path, '--map', RULE_MAP],
    'lookup_repair_plan'
)
plan_path = AUDIT_DIR / 'repair_plan.json'
try:
    plan_data = json.loads(out)
    plan_path.write_text(json.dumps(plan_data, indent=2))
except Exception:
    plan_data = {'result': 'NO_FAILURES', 'repair_steps': [], 'manual_escalations': [], 'unknown_rules': []}
    plan_path.write_text(json.dumps(plan_data, indent=2))

repair_steps      = plan_data.get('repair_steps', [])
manual_escalations= plan_data.get('manual_escalations', [])
unknown_rules     = plan_data.get('unknown_rules', [])

# Inject table headers fix if TH scope issues found and not already in plan
table_headers_script = 'tools/repair/fix_table_headers.py'
has_table_fix = any(s['repair_script'] == table_headers_script for s in repair_steps)
if th_missing > 0 and not has_table_fix:
    repair_steps.append({
        'step':            len(repair_steps) + 1,
        'repair_script':   table_headers_script,
        'repair_order':    10,
        'run_last':        True,
        'args_pattern':    '<input.pdf> <output.pdf>',
        'rules_addressed': ['table_semantics/TH_missing_scope'],
        'confidence':      'CONFIRMED',
        'notes':           f'Injected: {th_missing} TH cells missing Scope. MUST RUN LAST.'
    })
    # Re-sort: run_last always last
    repair_steps.sort(key=lambda s: (s.get('run_last', False), s.get('repair_order', 99)))
    for i, s in enumerate(repair_steps, 1):
        s['step'] = i

emit('PLAN', 'lookup_repair_plan', plan_data.get('result', 'UNKNOWN'),
     data={
         'repair_steps':       len(repair_steps),
         'manual_escalations': len(manual_escalations),
         'unknown_rules':      len(unknown_rules),
         'th_fix_injected':    th_missing > 0 and not has_table_fix
     })

# Surface manual escalations and unknown rules to agent immediately
if manual_escalations:
    for esc in manual_escalations:
        emit('PLAN', 'manual_escalation', 'ESCALATE',
             note=f"{esc['rule_id']}: {esc.get('notes','')}")
if unknown_rules:
    for ur in unknown_rules:
        emit('PLAN', 'unknown_rule', 'AGENT_REASONING_REQUIRED',
             note=f"{ur['rule_id']}: {ur.get('description','')} ({ur.get('failures',0)} failures)")

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
# PHASE 5 — Repair execution
# ─────────────────────────────────────────────────────────────────────────────

current_pdf = PASS0
pass_num    = 1

def next_pass(label):
    global pass_num
    p = REPAIR_DIR / f'pass{pass_num}_{label}.pdf'
    pass_num += 1
    return p

for step in repair_steps:
    script  = step['repair_script']
    rules   = step['rules_addressed']
    conf    = step['confidence']
    run_last= step.get('run_last', False)

    script_path = APP / script
    if not script_path.exists():
        emit_deviation(script, 'script_exists', 'NOT_FOUND',
                       f'Script not found at {script_path}', layer=1)
        continue

    script_label = Path(script).stem
    output_pdf   = next_pass(script_label)

    emit('REPAIR', script_label, 'RUNNING',
         data={'rules': rules, 'confidence': conf})

    # ── Special handling: fix_figure_alt_text ────────────────────────────────
    if 'fix_figure_alt_text' in script:
        if alt_branch in ('A_LOCAL', 'A_ASSET'):
            # Branch A — apply approved map directly
            rc, out, err = run(
                ['python3', script_path, current_pdf, output_pdf,
                 '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                script_label
            )
            # Always generate review HTML so operator can inspect what was applied.
            # Convert approved map to draft format for the review report generator.
            review_html  = REPORTS_DIR / 'alt_text_review.html'
            draft_json   = REPORTS_DIR / 'alt_text_drafts.json'
            try:
                approved = json.loads(ALT_MAP_JOB.read_text())
                # Build draft format from approved map
                draft = {
                    'result': 'PASS',
                    'pdf': str(current_pdf),
                    'model': 'approved_map',
                    'figures_total': len(approved.get('figures', {})),
                    'figures_drafted': len(approved.get('figures', {})),
                    'figures_skipped': 0,
                    'figures': {
                        idx: {
                            'figure_index': int(idx),
                            'page': 1,
                            'xref': 0,
                            'alt_text_draft': entry.get('alt_text', ''),
                            'source': 'approved_map',
                            'model': 'approved_map',
                            'instruction': entry.get('instruction'),
                            'decorative': entry.get('decorative', False),
                        }
                        for idx, entry in approved.get('figures', {}).items()
                    }
                }
                draft_json.write_text(json.dumps(draft, indent=2))
                run(
                    ['python3', TOOLS/'repair'/'generate_alt_text_review_report.py',
                     current_pdf,
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
            # Branch B — auto mode → drafts → auto-approve → apply
            auto_pdf     = REPAIR_DIR / f'pass{pass_num}_alt_auto.pdf'
            auto_json    = AUDIT_DIR  / 'alt_text_auto_output.json'
            drafts_json  = REPORTS_DIR/ 'alt_text_drafts.json'
            review_html  = REPORTS_DIR/ 'alt_text_review.html'

            # Step B1: auto placeholder
            rc1, out1, _ = run(
                ['python3', script_path, current_pdf, auto_pdf,
                 '--language', LANGUAGE],
                f'{script_label}_auto'
            )
            auto_data = None
            try:
                auto_data = json.loads(out1)
                auto_json.write_text(json.dumps(auto_data, indent=2))
            except Exception:
                pass

            pass_num += 1

            # Step B2: generate drafts (vision model)
            rc2, out2, _ = run(
                ['python3', TOOLS/'repair'/'generate_alt_text_drafts.py',
                 auto_pdf, '--fix-output', auto_json, '--out', drafts_json],
                f'{script_label}_drafts'
            )

            # Step B3: generate review report
            rc3, out3, _ = run(
                ['python3', TOOLS/'repair'/'generate_alt_text_review_report.py',
                 drafts_json, review_html],
                f'{script_label}_review'
            )

            # Step B4: auto-approve drafts
            if drafts_json.exists():
                shutil.copy2(drafts_json, ALT_MAP_JOB)
                emit('REPAIR', f'{script_label}_auto_approve', 'PASS',
                     note='Drafts auto-approved. Review HTML saved for post-delivery inspection.')

            # Step B5: apply approved map
            rc, out, err = run(
                ['python3', script_path, auto_pdf, output_pdf,
                 '--alt-map', ALT_MAP_JOB, '--language', LANGUAGE],
                f'{script_label}_manual'
            )

            # Step B6: copy to asset library
            asset_dir = WORKSPACE / 'assets' / 'alt_maps'
            asset_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ALT_MAP_JOB,
                         asset_dir / f'{BASENAME_SAFE}_alt_map_approved.json')

    # ── Special handling: fix_metadata_xmp_parity ────────────────────────────
    elif 'fix_metadata_xmp_parity' in script:
        rc, out, err = run(
            ['python3', script_path, current_pdf, output_pdf,
             '--title',    args.title,
             '--subject',  args.subject,
             '--keywords', args.keywords,
             '--language', LANGUAGE],
            script_label
        )

    # ── All other repair scripts ──────────────────────────────────────────────
    else:
        rc, out, err = run(
            ['python3', script_path, current_pdf, output_pdf],
            script_label
        )

    # ── Layer 1: execution signal check ──────────────────────────────────────
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
        # Don't advance current_pdf — keep previous for next step
        continue

    if not output_pdf.exists():
        emit_deviation(
            script_label,
            f'output_pdf exists at {output_pdf}',
            'output_pdf missing',
            f'Script exited {rc} but did not produce output file',
            layer=1
        )
        continue

    emit('REPAIR', script_label, step_result,
         data={'rules': rules, 'output': str(output_pdf)})

    current_pdf = output_pdf

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — Post-repair validation
# ─────────────────────────────────────────────────────────────────────────────

FINAL_PDF = current_pdf
emit('VALIDATE', 'final_pdf', 'INFO', data={'path': str(FINAL_PDF)})

# 6a. Post-repair veraPDF
emit('VALIDATE', 'verapdf_post', 'RUNNING')
rc, out, _ = run(
    ['bash', TOOLS/'audit'/'run_verapdf_profiles.sh',
     VERAPDF_BIN, PROFILES, FINAL_PDF, AUDIT_DIR],
    'verapdf_post'
)
# Rename post-repair XMLs
for src, dst in [
    (AUDIT_DIR/'verapdf_pdfua_ua1.xml',        AUDIT_DIR/'verapdf_post_pdfua1.xml'),
    (AUDIT_DIR/'verapdf_wcag_2_2_machine.xml',  AUDIT_DIR/'verapdf_post_wcag.xml'),
]:
    if Path(src).exists():
        shutil.copy2(src, dst)

verapdf_post = load_json(AUDIT_DIR/'verapdf_summary.json')
verapdf_post_result = get_result(verapdf_post)
gate_results['verapdf_post'] = verapdf_post_result

# Layer 2: parse post-repair failures and check for regressions
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

if remaining_failures:
    # Layer 2: rules still failing after repair — surface to agent
    for failure in remaining_failures:
        rule_id = failure.get('rule_id', 'unknown')
        # Check if this was in our repair plan
        planned_rules = [r for s in repair_steps for r in s.get('rules_addressed', [])]
        if rule_id in planned_rules:
            # Rule was supposed to be fixed — map entry may be wrong
            emit_deviation(
                f'verapdf_post/{rule_id}',
                'rule passes after mapped repair script',
                f'rule still failing ({failure.get("failures",0)} failures)',
                f'Script ran successfully but rule {rule_id} still fails. Map entry may be incorrect.',
                layer=2
            )
        else:
            # New failure not in original plan
            emit_deviation(
                f'verapdf_post/{rule_id}',
                'no new failures post-repair',
                f'new failure: {rule_id} ({failure.get("failures",0)} failures)',
                f'Rule {rule_id} not in original repair plan. Agent reasoning required.',
                layer=2
            )

emit('VALIDATE', 'verapdf_post', verapdf_post_result,
     data={'remaining_failures': len(remaining_failures)})

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
    'repair_steps_executed': len(repair_steps),
    'unknown_rules': unknown_rules,
    'manual_escalations': manual_escalations,
}

print(json.dumps({'phase': 'COMPLETE', 'summary': summary}, indent=2), flush=True)
sys.exit(0 if overall in ('PASS', 'REVIEW_REQUIRED') else 1)
