# Montefiore PDF/UA Orchestrator — Architectural Review & Remediation Plan

**Status:** working document, intended to span multiple sessions
**Scope of this review:** `tools/repair` (all), `tools/audit` decision logic +
indexer, `tools/qa` (all three), `tools/packaging` verdict + deliverables,
`tools/orchestrate`, plus `AGENTS.md` and `openclaw.json`, read from `master`.
**Method:** every file fetched from GitHub raw and read in full. Claims here
are grounded in the committed source, not the session-handoff summary.

**Read/unread inventory (coverage now complete):**
- *Read in full — entire `tools/` tree:* `remediate.py`; `AGENTS.md`;
  `openclaw.json`; all `tools/audit/*` (lookup_repair_plan, parse_verapdf_summary,
  rule_repair_map, post_job_indexer, detect_image_only_pages*, metadata_xmp_parity_audit,
  table_semantics_audit, contrast_audit, font_inventory, font_geometry_matcher,
  run_verapdf_profiles.sh, run_qpdf_check.sh, doc_taxonomy.json); all
  `tools/repair/*` (14 scripts incl. both alt-text generators + font_replacement_report);
  all `tools/qa/*` (preservation, render_compare, visual_qa); all
  `tools/packaging/*` (status_json_writer, package_deliverables, package_scaffold,
  checksums, cleanup_job); `tools/orchestrate/*`; both README.md files.
  (*detect_image_only_pages.py inferred from references — the one file the fetch
  gate never released; its contract is visible via remediate.py's call + the map.*)
- *Not code:* `audit/alt_map_approved.json` returns 404 (generated artifact, not
  committed). `skills/` tree out of scope for this review.

> **Read this first — the handoff and `master` disagree.**
> Several fixes the previous session's handoff lists as "completed" are **not
> present on `master`.** Either they were never committed, were committed to a
> different branch, or were reverted. Before doing *any* new work, confirm which
> branch is actually deployed in the container. The whole plan below assumes
> `master` as read; if the deployed branch differs, re-baseline first.
>
> Specifically not found on `master` (claimed done in handoff):
> - **P1.4** — `verapdf_pdfua` is *still* in `EXCLUDE_FROM_OVERALL` in
>   `status_json_writer.py`. (Handoff: "removed.")
> - **P1.3** — `package_deliverables.py` has **no `--skip-pdf` flag** and no
>   FAIL/REVIEW routing to `failed/` or `review/`.
> - **preservation_audit.py** — no 0.5% tolerance band, no REVIEW-logic fix;
>   it does an exact `==` on word lists.
> - **P1.2** — `fix_table_tagging.py` ParentTree uses `Integer(0)` as the empty
>   fill value (`pikepdf.Array([Integer(0)] * (max_mcid + 1))`), not `Null`.
>   (Whether this is the "fixed" or "unfixed" state is ambiguous from the
>   handoff wording — verify intent.)
> - **OPENCLAW_REQUIRED rules** (7.18.4, 7.21.7, 7.21.3.2, 7.4.2) are **not in
>   `rule_repair_map.json`**, and there is no `OPENCLAW_REQUIRED` concept
>   anywhere in the codebase. The map vocabulary is `CONFIRMED` / `EXPECTED` /
>   `MANUAL`, with `repair_script: null` meaning manual escalation.

---

## Part 1 — The original question, answered

**Q: Should we resolve all outstanding veraPDF codes that have known scripts
before entering the AI / no-existing-knowledge phase?**

**A: Yes. Run every *effective* known repair, re-validate with veraPDF, and
trigger the AI phase off the post-repair *residual* failure set — never off the
baseline plan's `unknown_rules`.**

Four grounded reasons:

1. **Known repairs manufacture new failures.** `fix_untagged_pdf.py` writes
   `Alt='[Figure - alt text required]'` on every Figure it creates (that *is*
   veraPDF 7.3-3) and assigns headings by font-size heuristic (can produce the
   7.4.2 heading-nesting failures in the MM-TEST3 list). `fix_table_tagging.py`
   slices MCIDs evenly across pdfplumber's row count — wrong slicing yields
   malformed tables veraPDF rejects. A baseline unknown set therefore both
   *contains codes known repairs will clear* and *misses codes known repairs
   will create*. Only the post-repair residual is trustworthy.

2. **Structural repairs are not commutative.** `fix_untagged_pdf.py`
   (`repair_order: 0`) rebuilds the entire content stream and struct tree.
   Everything downstream operates on the new tree. Sending a baseline structural
   code to the AI before the order-0 rebuild means writing a script against a
   tree that is about to be replaced.

3. **"Known vs. unknown" is the wrong partition.** The partition that matters is
   *resolvable-by-an-effective-script* vs. *everything else*. At least three
   map-listed `repair_script`s **cannot produce an output PDF** (see Part 2,
   the "audit-only scripts mapped as repairs" finding). When the plan routes a
   failure to one of these, the rule silently never gets fixed. Both genuinely
   unmapped rules *and* rules mapped to ineffective scripts only become visible
   in the post-repair residual.

4. **It is nearly free.** `remediate.py` already runs post-repair veraPDF
   (Phase 6) and writes `failures_post.json`. Moving the AI/exit-3 decision from
   Phase 3 (pre-repair plan) to after Phase 6 (residual) *reuses a validation
   pass that already exists*. No extra veraPDF run.

**Caveat to hold:** this is safe only while the single hard "must run first"
repair (the order-0 rebuild) is itself a *known* script. If a future known
repair assumes a tree property that an AI-written script was meant to establish,
"known first" breaks. Not a concern for the current inventory.

**Corrected high-level flow:**
```
plan → run all effective known repairs → veraPDF post → compute residual
   → residual empty?  → QA → package (PASS/REVIEW)
   → residual non-empty? → emit signals + exit 3 → [AI writes scripts,
        registers in map] → resume → repair loop on residual → veraPDF post …
   → FAIL only when residual persists after AI phase AND caps hit
```

---

## Part 2 — Findings (grouped, with severity)

Severity: **S1** = can produce a wrong compliance verdict or silent data loss;
**S2** = breaks the intended control flow / a script never does its job;
**S3** = correctness compromise, drift, or maintainability risk.

### A. Outcome-integrity bugs (verdict can be wrong)

**[S1] `status_json_writer.py` excludes post-repair veraPDF from the verdict.**
`EXCLUDE_FROM_OVERALL` contains `verapdf_pdfua`, and there is **no
`verapdf_post` key** in `gate_files`. The orchestrator overwrites
`verapdf_summary.json` with post-repair results, but the writer's only mapped
veraPDF gate is the excluded one. Net effect: **a job that fails veraPDF after
repair can be scored `PASS`.** This is the single most dangerous bug found.
- *Note:* `remediate.py` computes its *own* `overall` independently (from
  `verapdf_post_result` + deviations) and exits on that. So the orchestrator's
  exit code may be correct while the written `STATUS.json` disagrees — and
  `package_deliverables.py` builds the human report from `STATUS.json`. So the
  machine exit, the STATUS file, and the human report can all three disagree.

**[S1] `package_deliverables.py` never routes FAIL/REVIEW outputs.** It always
writes `{basename}_remediated.pdf` to the top level of `output_dir`, regardless
of `overall`. AGENTS.md specifies `failed/` and `review/` subdirectories. A
failed job currently produces a file that looks like a passing deliverable.

**[S1] `post_job_indexer.py` cannot capture AI-discovered repairs — the
learning loop is broken at the write step.** This is the finding most central to
the stated goal ("capture and index new remediation so it's available for future
runs"). The indexer learns a new rule's *working script* only from a
`deviation_log` it reads out of `STATUS.json`, expecting entries with `rule_id`,
`script_used`, `outcome`, `note`. **Nothing in the pipeline ever writes that.**
- `remediate.py` accumulates `deviations` (different key) with fields
  `layer/step/expected/actual/context/timestamp` — no `rule_id`, no
  `script_used`, no `outcome` — and only into its own stdout summary, never into
  `STATUS.json`.
- `status_json_writer.py` writes `gates` + `overall_result`; it never writes
  `deviation_log`.
- Therefore the indexer's `deviations` dict is **always empty**. For any rule the
  AI handled outside the plan, `plan_step` is None and `deviation` is None →
  `script_used=None`, `outcome='UNKNOWN'` → the rule is written as **`MANUAL`
  with `repair_script: null`.**

Net effect: when the AI phase writes a brand-new script and it works, the indexer
records the rule as *permanently manual*. Next run sees `repair_script: null`,
escalates to manual again, AI re-derives the same script from scratch. **The
knowledge base never captures the one thing it exists to capture.**

Second defect, same script: it keys off `failures.json` (the **pre-repair
baseline**). Per Part 1, what's worth learning is "what cleared the **residual**."
Repair-introduced failures (e.g. placeholder-Alt 7.3-3) are never in
`failures.json`, so even a successful AI fix for them can't be indexed. Wrong
input set, mirroring the trigger-point bug.

*Required design:* the AI phase must emit a structured record (proposed
`JOB/learned_strategies.json`) when it registers a new script — carrying
`rule_id`, `script_path`, `args_pattern`, `repair_order`, `run_last`, and the
**post-repair per-rule outcome**. The indexer consumes *that* + the residual
failure set, not a `deviation_log` nothing produces. Until this exists, the AI
phase is write-only: it can fix a document once but never teach the system.

**[S1] `run_verapdf_profiles.sh` conflates informational profiles into the
verdict.** The summary's `result` is PASS only if *every* profile passes —
including ISO-32000-1-Tagged (and PDF/UA-2 when `--pdfua2`). AGENTS.md is explicit
that PDF/UA-2 FAIL is expected and ISO is informational. So a fully PDF/UA-1
compliant document that trips the ISO profile gets `verapdf_summary.json →
result: FAIL`. The orchestrator reads that summary for `verapdf_post_result` and
has `verapdf_post` in its critical-fails set → **a clean PDF/UA-1 doc can be
driven to overall FAIL by an informational ISO failure.** The summary must report
per-profile results and the verdict must key on PDF/UA-1 + WCAG only. This is a
*second, independent* M1 verdict bug (distinct from the exclude-set bug).

**[S1] Gate-name chaos is the root of the verdict incoherence.** The veraPDF gate
is named three different ways across the pipeline: `package_scaffold.py` stub uses
`verapdf_pdfua1` + `verapdf_wcag`; `status_json_writer.py` uses `verapdf_pdfua`
(no digit) and has no wcag key; `remediate.py` tracks `verapdf_baseline` /
`verapdf_post`. Same for metadata: orchestrator writes `metadata_post.json`,
writer looks for `metadata_parity_final.json` / `metadata_xmp_parity_audit.json`.
These mismatches mean the verdict writer frequently isn't reading the file the
orchestrator produced. **M1 must first establish one canonical gate-name registry**
before fixing exclude-sets, or the fixes won't connect.

**[S1] `preservation_audit.py` exact-match is incompatible with stream rewrites.**
It compares word lists with `==`. `fix_untagged_pdf.py` and every
`garbage=4` save re-tokenize text, so `order_match` is almost always False
post-repair → `REVIEW` at best, `FAIL` if any count drifts. Without the
tolerance band the handoff claims exists, `preservation_post` is a
near-guaranteed non-PASS, which (a) inflates REVIEW_REQUIRED and (b) feeds the
deviation list that gates the overall result.

### B. Control-flow bugs (scripts that can't do their job)

**[S2] Branch B's review-report call has the wrong signature — it crashes every
time.** `generate_alt_text_review_report.py` requires `<pdf> --draft X --out Y
--map-out Z`. remediate.py Branch B Step 3 calls it positionally as
`[script, drafts_json, review_html]` — so `pdf=drafts_json`, `review_html` is an
unexpected second positional, and required `--draft`/`--out`/`--map-out` are
absent → argparse exits 2. The orchestrator doesn't check that call's rc, so it's
non-fatal, but **the review HTML is never produced in the auto path.** Branch A
calls the same script *correctly* with all three flags. Fix Branch B's invocation
to match (and pass the actual PDF, not the draft JSON).

**[S2] Three audit-only scripts are mapped as `repair_script`s — and the repo's
own README says so.** `tools/repair/README.md` explicitly marks
`fix_notdef_glyphs.py`, `fix_contrast_color_runs.py`, and
`font_replacement_report.py` as *"(audit only)"* and states audit-only scripts
"take only `<input.pdf>`." Yet `rule_repair_map.json` maps the first two as
`repair_script`s with `<input.pdf> <output.pdf>` patterns. **The documentation
and the map directly contradict each other.** They take a
single input arg, write **no output PDF**, and exit 1 on any finding:
- `fix_notdef_glyphs.py` ← mapped to `PDF/UA-1/7.21.6-1`
- `fix_contrast_color_runs.py` ← mapped to `WCAG-2-2-Machine/1.4.3`
- `fix_parent_tree_mcids.py` ← returns `PARTIAL`/exit 1 on the very orphan-MCID
  case it's named to fix (and is not mapped to any rule anyway).

When the plan routes a failure to one of these, `remediate.py` runs it with an
`<output.pdf>` arg it ignores, no output file appears, `current_pdf` does **not**
advance, and the loop emits a Layer 1 deviation. The rule is never fixed and the
job degrades to REVIEW/FAIL for a reason unrelated to the actual document. These
scripts are **detectors mislabeled as repairs.** Either rename/relocate them to
`tools/audit/` and remove the repair mappings, or write real repair counterparts.

**[S2] `PARTIAL` is not in `PASS_CODES`, but several scripts return it on
partial success.** `fix_cidset.py`, `fix_table_tagging.py`,
`fix_figure_alt_text.py` (manual mode, figures not in map), and
`fix_parent_tree_mcids.py` all can return `PARTIAL` with exit 1. The orchestrator
treats this as a blocking Layer 1 deviation and refuses to advance `current_pdf`
— even though these scripts *did* write a valid output PDF. Decide deliberately
whether `PARTIAL` should advance the chain (probably yes, with a Layer 2
deviation logged) rather than stall it.

**[S2] Orphan scripts the plan never invokes.** `fix_table_tagging.py` and
`fix_parent_tree_mcids.py` are not referenced by any rule in
`rule_repair_map.json`. `fix_table_tagging.py` is the *only* script that builds
`Table`/`TR`/`TH`/`TD` structure — so table-structure failures (as opposed to
TH-scope, which `fix_table_headers.py` handles) currently have no route from the
plan. If documents need table *tagging* (not just scope), this is a silent gap.

### C. State / resume architecture (the actual feature being built)

**[S2] No serialized job state exists for `--resume` to read.** `repair_steps`,
`alt_branch`, and "which pass file is current" live only in local variables in
`remediate.py`. A resume cannot know where the prior run stopped. Phase 0 also
*unconditionally* re-scaffolds and `shutil.copy2(SOURCE_PDF, PASS0)`, overwriting
`pass0_source.pdf`, and the untagged-fix path reassigns `PASS0` to pass1/pass2.
So "job state is preserved between runs" is only half true: directories persist,
but a naive re-run rebuilds pass0 and redoes Phase 1–2.

**Design requirement for resume (decision needed):**
- A `state.json` (in `JOB/`) written at end of each phase: chosen
  `repair_steps`, completed steps, `current_pdf` path, `alt_branch`,
  baseline+residual failure sets, run counter per rule.
- Phase 0 must become idempotent: skip copy if `pass0_source.pdf` exists and
  matches source checksum; skip scaffold if dirs exist.
- `--resume` reads `state.json`, sets `current_pdf` to the last good pass, and
  enters the repair loop without re-running the plan from scratch.

**[S3] The exit-3 / AI loop needs a termination guard.** If the AI writes a
script that registers in the map but doesn't actually clear the rule, the
residual never empties and exit-3 → resume → exit-3 loops forever. Need a
per-rule attempt cap (the handoff mentions 15/rule, 50/job — **none of which is
implemented**; there is no cap logic anywhere) and a hard stop that converts
"residual persists after N AI attempts" into an honest FAIL.

**[S3] Whether `--resume` re-runs `lookup_repair_plan.py`.** Open question: after
the AI registers new strategies, does resume re-derive the plan from the updated
map (cleaner; "skip plan phase" is then a misnomer), or trust a passed-in
residual? Recommendation: resume re-runs the plan against the *residual* failure
set + updated map. That way newly-registered scripts are picked up by the normal
mechanism and ordering is recomputed correctly.

**[S2] `package_scaffold.py` overwrites STATUS.json with an IN_PROGRESS stub.**
Directory creation is idempotent (`exist_ok=True`), so re-running scaffold is
safe for the tree — **but it unconditionally rewrites `STATUS.json`.** Phase 0 of
`remediate.py` runs scaffold every invocation, so a naive `--resume` wipes prior
status (and would wipe a co-located `state.json`). M4 must skip scaffold on
resume, or make scaffold preserve existing status/state. (Also: the stub's gate
keys `verapdf_pdfua1`/`verapdf_wcag` don't match the writer's keys — see the
gate-name chaos S1 finding.)

**[S3] `cleanup_job.py` safety check is coupled to the M1 routing bug.** Cleanup
refuses to delete a job dir unless `output/{ticket}_remediated/` exists. But
`package_deliverables.py` creates that dir even for FAIL jobs (the routing bug),
so cleanup could green-light deleting the working dir of a *failed* job whose
output was never genuinely promoted — destroying the intermediate state the
AI/resume loop needs. Fix M1 routing first, or cleanup can eat in-flight work.

### C2. Dormant knowledge systems (both halves of "learn and reuse" are unwired)

The stated goal — capture new remediation so future runs reuse it — depends on
TWO knowledge systems, and **neither is currently wired into `remediate.py`:**

**[S1] `post_job_indexer.py` capture loop is broken** (full detail in the S1
cluster above). The script-learning channel reads a `deviation_log` nothing
writes, so AI-discovered scripts are recorded as permanently `MANUAL`.

**[S2] `doc_taxonomy.json` is designed but never read.** It's a controlled
vocabulary meant to tag documents at job start and "weight repair strategy
ordering," with a defined `proposed_taxonomy_additions` review flow for new tags.
But `remediate.py` never reads it, never assigns tags, never writes
`proposed_taxonomy_additions`. (Tellingly, its `enrollment_form` example *is*
MM-TEST3 — "E-Parent Enrollment Packet" — so the taxonomy anticipates the exact
test case but isn't connected.) If document-class-aware ordering is wanted, the AI
phase should feed this too. At minimum, decide whether taxonomy is part of the
roadmap or should be removed to avoid implying a capability that doesn't exist.

### D. Contract / consistency drift

**[S3] Duplicate `fix_metadata_xmp_parity.py`.** `tools/orchestrate/` and
`tools/repair/` copies are **byte-for-byte identical**. The map and the repair
loop both call `tools/repair/...`. The orchestrate copy is dead. Delete it to
prevent future divergence.

**[S3] JSON-only contract violated by several scripts.** AGENTS.md mandates
JSON between steps. `fix_struct_content_marking.py` prints human prose
(`Phase 1: …`, `Done. ✓`) to stdout; `fix_list_numbering.py` (as served) was
preceded by container `[init]` log lines. The orchestrator tolerates this via
`json.loads` fallback to `'PASS' if rc==0`, but a resume/state design that wants
structured results from stdout cannot rely on these. Standardize: every
repair/audit script emits exactly one JSON object on stdout.

**[S3] `fix_cidset.py` repair_order is inconsistent in the map.** Listed at
`repair_order: 4` for `7.21.4.2-1` and `repair_order: 9` for `7.21.4.2`, while
its own docstring says it "MUST run AFTER all PyMuPDF saves." If both variants
fire, the plan dedupes by script and takes `max(repair_order)` = 9 — which
happens to be correct, but the order-4 entry is a latent foot-gun. Make both
entries order 9 (or whatever "after all PyMuPDF saves, before table headers"
resolves to).

**[S3] Semantic compromises that pass veraPDF but are wrong.** Not blocking, but
worth a backlog: `fix_table_headers.py` sets every TH to `Scope=/Column`;
`fix_list_numbering.py` sets every list to `Unordered`;
`fix_table_tagging.py` guesses row boundaries by even division;
`fix_untagged_pdf.py` classifies headings purely by font size. Each can produce
structurally-valid-but-semantically-wrong output. These are exactly the cases
human/AI review should catch — relevant to how much you trust an
auto-`PASS`.

### E. Robustness notes (lower priority)

- **[S3]** XMP edits in `fix_pdfua_identifier.py` and `fix_metadata_xmp_parity.py`
  are regex/string surgery on the packet. Brittle against XMP that nests
  `rdf:Description` differently or uses attribute-form properties. Works on the
  observed Montefiore producers; may break on others.
- **[S3]** `fix_untagged_pdf.py` heading thresholds and `BULLET_CHARS` list
  hardcode Latin-script, English-ish assumptions. The MM-TEST3 doc is an
  English enrollment packet, but multilingual Montefiore documents exist
  (the asset path naming implies it). Flag for i18n review.
- **[S3]** `fix_figure_alt_text.py` figure indexing is positional by struct-walk
  order, shared across the draft and apply passes. Any change that alters walk
  order between passes silently misassigns alt text. Lock the walk order or key
  by xref.

### F. QA performance & contract (found in QA sweep)

- **[S2/perf]** `render_compare.py` and `visual_qa.py` both compute pixel
  statistics with pure-Python loops over the full sample buffer
  (`sum(1 for j in range(len(samples)) ...)` and `sum(samples)/len(samples)`).
  At 150 DPI a letter page is ~1M+ bytes iterated in Python — seconds per page,
  minutes on a large packet like MM-TEST3. Replace with numpy
  (`np.count_nonzero(np.abs(a-b)>10)`). This may be the dominant runtime cost in
  a full job.
- **[S3]** `visual_qa.py` does **no vision-model call** — it's blank-page +
  aspect-ratio heuristics plus thumbnail dumping. AGENTS.md's model-routing table
  says "switch to VISION_MODEL before calling visual_qa.py," implying semantic
  visual inspection that doesn't happen. Either wire in the vision check or
  correct AGENTS.md so the gate isn't misrepresented.
- **[S3]** `render_compare.py`, `visual_qa.py`, and `preservation_audit.py` all
  return `REVIEW`/exit 1 on entirely expected post-repair rendering changes
  (contrast recolor, tag reflow). Combined, they make REVIEW_REQUIRED the default
  outcome even for clean jobs. Calibrate thresholds and decide which of these are
  advisory (don't gate overall) vs. blocking.
- **[S3]** Alt-text policy: the review model is "no action = accepted," and
  remediate.py auto-approves vision drafts without pause. Vision-model alt text
  reaches production deliverables with no human in the loop unless someone opens
  the HTML. Defensible as automation, but it's a *policy* choice that should be
  explicit, not an accident of wiring — especially for clinical documents.

---

## Part 3 — Proposed remediation sequence (dependency-ordered)

Grouped into milestones so this can cross sessions. Each item: one file unless
noted. **Do not start any milestone before confirming the deployed branch.**

### Milestone 0 — Baseline & truth
- [ ] Confirm deployed branch == `master` (or re-baseline this doc).
- [ ] Reconcile the 5 handoff/master mismatches listed at top. For each: decide
      "re-apply" or "handoff was wrong."
- [ ] Decide `PARTIAL`-advances-chain policy (affects everything downstream).

### Milestone 1 — Stop producing wrong verdicts (S1 cluster)
- [ ] **FIRST: establish one canonical gate-name registry.** Scaffold, writer,
      and orchestrator use 3 different names for the veraPDF gate and mismatched
      metadata filenames. Nothing else in M1 connects until names align.
- [ ] `run_verapdf_profiles.sh`: report per-profile results; make the summary
      `result` key on PDF/UA-1 + WCAG only, not ISO/PDF-UA-2 (informational).
- [ ] `status_json_writer.py`: add the post-repair veraPDF gate under the
      canonical name, remove it from `EXCLUDE_FROM_OVERALL`, ensure the
      post-repair result is what counts.
- [ ] `package_deliverables.py`: route to `review/` / `failed/` by `overall`;
      add the `--skip-pdf` behavior (report-only on FAIL) the handoff assumed.
- [ ] `preservation_audit.py`: add tolerance band; redefine REVIEW vs FAIL so
      stream-rewrite tokenization noise doesn't read as content loss.
- [ ] Make `remediate.py`'s `overall`, the written `STATUS.json`, and the audit
      report derive from **one** source of truth: a shared pure `verdict()` function
      (`tools/lib/`) imported by both `remediate.py` and `status_json_writer.py`
      over one consolidated input bundle. The status-writer rewrite (replacing the
      glob-and-guess logic) IS this work. Verdict must be re-derivable from a past
      job's artifacts. (decision #5 — resolved; see RESIDUAL_AND_CAPTURE_CONTRACT.md)

### Milestone 2 — Fix the repair/audit taxonomy (S2 cluster)
- [ ] Relocate `fix_notdef_glyphs.py`, `fix_contrast_color_runs.py` to
      `tools/audit/` (they are detectors). Remove their `repair_script` mappings
      or replace with real repairs.
- [ ] Decide fate of `fix_parent_tree_mcids.py` (detector vs. repair) and
      `fix_table_tagging.py` (wire into map for table-structure failures, or
      document as manual-only).
- [ ] Make the repair loop treat `PARTIAL` as "advance + log Layer 2," not
      "stall."
- [ ] Fix Branch B's `generate_alt_text_review_report.py` invocation (wrong
      positional signature — currently crashes silently every auto run).
- [ ] (perf) Replace pure-Python pixel loops in `render_compare.py` /
      `visual_qa.py` with numpy. Likely the dominant runtime cost per job.

### Milestone 3 — The AI learning loop (the actual goal: capture + reuse)
This milestone has TWO halves. The trigger half (when to invoke the AI) and the
**capture half (how the AI's success becomes reusable knowledge)**. The capture
half is currently 100% broken (S1 indexer finding) and is the whole point.

*Trigger half:*
- [ ] Relocate the AI/exit-3 decision to **after** Phase 6 residual (per Part 1).
- [ ] Emit `openclaw_signals.json` (rule_id, description, residual failure count)
      and exit 3 when residual is non-empty.

*Capture half (do not skip — this is the stated goal):*
- [ ] Define `JOB/learned_strategies.json`: the AI writes one record per new
      script it registers — `rule_id`, `script_path`, `args_pattern`,
      `repair_order`, `run_last`, post-repair per-rule `outcome`.
- [ ] Rewrite `post_job_indexer.py` to consume `learned_strategies.json` + the
      **residual** failure set, NOT the phantom `deviation_log` + baseline
      `failures.json`. Without this, AI fixes are never indexed and every run
      re-derives from scratch.
- [ ] Verify the round-trip: AI writes script → registers in map → indexer
      promotes it → next run on a sibling doc routes to it automatically. This is
      the acceptance test for the entire feature.
- [ ] Decide `doc_taxonomy.json`'s fate: wire it in (tag at job start, feed
      ordering, write `proposed_taxonomy_additions`) as the second learning
      channel, OR remove it so it stops implying an unbuilt capability. It's the
      other dormant knowledge system; don't leave it in limbo.

### Milestone 4 — Resume & state (enables the AI loop to actually run)
- [ ] Define and write `JOB/state.json` schema; emit at end of each phase
      (chosen `repair_steps`, completed steps, `current_pdf`, `alt_branch`,
      baseline + residual failure sets, per-rule attempt counter).
- [ ] Make Phase 0 idempotent: checksum-guarded source copy; **skip scaffold on
      `--resume` (or make `package_scaffold.py` preserve existing STATUS/state
      instead of overwriting with the IN_PROGRESS stub).**
- [ ] Add `--resume`: load state, set `current_pdf` to last good pass, re-derive
      plan against *residual* + current map, run repair loop.
- [ ] Add per-rule and per-job attempt caps (confirm numbers); convert
      cap-exceeded to honest FAIL. **No cap logic exists today.**
- [ ] Update `AGENTS.md` step 5 + `openclaw.json` for the exit-3 → write →
      register → `--resume` loop, with the termination guard.

> *Why M3 before M4:* the capture loop can be specified and the indexer fixed
> independent of resume. But the loop can't *run end-to-end* without resume. Build
> the capture contract first (it constrains the state schema), then resume to
> execute it. They'll likely land together, but the indexer fix shouldn't wait.

### Milestone 5 — Drift & hygiene (S3 cluster)
- [ ] Delete `tools/orchestrate/fix_metadata_xmp_parity.py`.
- [ ] Standardize one-JSON-object-on-stdout across all scripts.
- [ ] Normalize `fix_cidset.py` repair_order entries.
- [ ] Correct AGENTS.md visual_qa model-routing claim (no vision call happens).
- [ ] Calibrate REVIEW thresholds so clean jobs don't default to REVIEW_REQUIRED.
- [ ] Doc drift: `tools/README.md` is a verbatim copy of `tools/repair/README.md`
      (doesn't cover audit/qa/packaging). `fix_table_headers.py` README claims it
      writes a Summary attribute; the code does not. Reconcile README "(audit
      only)" labels with the map (covered in M2).
- [ ] `package_deliverables.py` reimplements sha256 inline instead of calling
      `checksums.py` — dedupe.
- [ ] Backlog: semantic-correctness compromises (TH scope, list ordering, table
      row split, heading classification), XMP robustness, i18n, alt-text keying,
      alt-text auto-approve policy decision.

---

## Part 4 — Per-file reference (as read from `master`)

| File | Role | Writes PDF? | Exit on partial | Touches struct tree | Can introduce failures | Notes |
|---|---|---|---|---|---|---|
| `audit/lookup_repair_plan.py` | plan | no | — | no | no | dedupes by script, `max(repair_order)`; prefix-match fallback on clause |
| `audit/parse_verapdf_summary.py` | parse | no | — | no | no | normalizes spec strings; 3 schema fallbacks |
| `audit/rule_repair_map.json` | map | — | — | — | — | missing OPENCLAW rules; 3 detectors mapped as repairs; cidset order split |
| `repair/fix_untagged_pdf.py` | repair (order 0) | yes | — | **rebuilds** | **yes** (placeholder Alt, heuristic H) | the load-bearing structural pass |
| `repair/fix_struct_content_marking.py` | repair (order 1) | yes | — | yes | low | prints prose not JSON |
| `repair/fix_parent_tree_mcids.py` | "repair" | yes | PARTIAL/exit1 | reads | no | orphan; unmapped; detector-ish |
| `repair/fix_cidset.py` | repair | yes | PARTIAL/exit1 | no | no | order entries inconsistent |
| `repair/fix_notdef_glyphs.py` | **detector** | **no** | FAIL/exit1 | no | no | mapped as repair (S2) |
| `repair/fix_list_numbering.py` | repair (order 8) | yes | — | yes | no | defaults all lists Unordered |
| `repair/fix_table_tagging.py` | repair | yes | PARTIAL/FAIL | **rebuilds** | **yes** (row-split guess) | unmapped orphan |
| `repair/fix_table_headers.py` | repair (order 10, last) | yes | — | yes | no | good citizen; defaults Scope=Column |
| `repair/fix_pdfua_identifier.py` | repair (order 1) | yes | — | no | no | string-surgery XMP |
| `repair/fix_metadata_xmp_parity.py` | repair (order 2) | yes | exit1 if args missing | no (catalog) | no | requires --title/--subject/--keywords |
| `repair/fix_contrast_color_runs.py` | **detector** | **no** | FAIL/exit1 | no | no | mapped as repair (S2) |
| `repair/fix_link_annotation_descriptions.py` | repair (order 7) | yes | — | no (annots) | no | may not fully clear 7.18.5 (needs Link struct elems) |
| `repair/fix_figure_alt_text.py` | repair (order 6) | yes | PARTIAL/exit1 | yes (Alt/Lang) | auto-mode writes placeholder (7.3-3) | positional figure indexing |
| `repair/generate_alt_text_drafts.py` | vision | no (writes json) | PARTIAL | no | no | keys by struct-walk index; renders by xref |
| `repair/generate_alt_text_review_report.py` | review html | no (writes html+json) | — | no | no | Branch B calls it with WRONG signature → crashes (S2) |
| `audit/post_job_indexer.py` | **capture/index** | no (writes map) | — | no | — | **learning loop broken (S1)**; reads phantom deviation_log + baseline failures |
| `orchestrate/fix_metadata_xmp_parity.py` | **dead dup** | — | — | — | — | byte-identical to repair/ copy |
| `qa/preservation_audit.py` | QA | no | REVIEW/FAIL exit1 | no | no | exact `==`; no tolerance band |
| `qa/render_compare.py` | QA | no (writes png+json) | REVIEW/exit1 | no | no | pure-Python pixel loop (perf); REVIEW on expected changes |
| `qa/visual_qa.py` | QA | no (writes png+json) | REVIEW/exit1 | no | no | NO vision call despite AGENTS.md; heuristic only; perf |
| `packaging/status_json_writer.py` | verdict | no (writes json) | — | no | — | **excludes post veraPDF; gate-name mismatch (S1)** |
| `packaging/package_deliverables.py` | package | copies | — | no | — | **no FAIL/REVIEW routing (S1)**; no --skip-pdf; inline sha256 |
| `packaging/package_scaffold.py` | scaffold | no | — | no | — | idempotent dirs BUT **overwrites STATUS.json (S2)**; gate-name mismatch |
| `packaging/checksums.py` | checksums | no | — | — | — | clean; duplicated by package_deliverables |
| `packaging/cleanup_job.py` | cleanup | no (deletes) | — | — | — | well-guarded; output-check coupled to M1 routing bug |
| `audit/run_verapdf_profiles.sh` | veraPDF runner | no (writes xml+json) | — | — | — | **summary conflates ISO/UA-2 into verdict (S1)**; overwrites summary post-run |
| `audit/run_qpdf_check.sh` | qpdf runner | no (writes json) | — | — | — | best-guarded script; warnings→PASS correctly |
| `audit/table_semantics_audit.py` | detector | no | FAIL/exit1 | reads | no | spanning-table guard; FAILs on untagged tables w/ no wired repair |
| `audit/metadata_xmp_parity_audit.py` | detector | no | FAIL/exit1 | no | no | mirrors repair script; writer reads wrong filename |
| `audit/contrast_audit.py` | detector | no | FAIL/exit1 | no | no | duplicate of fix_contrast_color_runs logic; white-bg FP risk |
| `audit/font_inventory.py` | detector | no | FAIL/exit1 | no | no | detects 7.21.7 (ToUnicode); not orchestrator-wired |
| `audit/font_geometry_matcher.py` | helper | no | — | no | no | font-substitution ranker; standalone |
| `repair/font_replacement_report.py` | **detector (misfiled)** | **no** | FAIL/exit1 | no | no | "(audit only)" per README; in repair/ dir |
| `audit/doc_taxonomy.json` | **dormant knowledge** | — | — | — | — | designed for tag-weighted ordering; **never read (S2)** |
| `tools/README.md` | docs | — | — | — | — | verbatim copy of repair/README; doesn't cover other dirs |
| `tools/repair/README.md` | docs | — | — | — | — | marks 3 scripts "(audit only)" — contradicts the map |

*(Coverage complete: every code file in `tools/` read. Only `audit/alt_map_approved.json`
unread — it 404s (generated artifact, not committed). `detect_image_only_pages.py`
contract inferred from remediate.py's call + map entry; read directly if M1/M2 work
touches OCR pre-flight. `skills/` tree is out of scope.)*

---

## Part 5 — Open decisions for the human (blockers for specific milestones)

1. **Deployed branch?** (blocks everything)
2. **RESOLVED — `PARTIAL` advances the repair chain** (keeps the output PDF) and is
   advisory only; a rule's outcome is judged by the residual, not the script's result
   string. Count reduction is captured as `partially_resolved`, a diagnostic
   sub-state that never gates. See RESIDUAL_AND_CAPTURE_CONTRACT.md. (was: blocks M2/M4)
3. **Resume re-derives plan from residual, or trusts passed-in residual?**
   (recommendation: re-derive) (blocks M4)
4. **Attempt caps — confirm 15/rule, 50/job, or set real numbers.** (blocks M4)
5. **RESOLVED — Single source of truth for `overall`.** Neither `remediate.py` nor
   `status_json_writer.py` owns it. A shared pure `verdict()` function (`tools/lib/`)
   is imported by both, over one consolidated input bundle; `remediate.py` produces
   inputs (incl. `execution_log.json`), writer/packager consume. Verdict re-derivable
   from artifacts. See RESIDUAL_AND_CAPTURE_CONTRACT.md. (was: blocks M1)
6. **RESOLVED — Detectors mapped as repairs.** Reclassified via the `resolvability`
   field: notdef/contrast/fonts/widgets become `repairable_review` (build real
   repairs that emit review packages); the mislabeled detector mappings are removed.
   See RESIDUAL_AND_CAPTURE_CONTRACT.md. (was: blocks M2)
7. **RESOLVED — Capture contract.** Defined: `learned_strategies.json` +
   `residual_analysis.json`, consumed by a rewritten `post_job_indexer.py`. See
   RESIDUAL_AND_CAPTURE_CONTRACT.md. (was: blocks M3)
8. **RESOLVED — Promotion / "successful but breaks something."** Success redefined
   empirically: a strategy is indexed only if `clean` (isolated before/after veraPDF
   snapshot shows target resolved, nothing introduced/regressed). Bad ordering shows
   up as a regression and disqualifies — so AI metadata is validated, not trusted.
   `clean:false` logged as `rejected_experiments[]`, not adopted. Enabled by
   discovery-mode per-step validation. (was: M3 trust model)
9. **RESOLVED — Alt-text policy.** Alt text always clears to PASS and emits its
   per-figure report as standard reporting; never gates the verdict. Operator
   spot-checks the report per document at their discretion. (was: affects M5)
10. **`doc_taxonomy.json` — wire it in as a second learning channel
    (tag-weighted strategy ordering + proposed-additions review), or remove it?**
    Currently designed but dead. (blocks M3 scope decision)
