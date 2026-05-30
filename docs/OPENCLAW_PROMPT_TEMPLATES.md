# OpenClaw Prompt Templates — Montefiore PDF/UA Implementation

**Purpose:** Structured prompts for OpenClaw + DeepSeek V4 Pro implementation
sessions. Each template is a complete prompt: slot in the current file contents
where indicated, send, iterate until the gate passes, then move on.

**Do not skip the gate sections.** The gates are what separate implementation
from verified implementation. A session that produces code but doesn't pass its
gate has not completed its task.

**Reasoning mode guidance:**
- Non-think: data-only changes (JSON seeding, map updates, rename/relocate)
- Think High: single-file logic changes (verdict.py, preservation_audit.py)
- Think Max: novel multi-file logic (residual_analyzer, indexer rewrite,
  discovery-mode validation, execution_log co-design)

**Regression baseline (applies to every milestone):**
MM-TEST2 (known-good document) must produce the same overall verdict after every
change. If MM-TEST2 degrades, stop — something broke regardless of whether the
target task looks correct.

---

## Template 0-A — Resolvability seeding (rule_repair_map.json)

**Reasoning mode:** Non-think
**Files needed:** `tools/audit/rule_repair_map.json` (current contents)

---

### SYSTEM PROMPT

You are implementing a specific, well-specified change to the Montefiore PDF/UA
remediation pipeline. All architectural decisions are already locked. Your job is
to implement exactly what is specified, not to re-derive or improve the
architecture. If the specification is ambiguous on a point, say so explicitly
rather than filling the gap with your own assumption.

### CONTEXT — Locked decisions

The `rule_repair_map.json` is gaining a new required field `resolvability` on
every rule entry. The five permitted values and their exact meanings are:

- `effective` — script exists, fix is trustworthy, clears to PASS unreviewed
- `repairable_unbuilt` — mechanical fix possible, no script yet, would clear to
  PASS once built. AI target (silent PASS path).
- `repairable_review` — fix can be attempted (vision LLM / font library /
  semantic inference) but result is a proposal; clears veraPDF but lands in
  REVIEW_REQUIRED pending human sign-off. AI target (review path).
- `not_auto_fixable` — no path even to a reviewable proposal. FAIL/escalate.
  Currently has NO residents in this map — if you find yourself assigning this,
  stop and flag it.
- `detector_mislabeled` — transitional flag: this rule's current `repair_script`
  is an audit-only script that writes no PDF. Will be fixed in M2.

A second new field `emits_review_artifact` (boolean) marks rules whose repair
generates a per-instance thumbnail+before/after report regardless of whether it
gates the verdict. Alt text (7.2, 7.3-x, 1.1.1) is `true`; it does NOT gate
the verdict. `repairable_review` rules are also `true` AND gate the verdict.

### TASK

Add `resolvability` and `emits_review_artifact` to every rule entry in
`rule_repair_map.json` according to this seed table. Do not change any other
fields. Do not add or remove rules.

| rule_id pattern | resolvability | emits_review_artifact | notes |
|---|---|---|---|
| 5, 6.2 | effective | false | |
| 6.7.2, 7.21.4.2(-1) | effective | false | |
| 6.2.11.8, 7.1, 7.1-1/2/3 | effective | false | |
| 7.1-content-unmarked | effective | false | |
| 7.1-untagged, 7.2-1, 7.18.3, 7.4.4 | effective | false | |
| 7.18.5, 7.18.1(-1) | effective | false | |
| 7.2, 7.3(-1/-3), 1.1.1 | effective | true | emits artifact; does NOT gate verdict |
| 7.5-1, 7.5-2 | effective | false | |
| 7.6-1 | effective | false | |
| 7.21.6-1 | detector_mislabeled | false | fix_notdef_glyphs is audit-only |
| WCAG/1.4.3 | detector_mislabeled | false | fix_contrast_color_runs is audit-only |
| 7.21.3-1 | repairable_review | true | font substitution — build in M3 |
| 7.18.3-1 | repairable_review | true | form-field tagging — build in M3 |

Current file contents:
[INSERT rule_repair_map.json CONTENTS HERE]

### GATE — must pass before moving on

1. **JSON validity:** `python3 -c "import json; json.load(open('tools/audit/rule_repair_map.json'))"` exits 0.
2. **Field presence:** every entry has both `resolvability` and `emits_review_artifact`.
3. **Value validity:** `resolvability` is one of the five permitted values; `emits_review_artifact` is boolean.
4. **No other changes:** `git diff tools/audit/rule_repair_map.json` shows only additions of the two new fields. No existing field values changed.
5. **Cross-reference:** every `repair_script` path that is non-null resolves to an existing file (`ls` confirms).

Run these checks and paste their output before marking this task done.

---

## Template 0-B — Gate-name registry (prerequisite for M1)

**Reasoning mode:** Think High
**Files needed:** `tools/packaging/package_scaffold.py`,
`tools/packaging/status_json_writer.py`, `tools/orchestrate/remediate.py`
(Phase 6 + Phase 8 sections specifically), `tools/audit/run_verapdf_profiles.sh`

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

The pipeline uses three different names for the same veraPDF gate across four
files, causing the verdict writer to read a different gate than the orchestrator
produced. This must be fixed before any verdict logic is touched. The canonical
names are:

| gate | canonical key | produced by | consumed by |
|---|---|---|---|
| veraPDF PDF/UA-1 post-repair | `verapdf_pdfua1_post` | `run_verapdf_profiles.sh` → `remediate.py` | `verdict()`, `status_json_writer.py` |
| veraPDF WCAG post-repair | `verapdf_wcag_post` | same | same |
| veraPDF PDF/UA-1 baseline | `verapdf_pdfua1_baseline` | same | status writer (informational only) |
| metadata parity post-repair | `metadata_parity_post` | `fix_metadata_xmp_parity.py` result + `metadata_xmp_parity_audit.py` | verdict, writer |
| preservation post-repair | `preservation_post` | `preservation_audit.py` | verdict, writer |
| qpdf | `qpdf` | `run_qpdf_check.sh` | verdict, writer |
| table semantics | `table_semantics` | `table_semantics_audit.py` | verdict, writer |

Experimental/non-standard profile results (ISO-32000-1, PDF/UA-2) are NOT gates.
They are collected as `experimental_flags[]` in the output and raise a job-level
`pending_review` flag but never drive FAIL.

### TASK

1. Update `run_verapdf_profiles.sh` so its output JSON uses `verapdf_pdfua1_post`
   and `verapdf_wcag_post` as the primary result keys, and collects
   ISO/PDF-UA-2 results into `experimental_flags[]` rather than folding them
   into the top-level `result`.
2. Update `package_scaffold.py`'s STATUS.json stub to use the canonical key names
   above (replacing `verapdf_pdfua1` / `verapdf_wcag` stub keys).
3. Update the Phase 6 and Phase 8 sections of `remediate.py` to read and emit
   the canonical key names.
4. Update `status_json_writer.py`'s `gate_files` map to use canonical keys.
   Remove `EXCLUDE_FROM_OVERALL` entries for these gates — exclusion logic moves
   to `verdict()` in Template 1-B.

Do not implement `verdict()` yet — that is Template 1-B. This task only
normalizes names so all files agree on what to call each gate.

Current file contents:
[INSERT EACH FILE'S CONTENTS HERE, CLEARLY LABELLED]

### GATE — must pass before moving on

1. **No name appears in more than one form:** `grep -r "verapdf_pdfua[^1]" tools/` returns nothing. `grep -r "verapdf_wcag[^_]" tools/` returns nothing.
2. **MM-TEST2 regression:** run the full pipeline on MM-TEST2. Overall verdict must be identical to pre-change baseline. Paste the before/after STATUS.json `overall_result` fields.
3. **Experimental flags collected:** run on any document that trips ISO-32000-1. Confirm `experimental_flags` is non-empty in the veraPDF output JSON and does NOT appear in the top-level `result`.
4. **Git diff scope:** changes are confined to the four files listed. No other files changed.

---

## Template 1-A — `run_verapdf_profiles.sh` + `preservation_audit.py`

**Reasoning mode:** Think High
**Files needed:** `tools/audit/run_verapdf_profiles.sh`,
`tools/qa/preservation_audit.py`, current MM-TEST2 baseline outputs for both.
**Prerequisite:** Template 0-B gate passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

**`run_verapdf_profiles.sh`:** Already updated in 0-B for canonical names and
experimental flag separation. In this task, add per-profile result reporting so
downstream consumers can see which specific profile failed, not just the
aggregate. The top-level `result` keys on PDF/UA-1 + WCAG only.

**`preservation_audit.py`:** Currently does an exact `==` comparison on word
lists. This fails on any document that went through `fix_untagged_pdf.py`
(which rewrites all content streams, changing tokenization). The fix:
- Add a tolerance band: word-count delta within 0.5% of baseline → still `PASS`.
- Redefine `REVIEW` vs `FAIL`: `REVIEW` = word count within tolerance but order
  changed; `FAIL` = word count delta exceeds tolerance (actual content loss).
- A job that went through the untagged-PDF rebuild path (`alt_branch` in state)
  should treat order mismatch as expected, not as `REVIEW`.

**PARTIAL policy (locked #2):** `preservation_audit.py` exit 1 (REVIEW) must
advance the repair chain in the orchestrator — it is advisory, not blocking.
This template does not touch `remediate.py`; note it as a dependency for 1-C.

### TASK

1. Add per-profile output to `run_verapdf_profiles.sh` — each profile gets its
   own result entry; aggregate `result` keys on PDF/UA-1 + WCAG.
2. Add 0.5% tolerance band to `preservation_audit.py`.
3. Redefine REVIEW/FAIL thresholds as specified above.
4. Accept an optional `--rebuilt` flag that suppresses order-mismatch REVIEW
   for documents that went through the full rebuild path.

Current file contents:
[INSERT BOTH FILES HERE]

### GATE — must pass before moving on

1. **MM-TEST2 regression:** preservation audit on MM-TEST2 (no rebuild) must
   return same result as baseline. Paste before/after.
2. **Tolerance test:** construct a word list with a 0.4% count delta. `preservation_audit.py` returns PASS.
3. **Threshold test:** construct a word list with a 0.6% count delta. Returns FAIL.
4. **Order test (without --rebuilt):** same count, different order → REVIEW.
5. **Order test (with --rebuilt):** same count, different order → PASS.
6. **Per-profile output:** run veraPDF on a test PDF, confirm the output JSON
   has per-profile entries and `result` does not include ISO/PDF-UA-2.

---

## Template 1-B — `verdict()` module + `status_json_writer.py` rewrite

**Reasoning mode:** Think Max
**Files needed:** `tools/packaging/status_json_writer.py`,
`tools/packaging/package_deliverables.py`
**Prerequisite:** Templates 0-B and 1-A gates passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

**Verdict ownership (locked #5):** a shared pure function `verdict()` in
`tools/lib/verdict.py` is the single source of truth. Both `remediate.py` and
`status_json_writer.py` import and call it. It takes one input: a consolidated
verdict-input bundle (a dict/JSON object). It returns exactly one of:
`'PASS'`, `'REVIEW_REQUIRED'`, `'FAIL'`.

The verdict logic:
- **FAIL** if any rule has outcome `persistent` with caps exhausted, OR any
  `never_attempted`/`introduced` that survived the AI phase, OR any `escalated`.
- **REVIEW_REQUIRED** if no FAIL conditions AND any of: a rule has
  `pending_review: true` (repairable_review fix applied, awaiting sign-off); OR
  `experimental_flags` is non-empty; OR any QA gate returns non-PASS
  (`preservation_post`, `render_compare`, `visual_qa`) AND is configured as
  advisory (not blocking) — see advisory_gates list below.
- **PASS** otherwise.

Advisory QA gates (non-blocking — contribute to REVIEW_REQUIRED not FAIL):
`preservation_post`, `render_compare`, `visual_qa`.
Blocking gates (failure → FAIL): `qpdf`, `metadata_parity_post`.
veraPDF gates: `verapdf_pdfua1_post` FAIL → FAIL; `verapdf_wcag_post` FAIL →
REVIEW_REQUIRED (WCAG is advisory).

The consolidated verdict-input bundle schema:
```json
{
  "residual_analysis": { ... },
  "gate_results": {
    "verapdf_pdfua1_post": "PASS|FAIL",
    "verapdf_wcag_post": "PASS|FAIL",
    "metadata_parity_post": "PASS|FAIL",
    "preservation_post": "PASS|REVIEW|FAIL",
    "qpdf": "PASS|FAIL",
    "render_compare": "PASS|REVIEW|FAIL",
    "visual_qa": "PASS|REVIEW|FAIL",
    "table_semantics": "PASS|FAIL"
  },
  "experimental_flags": [],
  "pending_review_rules": []
}
```

`status_json_writer.py` is rewritten to: (1) load the consolidated bundle from
`JOB/audit/verdict_inputs.json`, (2) call `verdict()`, (3) serialize result to
STATUS.json. It no longer globs directories or computes anything independently.

`package_deliverables.py` routes output by verdict: PASS → top-level output dir;
REVIEW_REQUIRED → `output/{ticket}_remediated/review/`; FAIL → `output/{ticket}_remediated/failed/`. Also adds `--skip-pdf` flag: when set, copies
only audit reports and STATUS.json (no remediated PDF), for FAIL jobs where the
PDF shouldn't be promoted.

### TASK

1. Create `tools/lib/__init__.py` (empty) and `tools/lib/verdict.py` with the
   `verdict(bundle: dict) -> str` function per the logic above.
2. Rewrite `tools/packaging/status_json_writer.py` to load
   `JOB/audit/verdict_inputs.json` and call `verdict()`.
3. Update `tools/packaging/package_deliverables.py` with routing + `--skip-pdf`.
4. Do NOT touch `remediate.py` yet — it will be updated in Template 1-C to
   produce `verdict_inputs.json`.

Current file contents:
[INSERT status_json_writer.py AND package_deliverables.py HERE]

### GATE — must pass before moving on

1. **Unit tests for `verdict()`** — write and run these before anything else:
   - All-resolved, no flags → PASS
   - Any persistent (caps hit) → FAIL
   - Any experimental_flag, no FAIL conditions → REVIEW_REQUIRED
   - WCAG FAIL only (no PDF/UA-1 FAIL) → REVIEW_REQUIRED
   - PDF/UA-1 FAIL → FAIL
   - qpdf FAIL → FAIL
   - preservation_post REVIEW → REVIEW_REQUIRED (advisory)
   - pending_review_rules non-empty → REVIEW_REQUIRED
   Paste unit test output.
2. **Recomputability test:** take a completed MM-TEST2 job dir, run
   `status_json_writer.py` on it, confirm STATUS.json matches the orchestrator's
   exit-code verdict. Paste comparison.
3. **Routing test:** manually set `overall_result` to each of PASS / REVIEW_REQUIRED / FAIL in STATUS.json; run `package_deliverables.py`; confirm files land in the correct subdirectory for each.
4. **`--skip-pdf` test:** run with `--skip-pdf` on a FAIL job; confirm no `_remediated.pdf` is copied.
5. **MM-TEST2 regression:** full pipeline run; overall verdict unchanged.

---

## Template 1-C — `remediate.py` M1 changes

**Reasoning mode:** Think Max
**Files needed:** `tools/orchestrate/remediate.py` (full),
`tools/lib/verdict.py` (from 1-B)
**Prerequisite:** Templates 0-B, 1-A, 1-B gates all passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

`remediate.py` needs three targeted changes in this milestone. Do not make any
other changes — M3 and M4 will add significant new logic to this file and those
changes must not be anticipated here.

**Change 1 — produce `verdict_inputs.json`:** After Phase 6 (post-repair
veraPDF), assemble the consolidated verdict-input bundle from the gate results
already collected in the Phase 6 flow, and write it to
`JOB/audit/verdict_inputs.json`. Import and call `verdict()` from
`tools/lib/verdict.py` to compute `overall`. Use this result for the exit code.

**Change 2 — PARTIAL advances the chain (locked #2):** In the repair loop
(Phase 5), a script that returns a non-PASS_CODES result but produces an output
file must still advance `current_pdf` to that output. The result string goes to
the deviation log as advisory; it does not stall the chain. Add `PARTIAL` and
`NEEDS_REVIEW` to a new `ADVANCE_CODES` set that is checked for file existence
before deciding whether to advance.

**Change 3 — preservation_audit `--rebuilt` flag:** Pass `--rebuilt` to
`preservation_audit.py` when `alt_branch` is True (the untagged-PDF rebuild
path was taken). This is wired here because `remediate.py` knows `alt_branch`;
the audit script doesn't.

### TASK

Make exactly these three changes to `remediate.py`. Mark each change with a
`# M1:` comment so they are findable in review.

Current file contents:
[INSERT remediate.py FULL CONTENTS HERE]

### GATE — must pass before moving on

1. **MM-TEST2 full pipeline:** run end to end. Verdict unchanged. `verdict_inputs.json` exists in the job's audit dir after the run. Paste its contents.
2. **PARTIAL advance test:** instrument a repair script to return exit 1 with result `PARTIAL` but still write a valid output PDF. Confirm `current_pdf` advances to that output in the next step.
3. **`--rebuilt` pass-through:** run on a document that triggers `fix_untagged_pdf.py`. Confirm `preservation_audit.py` is called with `--rebuilt`. Check orchestrator logs.
4. **No scope creep:** `git diff tools/orchestrate/remediate.py` shows only the three M1-tagged changes. No other logic altered.

---

## Template 2-A — Repair/audit taxonomy fix

**Reasoning mode:** Think High
**Files needed:** `tools/audit/rule_repair_map.json`,
`tools/repair/fix_notdef_glyphs.py`, `tools/repair/fix_contrast_color_runs.py`,
`tools/repair/font_replacement_report.py`,
`tools/orchestrate/remediate.py` (repair loop section only)
**Prerequisite:** M1 complete (all gates passed).

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

Three scripts in `tools/repair/` are actually detectors — they write no output
PDF and the repo's own README marks them "(audit only)". They must be relocated.

`fix_table_tagging.py` and `fix_parent_tree_mcids.py` are unmapped orphans (no
rule in the map points to them). `fix_table_tagging.py` is the only script that
builds table structure; it needs to be wired. `fix_parent_tree_mcids.py` is a
partial detector — treat as audit-only pending a real repair being written.

PARTIAL policy (locked #2): repair loop must advance `current_pdf` when a script
returns PARTIAL and produced an output file. This was implemented in Template 1-C;
do not re-implement, just verify it covers `fix_table_tagging.py`'s PARTIAL case.

### TASK

1. Move `fix_notdef_glyphs.py`, `fix_contrast_color_runs.py`, and
   `font_replacement_report.py` from `tools/repair/` to `tools/audit/`.
   Update any import or path references in `remediate.py`.
2. Remove `fix_notdef_glyphs.py` and `fix_contrast_color_runs.py` from
   `rule_repair_map.json` `repair_script` fields. Their `resolvability` is
   already `detector_mislabeled` from Template 0-A; update to `repairable_review`
   now that the detector confusion is resolved (they will get real repairs in M3).
3. Wire `fix_table_tagging.py` into `rule_repair_map.json` for table-structure
   failures (rules 7.5.2, or the appropriate table-structure rule IDs confirmed
   by `table_semantics_audit.py` output). Set `repair_order: 3` (after struct
   marking, before alt text), `confidence: EXPECTED`.
4. Delete `tools/orchestrate/fix_metadata_xmp_parity.py` (dead duplicate).
5. Fix Branch B's `generate_alt_text_review_report.py` invocation in
   `remediate.py` — current call uses wrong positional signature. Correct it to
   match the script's actual argparse (`<pdf> --draft X --out Y --map-out Z`).

Current file contents:
[INSERT ALL LISTED FILES HERE]

### GATE — must pass before moving on

1. **No broken paths:** `python3 -c "import json; m=json.load(open('tools/audit/rule_repair_map.json')); [open('tools/repair/'+e['repair_script']) for e in m.values() if e.get('repair_script')]"` — or equivalent path check — exits 0.
2. **Relocated scripts importable from new location:** `python3 tools/audit/fix_notdef_glyphs.py --help` (or equivalent invocation) exits without ImportError.
3. **MM-TEST2 regression:** full pipeline unchanged.
4. **Branch B review report:** run on a document with figures. Confirm `generate_alt_text_review_report.py` is called with the correct signature and the review HTML is actually produced. Paste the call log line.
5. **No dead duplicate:** `ls tools/orchestrate/fix_metadata_xmp_parity.py` returns "No such file."

---

## Template 3-A — `execution_log.json` (shared M3/M4 foundation)

**Reasoning mode:** Think Max
**Files needed:** `tools/orchestrate/remediate.py` (repair loop + Phase 6),
`tools/lib/verdict.py`
**Prerequisite:** M1 complete, M2 complete.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

`execution_log.json` is written by `remediate.py` (the orchestrator owns it —
locked #5) and serves two consumers: (1) the residual analyzer (M3), and (2)
the M4 resume state. Design it once for both.

Schema:
```json
{
  "job_id": "string",
  "source_pdf": "path",
  "alt_branch": true,
  "current_pdf_final": "path to the last good pass file",
  "steps": [
    {
      "step_index": 0,
      "script": "tools/repair/fix_untagged_pdf.py",
      "rules_addressed": ["PDF/UA-1/7.1-untagged"],
      "args": ["<input>", "<output>"],
      "exit_code": 0,
      "result_string": "FIXED",
      "produced_output": true,
      "output_path": "path/to/pass1.pdf",
      "started_at": "ISO-8601",
      "completed_at": "ISO-8601"
    }
  ],
  "phases_completed": ["phase0", "phase1", "phase2", "phase3", "phase4",
                       "phase5", "phase6"],
  "residual_computed": false,
  "ai_phase_triggered": false,
  "attempt_counts": {}
}
```

`produced_output` is true iff the output file exists on disk after the script
ran — checked by `os.path.exists(output_path)`, not by trusting the exit code.
`result_string` is whatever the script printed (FIXED, PARTIAL, NEEDS_REVIEW,
FAIL, etc.) — recorded verbatim, not interpreted.

The log is written incrementally: opened at Phase 0, appended after each repair
step, closed/finalized after Phase 6. This means a crashed job leaves a partial
log that M4's `--resume` can read to determine where to restart.

### TASK

1. Add incremental `execution_log.json` emission to `remediate.py` covering:
   - initialization at Phase 0 start (job_id, source_pdf, alt_branch)
   - append after each repair step (one entry per step)
   - update `current_pdf_final` after each step that produces output
   - update `phases_completed` after each phase exits
   - finalize `residual_computed` / `ai_phase_triggered` at Phase 6/7 boundary
2. The log is written to `JOB/audit/execution_log.json`.
3. Do not implement `--resume` yet — that is M4. Do not implement the residual
   analyzer yet — that is Template 3-B.

Current file contents:
[INSERT remediate.py FULL CONTENTS HERE]

### GATE — must pass before moving on

1. **Log produced:** run on MM-TEST2. `JOB/audit/execution_log.json` exists.
   Paste its contents.
2. **Step count correct:** number of entries in `steps[]` matches the number of
   repair scripts the plan selected. Verify manually against the plan output.
3. **`produced_output` accurate:** for each step, confirm `produced_output`
   matches whether the output file actually exists. Specifically: for any step
   that exited non-zero, does `produced_output` correctly reflect whether a file
   was written?
4. **Incremental write:** kill the process mid-run (after at least one step).
   Confirm a partial `execution_log.json` exists and is valid JSON with the
   completed steps recorded.
5. **MM-TEST2 regression:** overall verdict unchanged.

---

## Template 3-B — Residual analyzer

**Reasoning mode:** Think Max
**Files needed:** `tools/audit/rule_repair_map.json`,
`tools/audit/parse_verapdf_summary.py`,
`tools/lib/verdict.py`
**Prerequisite:** Template 3-A gate passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

The residual analyzer is a new script `tools/audit/residual_analyzer.py`. It
reads `execution_log.json`, `failures.json` (baseline), `failures_post.json`
(post-repair), and `rule_repair_map.json`, and produces
`JOB/audit/residual_analysis.json` and the consolidated `verdict_inputs.json`
(which `verdict()` reads and `status_json_writer.py` consumes).

**Outcome states (one per rule, locked):**

| outcome | condition |
|---|---|
| `resolved` | in_baseline, not in_residual, effective_repair_ran |
| `resolved_incidental` | in_baseline, not in_residual, no repair targeted it |
| `persistent` | in_baseline, in_residual, effective_repair_ran |
| `partially_resolved` | sub-state of persistent: post_count < baseline_count |
| `attempted_no_effect` | in_baseline, in_residual, repair ran but was a detector (produced_output=false) |
| `introduced` | not in_baseline, in_residual |
| `never_attempted` | in_baseline, in_residual, no repair targeted it OR resolvability is repairable_unbuilt/repairable_review with no script |
| `escalated` | in_baseline, in_residual, resolvability=not_auto_fixable |

**`effective_repair_ran`** = a step in `execution_log.steps[]` whose
`rules_addressed` includes this rule_id AND `produced_output == true`. Exit code
and result_string are NOT considered — only whether the step ran and produced a
file (locked #2, validator wins).

**Count fields:** every rule outcome records `baseline_count` and `post_count`
(failure instance counts from the veraPDF summary, not the outcome states).
`partially_resolved` is when `post_count < baseline_count` and rule is still
in residual.

**`pending_review`** flag: attached to `resolved` outcomes where the rule's
`resolvability` is `repairable_review` (fix was applied, clears veraPDF, but
a human must sign off on the choice).

**Experimental flags:** collected from the veraPDF post-repair run's
`experimental_flags[]` field (produced by the updated `run_verapdf_profiles.sh`).
Written into `verdict_inputs.json` as-is.

After computing the residual, the analyzer assembles and writes
`JOB/audit/verdict_inputs.json` (the consolidated bundle `verdict()` reads).
It then calls `verdict()` and writes the result to `verdict_inputs.json` as
`computed_verdict`. `remediate.py` reads this for its exit code.

**Discovery-mode validation (two testing modes — locked):**
When `execution_log.ai_phase_triggered == true`, the analyzer also reads
per-step isolation snapshots from `JOB/audit/discovery/` (written by the AI
phase, one before/after veraPDF pair per new script). These are used to compute
`clean`, `introduced_rules`, and `regressed_rules` per new script.
In this template, implement the non-discovery path only. The discovery path
(reading isolation snapshots) is added in Template 3-D.

### TASK

1. Write `tools/audit/residual_analyzer.py` per the above.
2. Update `remediate.py` to call the analyzer after Phase 6, passing the job dir.
   The analyzer replaces the current inline `overall` computation — remove the
   inline logic and use `computed_verdict` from `verdict_inputs.json` instead.

Current file contents:
[INSERT rule_repair_map.json, parse_verapdf_summary.py, verdict.py HERE]
[INSERT the current Phase 6-8 section of remediate.py HERE]

### GATE — must pass before moving on

1. **MM-TEST2 full run:** `residual_analysis.json` and `verdict_inputs.json`
   exist. Paste both files.
2. **Outcome correctness:** for each rule in `residual_analysis.json`, manually
   verify the assigned outcome matches the expected state given what the MM-TEST2
   run actually did. Specifically check: at least one `resolved` rule, zero
   `introduced` rules (MM-TEST2 should be clean), zero `never_attempted` rules.
3. **Count fields present:** every outcome has `baseline_count` and `post_count`.
4. **Verdict matches:** `computed_verdict` in `verdict_inputs.json` matches
   `remediate.py`'s exit-code verdict (0=PASS, 1=FAIL, 2=REVIEW). Paste
   comparison.
5. **MM-TEST3 partial run (no AI phase):** run on MM-TEST3 stopping before any
   AI phase. Confirm the 4 OPENCLAW rules appear as `never_attempted` in the
   residual. Paste the relevant entries.

---

## Template 3-C — `learned_strategies.json` + `post_job_indexer.py` rewrite

**Reasoning mode:** Think Max
**Files needed:** `tools/audit/post_job_indexer.py`,
`tools/audit/rule_repair_map.json`, `tools/audit/residual_analyzer.py`
**Prerequisite:** Template 3-B gate passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

**`learned_strategies.json`** is written by the AI phase (OpenClaw) when it
creates and proves a new repair script. Schema:
```json
{
  "job_id": "string",
  "generated_at": "ISO-8601",
  "strategies": [
    {
      "rule_id": "PDF/UA-1/7.21.7",
      "script_path": "tools/repair/fix_tounicode_map.py",
      "args_pattern": "<input.pdf> <output.pdf>",
      "repair_order": 4,
      "run_last": false,
      "proposed_resolvability": "effective",
      "outcome": "resolved",
      "pre_count": 30,
      "post_count": 0,
      "attributable": true,
      "clean": true,
      "introduced_rules": [],
      "regressed_rules": [],
      "isolation_snapshot": "audit/discovery/7.21.7_pre_post.json",
      "notes": "string"
    }
  ]
}
```

**Indexer gate (locked #8):** a strategy is indexed only if `clean == true`.
`clean == false` strategies are logged as `rejected_experiments[]` on the target
rule — NOT added as repairs. A `repairable_review` rule stays `repairable_review`
after promotion — never auto-promoted to `effective`.

**Confidence policy:** new entries start at `EXPECTED`. Promoted to `CONFIRMED`
after `confirmed_jobs >= 3`. Only `clean == true` jobs count toward the threshold.

**`introduced_by[]`:** when a known repair produces `introduced` outcomes in the
residual, the indexer logs the introduced rule_ids against the repair script in a
new `introduced_by` annotation on that script's map entry. Accumulated over jobs.

### TASK

1. Define `JOB/audit/learned_strategies.json` as a readable/writable contract
   (write a JSON schema comment block at the top of the indexer).
2. Rewrite `tools/audit/post_job_indexer.py` to:
   - Consume `learned_strategies.json` + `residual_analysis.json`
   - Gate on `clean == true` before indexing
   - Log `clean == false` as `rejected_experiments[]`
   - Maintain `confirmed_jobs` counter, promote at threshold 3
   - Never promote `repairable_review` to `effective`
   - Accumulate `introduced_by[]` from residual `introduced` outcomes
3. Do NOT write the `learned_strategies.json` producer yet — that is the AI
   phase / OpenClaw's responsibility and is documented in a separate prompt.

Current file contents:
[INSERT post_job_indexer.py AND rule_repair_map.json HERE]

### GATE — must pass before moving on

1. **Dry-run on MM-TEST2:** run indexer. No new entries added (no AI phase ran).
   Map unchanged. Exit 0. Paste log output.
2. **Clean-strategy test:** construct a synthetic `learned_strategies.json` with
   one `clean:true` strategy for a `repairable_unbuilt` rule. Run indexer.
   Confirm the rule is now in the map with `confidence:EXPECTED` and
   `repair_script` set. Paste the new map entry.
3. **Dirty-strategy test:** construct a `learned_strategies.json` with one
   `clean:false` strategy. Run indexer. Confirm the rule's `rejected_experiments`
   array now has one entry and NO `repair_script` was set. Paste.
4. **Promotion test:** run three jobs each contributing a `clean:true` strategy
   for the same rule. Confirm `confidence` promotes from EXPECTED to CONFIRMED
   after the third. Paste map entry after each run.
5. **Review-rule no-promote test:** construct a `clean:true` strategy with
   `proposed_resolvability: repairable_review`. Confirm the map entry keeps
   `repairable_review` and is not promoted to `effective`.
6. **`introduced_by` test:** construct a residual with one `introduced` rule
   attributed to `fix_untagged_pdf.py`. Confirm the map entry for that script
   gains an `introduced_by` entry. Paste.

---

## Template 3-D — Discovery-mode validation (AI phase isolation snapshots)

**Reasoning mode:** Think Max
**Files needed:** `tools/audit/residual_analyzer.py`,
`tools/orchestrate/remediate.py` (Phase 7 / AI trigger section)
**Prerequisite:** Template 3-C gate passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

**Two testing modes (load-bearing principle — must not be optimized away):**

Execution mode (known/mapped repairs): single post-repair veraPDF run.
Minimize calls. Trust the plan.

Discovery mode (AI building a NEW script): per-step isolated before/after
veraPDF snapshot around each new script. Cost is not a concern. Correctness is.
This is how `clean`, `introduced_rules`, and `regressed_rules` are attributed
to a specific new script with certainty rather than inference.

**The exit-3 trigger:** after the residual analyzer runs, if `never_attempted`
or `introduced` rules remain, `remediate.py` writes
`JOB/audit/openclaw_signals.json` (one entry per rule: rule_id, description,
resolvability, baseline_count, post_count), emits them as signals, and exits 3.

**After OpenClaw writes a new script and calls `--resume`:** the resume path
(M4) runs the new script in isolation, taking a veraPDF snapshot before and
after. Each snapshot is written to `JOB/audit/discovery/{rule_id}_pre_post.json`.
The residual analyzer's discovery path reads these snapshots to compute `clean`
per new script.

### TASK

1. Add the discovery-path branch to `residual_analyzer.py`: when
   `execution_log.ai_phase_triggered == true`, read isolation snapshots from
   `JOB/audit/discovery/` and compute `clean`/`introduced_rules`/
   `regressed_rules` per strategy entry, then write updated
   `learned_strategies.json` fields.
2. Add the exit-3 trigger to `remediate.py`: after the residual analyzer runs,
   check for `never_attempted`/`introduced` outcomes; if found, write
   `openclaw_signals.json` and exit 3.
3. Update `AGENTS.md` step 5: when orchestrator exits 3, OpenClaw reads
   `openclaw_signals.json`, writes repair scripts for each signal, registers them
   in `rule_repair_map.json`, runs each in isolation (before/after veraPDF
   snapshot to `audit/discovery/`), writes `learned_strategies.json`, then
   re-runs orchestrator with `--resume`. Include the termination guard: if the
   same rule has appeared in `rejected_experiments[]` N times (cap = 3), escalate
   rather than attempting again.

Current file contents:
[INSERT residual_analyzer.py AND the Phase 7 section of remediate.py HERE]
[INSERT AGENTS.md CURRENT CONTENTS HERE]

### GATE — must pass before moving on

1. **Exit-3 test:** run on MM-TEST3 (which has 4 `never_attempted` rules).
   Confirm exit code is 3. Confirm `openclaw_signals.json` exists with 4 entries.
   Paste the file.
2. **Snapshot attribution test:** construct two synthetic isolation snapshots
   (one for a new script that cleanly resolves, one that introduces a new rule).
   Run the analyzer discovery path. Confirm `clean:true` for the first and
   `clean:false` + `introduced_rules` populated for the second.
3. **AGENTS.md coverage:** read the updated step 5. Confirm it covers: read
   signals → write scripts → run in isolation → write snapshots → write
   learned_strategies → re-run with --resume → termination guard.
4. **MM-TEST2 regression:** full pipeline unchanged.

---

## Template 4-A — Resume / state (`--resume` flag + Phase 0 idempotency)

**Reasoning mode:** Think Max
**Files needed:** `tools/orchestrate/remediate.py` (full),
`tools/packaging/package_scaffold.py`,
`tools/audit/execution_log.json` (example from a completed job)
**Prerequisite:** Templates 3-A through 3-D gates all passed.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT — Locked decisions

**Phase 0 idempotency:** `package_scaffold.py` is called unconditionally and
overwrites STATUS.json with an IN_PROGRESS stub. On `--resume`, scaffold must
be skipped OR made to preserve an existing STATUS.json/execution_log.json.
Preferred: skip scaffold on `--resume` entirely (simpler).

**`--resume` behavior:**
1. Load `JOB/audit/execution_log.json`.
2. Set `current_pdf = execution_log.current_pdf_final`.
3. Re-derive the repair plan from the *residual* failure set (in
   `failures_post.json`) + the current (now-updated) `rule_repair_map.json`.
   This picks up newly-registered AI scripts automatically.
4. Run the repair loop on the residual plan only (skip rules already `resolved`
   in the residual analysis).
5. Continue to Phase 6 (post-repair veraPDF), residual analyzer, verdict.

**Attempt caps:** per-rule cap = 5 AI attempts; per-job cap = 15 AI attempts
total. Tracked in `execution_log.attempt_counts[rule_id]`. Cap exceeded →
rule becomes `escalated` in the residual, verdict reflects that honestly.

**Termination guard:** if exit-3 would fire but every remaining
`never_attempted` rule has either hit its cap or appears in
`rejected_experiments[]` N >= 3 times, exit 1 (FAIL) instead of 3. No infinite
loop.

### TASK

1. Add `--resume` flag to `remediate.py` argparse.
2. Implement resume behavior as described above.
3. Make Phase 0 skip `package_scaffold.py` when `--resume` is set.
4. Add `attempt_counts` tracking to `execution_log.json` emission (Template 3-A
   laid the groundwork; extend it).
5. Implement the per-rule and per-job cap logic, checked before exit-3 fires.
6. Update `openclaw.json` with the correct `--resume` invocation pattern.

Current file contents:
[INSERT remediate.py, package_scaffold.py, openclaw.json HERE]

### GATE — must pass before moving on

1. **Basic resume test:** run a job on MM-TEST2, interrupt it mid-repair (kill
   after Phase 3), re-run with `--resume`. Confirm it picks up at the correct
   `current_pdf` and completes successfully. Paste before/after STATUS.json.
2. **Plan re-derivation test:** after adding a synthetic new strategy to the map
   (as if the AI phase registered it), run `--resume` on a job with a residual.
   Confirm the new strategy is picked up in the resumed plan.
3. **Cap test:** set cap to 2 for testing; construct a rule that never clears.
   Run two resume cycles. Confirm exit-3 does NOT fire on the third cycle and
   instead the rule is `escalated` in the residual.
4. **Termination guard test:** confirm that a job where all remaining
   `never_attempted` rules are either capped or in `rejected_experiments[]`
   exits 1 (FAIL) rather than 3 (waiting for AI).
5. **Idempotency test:** run `--resume` twice on a completed job. Confirm the
   second run detects "nothing to do" and exits 0 cleanly without overwriting
   artifacts.
6. **MM-TEST2 regression:** full pipeline (non-resume) unchanged.

---

## Template 5-A — Hygiene sweep

**Reasoning mode:** Non-think / Think High
**Files needed:** all files with pending hygiene items (see list below)
**Prerequisite:** M1-M4 complete.

---

### SYSTEM PROMPT

[same as Template 0-A]

### CONTEXT

Mechanical cleanup items — no architectural decisions involved. Each is
independent; they can be done in any order or batched.

### TASK (checklist — complete all)

- [ ] Delete `tools/orchestrate/fix_metadata_xmp_parity.py` (dead duplicate).
- [ ] Standardize stdout to one JSON object in: `fix_struct_content_marking.py`
      (currently prints prose), `fix_list_numbering.py` (currently prints
      `[init]` lines). Schema: `{"result": "FIXED|PARTIAL|FAIL", "notes": "..."}`.
- [ ] Normalize `fix_cidset.py` `repair_order` — both rule variants (7.21.4.2
      and 7.21.4.2-1) should be `repair_order: 9` (after all PyMuPDF saves).
      Currently one is 4, one is 9.
- [ ] Update `tools/README.md` — currently a verbatim copy of
      `tools/repair/README.md`. Rewrite to cover all subdirs: audit, repair, qa,
      packaging, orchestrate, lib.
- [ ] Correct `tools/repair/README.md` — states `fix_table_headers.py` writes a
      Summary attribute; the code does not. Remove that claim.
- [ ] Deduplicate sha256 logic — `package_deliverables.py` reimplements it
      inline; replace with an import of `checksums.py`'s function.
- [ ] numpy pixel diff in `render_compare.py` and `visual_qa.py` — replace
      the pure-Python `sum(...)` loops with `np.count_nonzero(np.abs(a-b)>10)`.
- [ ] Correct `AGENTS.md` visual_qa model-routing claim — currently says
      "switch to VISION_MODEL before calling visual_qa.py" implying vision-model
      inspection. `visual_qa.py` does no vision call; it is heuristic-only.
      Correct the routing table.

Current file contents:
[INSERT EACH AFFECTED FILE HERE AS NEEDED]

### GATE — must pass before moving on

1. **Dead file gone:** `ls tools/orchestrate/fix_metadata_xmp_parity.py` → not found.
2. **JSON stdout test:** call each standardized script on a test PDF. Parse stdout
   as JSON. Confirm valid JSON with `result` field.
3. **cidset order:** `grep repair_order tools/audit/rule_repair_map.json` — both
   cidset entries show 9.
4. **numpy diff:** `python3 -c "import render_compare"` does not import `sum`;
   confirm `np` is used. Run on a page pair; timing should be <1s per page.
5. **MM-TEST2 regression:** full pipeline unchanged after all hygiene changes.

---

## Notes for OpenClaw / session handoff

**At the start of every session:**
1. Fetch the relevant files fresh from GitHub master (never use cached versions).
2. Read the relevant template in full before writing any code.
3. Confirm the prerequisite gate for this template was passed (paste its output).

**At the end of every session:**
- Commit all changes to master with a message referencing the template number.
- Paste the gate output into a session summary (can use the ORCHESTRATOR_REVIEW
  format — add a "Sessions" section at the bottom).
- If any gate test revealed an unexpected issue, document it before stopping —
  don't leave it implicit in the code.

**When the AI phase (OpenClaw) writes a new repair script for a `never_attempted`
or `introduced` rule:**
- The script must live in `tools/repair/fix_{rule_slug}.py`.
- It must accept `<input.pdf> <output.pdf>` as positional args.
- It must print exactly one JSON object to stdout: `{"result": "FIXED|PARTIAL|FAIL", "notes": "..."}`.
- It must exit 0 on FIXED/PARTIAL, non-zero on FAIL.
- Before registering it, run the isolation veraPDF snapshot (before/after).
- Write `learned_strategies.json` with the result, including `clean` determined
  from the snapshot.
- Only then register in `rule_repair_map.json` and call orchestrator with
  `--resume`.
