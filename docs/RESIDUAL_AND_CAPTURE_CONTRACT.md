# Residual Analysis + Capture Contract

**Companion to:** `ORCHESTRATOR_REVIEW.md` (milestone M3, the AI learning loop).
**Type:** design specification. No code yet — this defines the data contracts that
M3 (capture) and M4 (resume) both build against, so they can be implemented
consistently across sessions.
**Status:** draft for review. Resolvability seed values (Part 5) are *proposed* and
need human confirmation before they become authoritative.

---

## Why this is the first thing to build

Three separate mechanisms all consume the same artifact, and none can be correct
until it exists in a defined form:

1. **The AI trigger** (Part 1 decision) fires when the residual is non-empty *with
   the right kind of failure*.
2. **The capture loop** (the stated goal) learns by recording which script moved a
   specific rule from failing → passing — a per-rule slice of the residual.
3. **The verdict** should only say FAIL when the residual persists *and* it's the
   kind of failure that should block.

Today `remediate.py` writes `failures_post.json` (a flat post-repair failure list)
but never diffs it against baseline or attributes a per-rule outcome. So a rule
that was never genuinely attempted (its "repair" was a detector that writes no PDF)
is indistinguishable from a rule that was attempted and still failed, which is
indistinguishable from a rule a structural rebuild cleared as a side effect. All
three downstream consumers are guessing. Define the residual cleanly once and all
three get a correct input.

This contract depends on three Part-5 decisions, flagged inline where they bite:
- **#6 resolvability** — RESOLVED: recorded explicitly in the map (this doc).
- **#2 PARTIAL policy** — still open; affects "was an effective repair attempted."
- **#5 verdict owner** — still open; affects who computes and reads the residual.

---

## Part 1 — The `resolvability` field (map schema addition)

A new field on every rule in `rule_repair_map.json`. It answers a different
question than `confidence`:

- `confidence` (existing) = *how sure are we the mapped script works?*
  (CONFIRMED / EXPECTED / MANUAL)
- `resolvability` (new) = *what class of path to compliance exists, and what
  terminal outcome does that path produce — PASS, REVIEW_REQUIRED, or FAIL?*

The two are orthogonal. A rule can be `repairable_unbuilt` (a fix is possible) with
no confidence value yet (no script to be confident about).

### The model rests on two orthogonal questions

The original draft of this field conflated them and got contrast/fonts/widgets
wrong. They must be kept separate:

1. **Can a fix be *attempted* from resources available to the pipeline?** (the
   vision LLM, the loaded font library, semantic inference of structure)
2. **Is the attempt trustworthy enough to ship unreviewed, or must a human approve
   the *choice* the fix made?**

Contrast, font substitution, and widget tagging are all "yes, attempt it" on
question 1 — and "must be reviewed" on question 2. They are **fixable**; they are
not *silently* fixable. The terminal outcome of their path is REVIEW_REQUIRED, not
PASS and not FAIL. This is the historical third outcome (PASS / REVIEW_REQUIRED /
FAIL) made explicit on the resolvability axis. Earlier I mistakenly collapsed
REVIEW_REQUIRED out of resolvability even though it was already in the verdict
vocabulary — that was the error.

**Invariant for the review path:** `repairable_review` hinges on a veraPDF *pass*.
The fix is applied, the rule clears veraPDF, and the review is about
*correctness-of-content* (is this the right color / font / tag?), NOT about
compliance. So in the residual analysis a successfully-applied `repairable_review`
rule shows up as `resolved` — with a "pending sign-off" marker riding alongside —
not as a residual failure. If a fix attempt does NOT make veraPDF pass, the rule is
`persistent`, the same as any other unfixed rule. Review is never a place to dump
rules veraPDF still fails.

### The five values

| value | fix attemptable? | terminal outcome | AI phase action |
|---|---|---|---|
| `effective` | yes, script exists | **PASS** unreviewed | Don't invoke AI; run the script. |
| `repairable_unbuilt` | yes, mechanical, no script yet | **PASS** unreviewed once built | **AI target.** Invoke AI to write the script. |
| `repairable_review` | yes, but result is a *proposal* (vision LLM / font library / semantic inference) | **REVIEW_REQUIRED** — veraPDF passes, human signs off on the choice | **AI target**, but output must be applied AND documented for review (see review-package contract). |
| `not_auto_fixable` | no path even to a reviewable proposal | **FAIL / escalate** | Do not invoke AI. Hand to external validators / manual remediation. |
| `detector_mislabeled` | *transitional data-quality flag* | n/a | n/a — fix the mapping in M2. |

`not_auto_fixable` is now **deliberately near-empty.** With the vision LLM and the
font library in play, almost everything standard is at least `repairable_review`.
The only genuine residents are cases where even a reviewable proposal can't be
formed — e.g. a font that cannot be embedded for licensing reasons *and* has no
acceptable geometry match in the library. Most things that look unfixable are really
`repairable_review`.

`detector_mislabeled` exists only so the M2 audit can mark rules needing
reclassification. By end of M2 every rule should be one of the first four.

### Why explicit beats inferred

Runtime inference ("did the script write a PDF? did the rule clear?") cannot
distinguish the five paths — they only diverge once something attempts a fix, and
even then "passed veraPDF but needs human sign-off" is invisible to a pure
residual-diff. The field also tells the AI phase *what kind of output to produce*:
a silent PASS fix vs. a fix-plus-review-package. That can't be inferred at runtime;
it's a property of the rule. Recording it in the map suits the capture-and-index
goal — the map is the knowledge base, so this judgment belongs there.

### Two orthogonal properties: "produces a review artifact" vs. "gates the verdict"

These were fused in the first draft and must stay separate. They answer different
questions:

- **Produces a review artifact** — does this fix generate a thumbnail-and-before/
  after record for a human to inspect? Alt text, contrast, fonts, and widgets all
  do. This is *standard deliverable reporting*, not a gate.
- **Gates the verdict** (`pending_review` → REVIEW_REQUIRED) — does the system
  refuse to declare the job PASS until a human signs off on the choice the fix
  made? **Only contrast, fonts, and widgets.** Alt text does NOT.

The line between them is *what the human is being asked to judge, and whether the
pipeline should hold the verdict on it*:

- Contrast/fonts/widgets make a substantive choice that could change the document
  in a way the system shouldn't unilaterally ratify (a recolor, a font swap, an
  inferred tag structure). The pipeline holds REVIEW_REQUIRED.
- Alt text also makes a fallible choice, but **policy decision: alt text always
  clears to PASS and emits its report as standard reporting.** The report's
  existence is the safety mechanism; whether the operator opens it is per-document
  discretion, outside the pipeline. The pipeline never gates on alt-text quality and
  never tracks per-figure confidence — generating the report *is* the safeguard.

So alt text's `resolvability` is `effective` (clears to PASS), and it carries a
separate `emits_review_artifact: true` property. Contrast/fonts/widgets are
`repairable_review` AND `emits_review_artifact: true`. A rule can emit an artifact
without gating; nothing gates without also emitting.

### The review-artifact format (NEW — was never normalized)

One shared artifact shape serves all four (alt-text, contrast, fonts, widgets);
only the verdict treatment differs. Each fix emits a per-instance record into
`JOB/reports/review/` containing, at minimum:
- `rule_id`, page(s), and the struct/content target touched
- a **before/after**: alt text → the figure + generated description; contrast →
  old vs. new color + old vs. new ratio; font → geometry-match report (candidate,
  coverage %, metrics) + substitution; widget → inferred tag structure vs. prior
- a rendered crop or thumbnail of the affected region (the vision LLM already
  renders these — reuse that path)
- `verapdf_now_passes: true` (the invariant for the gating rules — if false it's not
  review, it's `persistent`)
- `gates_verdict: true|false` — contrast/fonts/widgets true; alt text false
- a decision slot: approve / reject / manual-override (advisory for alt text)

The alt-text review HTML (`generate_alt_text_review_report.py`) is the closest
existing model and should be generalized into this single format. Designing it is an
M3 deliverable. Jobs with any `gates_verdict: true` artifact route the package to
`output/{ticket}_remediated/review/`; PASS jobs (incl. alt-text-only) include the
report in the normal deliverable package.

---

## Part 2 — Residual analysis (the core artifact)

A new analysis step, run after Phase 6 post-repair veraPDF. It diffs baseline vs.
post-repair failures and assigns each rule exactly one **outcome state**. Output:
`JOB/audit/residual_analysis.json`.

### Inputs (all already produced, except one)

- `audit/failures.json` — baseline failures (`failures_by_rule[]`), from
  `parse_verapdf_summary.py`.
- `audit/failures_post.json` — post-repair failures, same shape.
- `audit/repair_plan.json` — `repair_steps[]` (each with `rules_addressed`,
  `repair_script`, confidence), `manual_escalations[]`, `unknown_rules[]`.
- `rule_repair_map.json` — for `resolvability` per rule.
- **NEW: a per-step execution record** — which repair steps actually ran, their
  exit code/result, and whether they produced an output PDF. This does not exist
  today (it lives only in `remediate.py` stdout `emit()` lines). It must be
  persisted as `audit/execution_log.json`. **Per resolved decision #5, this is
  written by `remediate.py`** (the orchestrator is the only component with the live
  execution context). **It is the same record M4's `state.json` needs**, so design
  once, use for both.

### Outcome states (one per rule)

Computed from three booleans per rule — `in_baseline`, `in_residual`,
`effective_repair_ran` (a step whose `rules_addressed` includes this rule, with an
`effective`- or `repairable_*`-resolvability script, that exited success **and
produced output**) — plus the rule's `resolvability`. **Every rule outcome also
records `baseline_count` and `post_count`** (the veraPDF failure counts before and
after), regardless of state — cheap, and the input to the partial sub-state below.

| outcome | in_baseline | in_residual | condition | meaning |
|---|---|---|---|---|
| `resolved` | yes | no | a real repair ran for it | Genuine targeted fix. **Learning signal** if the script was AI-written. Carries a `pending_review` flag (below). |
| `resolved_incidental` | yes | no | no repair targeted it | Cleared as a side effect (e.g. structural rebuild). Note it; not a per-script learning signal. |
| `persistent` | yes | yes | a real repair ran, didn't clear | Layer 2: map entry likely wrong. Downgrade confidence; FAIL only if caps exhausted. |
| `attempted_no_effect` | yes | yes | rule was "mapped" but to a detector / no-PDF script | **Wiring bug, not a real fix-failure.** Must NOT hard-FAIL. Re-route by resolvability. |
| `introduced` | no | yes | — | Created by a repair (placeholder Alt 7.3-3; heuristic headings 7.4.2). New failure needing handling. |
| `never_attempted` | yes | yes | no map entry (unknown) OR `repairable_unbuilt`/`repairable_review` with no script yet | **The legitimate AI trigger set.** |
| `escalated` | yes | yes | `resolvability = not_auto_fixable` | Genuinely unfixable even as a proposal. → FAIL/escalate to external validators. |

### `partially_resolved` — a diagnostic sub-state of `persistent` (decision #2)

Defined as: `in_baseline AND in_residual AND post_count < baseline_count`. It
subdivides `persistent` — it is **not** a top-level state and **never** competes
with `resolved` (a rule that reached `post_count == 0` is `resolved`, full stop;
partial-ness is irrelevant once compliant).

Strict containment rules — this is the discipline that keeps it a signal, not noise:

- **It does not change routing.** A `partially_resolved` rule is still a failing
  rule (veraPDF still flags it). It triggers the AI / escalates exactly as
  `persistent` does.
- **It does not change the verdict.** Count reduction buys NO partial credit toward
  PASS. Compliance is binary — veraPDF passes or it doesn't. `partially_resolved` is
  a *label on a failure*, never a softening of it. The moment "we got it down to 5"
  earns leniency, the "compliant-ish" fuzziness we eliminated comes back.
- **It feeds only triage and knowledge:** (1) the AI-trigger payload — "470→5 with
  the existing script" tells the AI to *refine*, not write from scratch, and tells a
  human reviewer they're near-done, not starting over; (2) the indexer's
  `failure_modes[]` — a script that reliably reaches `partially_resolved` but never
  `resolved` has a systematic blind spot worth recording over time.

**Caveat on the count itself:** veraPDF failure counts are not always clean instance
counts — one structural root cause can cascade into many reported failures. So the
delta is a strong *triage hint*, not a precise "how much remains" metric. Record it
and route attention with it; do **not** build logic that treats the number as exact
(e.g. no "auto-escalate if <10 remain"). Signal, not metric.

### The `pending_review` marker (orthogonal to outcome)

A `resolved` rule whose fix came from a `repairable_review` script clears veraPDF
but is **not yet signed off**. The analyzer attaches `pending_review: true` to such
rules. This is a *marker on a resolved rule*, not a separate failure state —
because the veraPDF invariant holds (it passed). It rides into the verdict to
distinguish a job that is mechanically PASS-clean but awaiting human approval of
its proposed contrast/font/widget choices.

### How each state routes

- **AI trigger fires on:** `never_attempted` + `introduced`. The AI's output mode
  depends on the rule's `resolvability`: a `repairable_unbuilt` target gets a
  silent-PASS script; a `repairable_review` target gets a fix-plus-review-package.
- **AI trigger does NOT fire on:** `escalated` (truly unfixable → external),
  `persistent` (script exists but is wrong — a map-correction task), and
  `attempted_no_effect` (fix the wiring first — after M2 this state should vanish).

### Verdict (resolved #5: shared `verdict()` function) — three-valued PASS/REVIEW/FAIL

The verdict is a single pure function in a shared module (e.g. `tools/lib/verdict.py`)
that **both** `remediate.py` and `status_json_writer.py` import — neither recomputes
independently, so they cannot disagree. `remediate.py` *produces* the inputs (runs
the residual analyzer, writes `execution_log.json` + the consolidated verdict-input
bundle); the function *decides*; the writer and `package_deliverables.py` *consume*
(read the bundle, call `verdict()`, serialize/route). A past job's verdict is
re-derivable from its artifacts without re-running remediation. The function returns:

- **FAIL** iff any `persistent` (caps hit), `introduced`/`never_attempted` that
  survived the AI phase, or any `escalated`.
- **REVIEW_REQUIRED** iff no FAIL conditions, but any of: a rule carries
  `pending_review: true` (a `repairable_review` fix awaiting sign-off); an
  **experimental-profile failure** was flagged (see below); or an existing QA-side
  review flag fires.
- **PASS** iff all rules are `resolved` (none gating review) /
  `resolved_incidental` / `SKIPPED`. Alt-text-only jobs land here — the alt-text
  report ships in the package but does not gate.

**Experimental-profile failures (#12):** veraPDF experimental / non-standard profile
failures never FAIL and are never AI targets — but they raise a job-level
`pending_review` flag so a human glances at them. The verdict summary keys PASS/FAIL
on the standard profiles only (PDF/UA-1 + WCAG); experimental results ride alongside
as review flags. This is the corrected form of the old "scope filter" — nothing is
discarded; non-standard failures surface for review rather than gate or trigger.

This is the first time the three historical outcomes map cleanly onto the residual:
`escalated`/unfixed → FAIL, `pending_review` (gating fixes or experimental flags) →
REVIEW_REQUIRED, clean → PASS.

### PARTIAL handling (decision #2 — RESOLVED)

A script's `PARTIAL` return is a **self-report about its own internal completion**
("I processed some items, couldn't finish all"), emitted *before* veraPDF re-runs
and independent of it. It is not a veraPDF measurement. The residual is the source
of truth for whether failures actually went down. Resolution:

- **The validator wins.** A rule's outcome is judged purely by the residual
  (`post_count`), never by the script's result string. A script can return `PARTIAL`
  yet drive the rule to zero (→ `resolved`); it can return `FIXED` yet leave failures
  (→ `persistent`). The script's self-assessment is wrong in both directions often
  enough that it must not be a verdict input.
- **`PARTIAL` must advance the chain.** The script wrote a valid, improved PDF —
  `current_pdf` advances and the output is kept. (Fixes the current orchestrator bug
  where a non-`PASS_CODES` result discards the output and the next repair runs on the
  stale file.) `effective_repair_ran` keys on "ran + produced output," not the result
  string.
- **`PARTIAL` is advisory metadata**, retained for debugging ("this script is flaky")
  but never gating.

The count-reduction signal the user asked about lives in the `partially_resolved`
sub-state above — diagnostic only, never gating.

---

## Part 3 — `learned_strategies.json` (the capture record)

Written by the AI phase when it creates and runs a new repair script, before
handing back to the resumed orchestrator. One record per script the AI registers.
Location: `JOB/learned_strategies.json`.

```json
{
  "job_id": "MM-TEST3_English_E-Parent_Enrollment_Packet",
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
      "notes": "Generated ToUnicode CMap from embedded font cmap subtable."
    }
  ]
}
```

Field contracts:
- `outcome` must be drawn from the residual analysis and must be `resolved` for the
  strategy to be indexed as a working repair. (`persistent`/`introduced` records may
  still be written with `clean:false` so failed AI attempts are visible, but they do
  NOT enter the map as repairs.)
- `attributable` = the rule's `resolved` state is specifically explained by *this*
  script. With isolated per-step validation (below) this is always determinable.
- **`clean` (decision #8 — RESOLVED) = the definition of a capturable success.**
  True iff, measured by the script's own isolated before/after veraPDF snapshot, it
  (a) moved its target rule to `resolved`, (b) introduced no new failures
  (`introduced_rules` empty), and (c) regressed no previously-passing rule
  (`regressed_rules` empty). **Only `clean:true` strategies are indexed into the
  map.** A strategy that fixes its target but breaks something else is `clean:false`
  → logged as a known-bad experiment, NOT adopted (see indexer).
- `isolation_snapshot` = path to the per-step pre/post veraPDF result that proves
  the above. Produced in *discovery mode* (see the two-testing-modes principle).
- `repair_order` / `run_last` are the AI's proposal, but they are **validated
  empirically, not trusted on faith**: if the proposed order/slot caused a
  regression, the snapshot shows it and the strategy is `clean:false`. So #8's
  original worry ("can we trust the AI's ordering metadata?") dissolves — bad
  ordering manifests as a regression and disqualifies the strategy automatically.

### Two testing modes — execution vs. discovery (load-bearing principle)

**This distinction must be stated as a principle, with rationale, so it is not
later "optimized away" by a reader who sees only AGENTS.md's "minimize veraPDF
calls" rule and rips out discovery-mode validation as redundant.**

veraPDF is expensive, and AGENTS.md rightly says to minimize calls. But that rule
was written for — and only applies to — the **execution path**, where known repairs
are applied whose effects the plan already predicts. Re-validating after every known
step is the "rampant over-testing" that motivated the rule. That posture is correct
*there*.

It is **wrong** for the **discovery path**, where the AI is building a new repair
for an unsolved problem and the entire goal is to pin down precisely what the new
script did and did not do. Here, validation cost is not a concern — correctness is.

| | Execution mode | Discovery mode |
|---|---|---|
| When | Applying known/mapped repairs | AI building/proving a NEW script |
| veraPDF posture | Minimize — trust the plan, validate once post-repair | Validate as much as correctness needs |
| Validation granularity | One post-repair run for the whole sequence | **Isolated before/after snapshot around each new script** |
| Why | Effects are predictable; over-testing is waste | Effects are unknown; precise attribution is the point |

Consequences of getting this right:
- A new script gets its own immediate pre/post veraPDF snapshot, so
  `introduced_rules` / `regressed_rules` are attributed to *that script* with
  certainty — no inference, no "by elimination."
- The earlier proposed constraint of **one-new-script-per-AI-cycle is removed.** It
  existed only to keep attribution cheap under single-end-validation. With per-step
  discovery snapshots, the AI may introduce multiple new scripts per cycle and each
  is still attributed precisely. The loop is shaped by what's correct for the
  remediation, not by what keeps testing cheap.

The cost is bounded by construction: discovery-mode validation fires only when a new
script is being proven — rare and high-value — not on every step of every job.

---

Replace the broken capture logic (reads a `deviation_log` nothing writes; keys off
baseline `failures.json`). New behavior:

**Consume** `learned_strategies.json` + `residual_analysis.json` (NOT
`deviation_log` + baseline failures).

For each strategy with `outcome == "resolved"` AND `clean == true` (decision #8 —
target resolved, nothing introduced, nothing regressed, per its isolation snapshot):

- **Rule not in map** → add entry: `repair_script = script_path`,
  `resolvability = proposed_resolvability` (default `effective`; may be
  `repairable_review` if the AI built a review-producing repair),
  `confidence = EXPECTED` (one job isn't enough — unchanged from current policy),
  `repair_order`, `run_last`, `args_pattern` from the record, `confirmed_jobs = 1`.
- **Rule in map as `repairable_unbuilt` (no script)** → promote: attach the script,
  set `resolvability = effective`, `confidence = EXPECTED`.
- **Rule in map as `repairable_review` (no script)** → promote: attach the script,
  keep `resolvability = repairable_review` (the rule still terminates in review even
  with a working script — review is intrinsic to the rule, not a maturity stage),
  `confidence = EXPECTED`.
- **Rule in map with a different script that worked** → add `edge_cases[]` entry,
  don't overwrite primary (keep current behavior — it was correct).

**A `clean == false` strategy is NEVER indexed as a repair**, even if its target
rule reached `resolved`. A fix that breaks something else is not the fix we want.
Instead it is logged as a known-bad experiment under a new `rejected_experiments[]`
record on the target rule: `{script_path, introduced_rules, regressed_rules,
job_id, date}`. This is the operational form of "successful but causes downstream
issues → re-evaluate": the next run sees the rejected experiment and does NOT repeat
it blindly; the AI (or a human) knows that approach, in that slot, has a known side
effect to design around.

Note the asymmetry: a `repairable_unbuilt` rule, once a script exists, becomes
`effective` (silent PASS). A `repairable_review` rule stays `repairable_review`
forever — the need for human sign-off on the *choice* doesn't go away because we
automated the *attempt*. The map must not auto-promote a review rule to silent-PASS.

For residual outcomes the indexer also records (for knowledge, not as repairs):
- `persistent` on an `effective` rule → downgrade confidence, log `failure_modes[]`
  (current behavior was right; just drive it from residual not deviations).
- `introduced` rules → log under a new `introduced_by[]` annotation on whichever
  repair created them, so the map accumulates "this repair has this side effect."
  This is how the system learns that `fix_untagged_pdf.py` reliably introduces
  7.3-3, which informs ordering and follow-up planning.

Promotion to `CONFIRMED` after N jobs stays, gated by decision #8.

---

## Part 5 — Proposed resolvability seed values

To be confirmed before authoritative. Covers current map rules + the four MM-TEST3
rules. (Confidence values are existing; resolvability is the new proposed column.)

| rule_id | current script | proposed `resolvability` | rationale |
|---|---|---|---|
| 5, 6.2 | fix_pdfua_identifier | `effective` | deterministic XMP fix, confirmed |
| 6.7.2, 7.21.4.2(-1) | fix_cidset | `effective` | deterministic descriptor edit |
| 6.2.11.8, 7.1, 7.1-1/2/3 | fix_metadata_xmp_parity | `effective` | deterministic metadata/catalog |
| 7.1-content-unmarked | fix_struct_content_marking | `effective` | confirmed |
| 7.1-untagged, 7.2-1, 7.18.3, 7.4.4 | fix_untagged_pdf | `effective` | confirmed structural rebuild |
| 7.18.5, 7.18.1(-1) | fix_link_annotation_descriptions | `effective` | but may need Link struct elems too — watch for `persistent` |
| 7.2, 7.3(-1/-3), 1.1.1 | fix_figure_alt_text | `effective` + emits-artifact | with approved map. Clears to PASS; emits per-figure thumbnail+description report as standard reporting; does NOT gate the verdict. |
| 7.5-1/7.5-2 | fix_table_headers | `effective` | confirmed, run-last |
| 7.6-1 | fix_list_numbering | `effective` | deterministic (semantics may be wrong, but rule clears) |
| 7.21.6-1 | fix_notdef_glyphs *(detector!)* | **`repairable_review`** | font substitution via the loaded library + geometry match → propose & review. Build the real repair. |
| WCAG 1.4.3 | fix_contrast_color_runs *(detector!)* | **`repairable_review`** | vision LLM ascertains fg/bg, makes minimum lightness/darkness change to pass; document old→new color + ratio for review. Build the real repair. |
| 7.21.3-1 | null (MANUAL) | **`repairable_review`** | geometry-match against the loaded font library, substitute, emit geometry report for review |
| 7.18.3-1 | null (MANUAL) | **`repairable_review`** | semantic inference of form-field tag structure → propose & review |
| **7.18.4** | *(absent)* | **`repairable_review`** | widget-in-Form-tag; same semantic-inference path as 7.18.3-1, reviewed |
| **7.21.7** | *(absent)* | **`repairable_unbuilt`** | ToUnicode CMap mechanically generable from embedded font → silent-PASS AI target |
| **7.21.3.2** | *(absent)* | **`repairable_unbuilt`** | CIDToGIDMap for Identity-encoded Type2 CIDFonts is mechanical → silent-PASS AI target |
| **7.4.2** | *(absent)* | **`repairable_review`** | heading-level renumbering is mechanical but semantically risky → propose & review (not silent PASS) |

*(`not_auto_fixable` has no residents in the current rule set. It's reserved for a
future case where even a reviewable proposal can't be formed — e.g. a non-embeddable
licensed font with no acceptable geometry match. If that arises it's a true FAIL.)*

Note the payoff, now sharper: every MM-TEST3 rule has an automated path. `7.21.7`
and `7.21.3.2` are silent-PASS AI targets (mechanical, trustworthy). `7.4.2` and
`7.18.4` are AI targets that produce *reviewable proposals* (apply the fix, clear
veraPDF, document for sign-off) rather than being escalated unfixed. Contrast and
fonts move from "deprioritized/unfixable" to "build the vision-LLM / font-library
repair, terminating in REVIEW_REQUIRED." Nothing in the current set is a dead end.

---

## Part 6 — Dependencies & build order within this contract

0. **Experimental-profile handling** (#12). veraPDF experimental / non-standard
   profile failures don't gate PASS/FAIL and aren't AI targets, but they raise a
   job-level `pending_review` flag → REVIEW_REQUIRED. The summary keys PASS/FAIL on
   standard profiles (PDF/UA-1 + WCAG) only; experimental results ride alongside as
   review flags. This is also the fix for the `run_verapdf_profiles.sh` verdict bug.
   No profile-metadata verification needed — simple logic, nothing discarded.
1. **Add `resolvability` to the map** + seed it (Part 1 + Part 5). Pure data; no
   code. Can happen immediately, independent of deployed-branch question.
2. **Persist `execution_log.json`** from `remediate.py` (the per-step run record).
   Shared with M4 `state.json` — co-design. *Unblocked: #5 resolved — orchestrator
   writes it.*
3. **Build the residual analyzer** → `residual_analysis.json` (Part 2). Needs 0+1+2.
   *Unblocked: #2 resolved.* Includes **discovery-mode per-step validation** (Part 3
   two-modes principle): when a new script is being proven, take an isolated
   before/after veraPDF snapshot so `clean`/`introduced_rules`/`regressed_rules` are
   attributed to it with certainty. Execution mode keeps single post-repair
   validation. Record `baseline_count`/`post_count` per rule; derive
   `partially_resolved`.
3b. **Define the consolidated verdict-input bundle + shared `verdict()` module**
   (resolved #5). The bundle = `residual_analysis.json` + QA/metadata gate summary
   + experimental-profile flag, emitted by the analyzer phase as one file so
   `verdict()` has a single input. `verdict()` lives in `tools/lib/`; both
   `remediate.py` and the rewritten `status_json_writer.py` import it. This *is* the
   M1 status-writer rewrite, pointed at a clean target — not extra work. Requires
   the canonical gate-name registry (M1) to exist first.
4. **Design the unified review-package format** (Part 1, review-package contract).
   Generalize the alt-text review HTML to serve alt-text + contrast + fonts +
   widgets. Needed before any `repairable_review` repair can be built.
5. **Define `learned_strategies.json`** (Part 3) — data contract the AI writes to,
   including the `clean` criterion + isolation snapshot (resolved #8).
   Needs the residual analyzer's outcome vocabulary + discovery-mode validation.
6. **Rewrite `post_job_indexer.py`** (Part 4). Needs 4+5. Indexes only
   `clean:true` strategies; logs `clean:false` as `rejected_experiments[]`.

All decisions blocking this contract are now resolved. Step 0–1 are the
unblock-everything moves (data + simple logic). Steps 2–3 are the foundation
(execution log + analyzer + discovery validation). Step 6 closes the learning loop.

---

## Decisions (all resolved — fed back to ORCHESTRATOR_REVIEW Part 5)

- **#8 RESOLVED** — success is redefined empirically: a learned strategy is indexed
  only if `clean` — its isolated before/after veraPDF snapshot shows the target rule
  resolved AND nothing introduced AND nothing regressed. Bad ordering manifests as a
  regression → `clean:false` → not indexed, so the AI's proposed `repair_order` is
  validated empirically rather than trusted. `clean:false` strategies are logged as
  `rejected_experiments[]` (known-bad, not repeated). Enabled by discovery-mode
  per-step validation; this removed the one-script-per-cycle constraint.
- **#5 RESOLVED** — neither existing component owns the verdict. A shared pure
  `verdict()` function (in `tools/lib/`) is imported by both `remediate.py` and
  `status_json_writer.py`, over one consolidated input bundle. `remediate.py`
  produces inputs (incl. `execution_log.json`); writer/packager consume. Verdict is
  re-derivable from artifacts. This subsumes the M1 status-writer rewrite.
- **#2 RESOLVED** — the validator wins: a rule's outcome is judged by the residual
  (`post_count`), never the script's result string. `PARTIAL` advances the chain
  (keeps the output PDF) and is advisory only. Count reduction is captured as the
  `partially_resolved` diagnostic sub-state of `persistent` — never gates the verdict.
- **#11 RESOLVED** — seed table confirmed with `repairable_review` added: contrast,
  fonts (7.21.3-1, 7.21.6-1), and widgets (7.18.3-1, 7.18.4) are reviewable repairs
  (build them); `7.4.2` is reviewable; `7.21.7`/`7.21.3.2` are silent-PASS targets.
  `not_auto_fixable` is near-empty by design.
- **#6 RESOLVED** — resolvability is explicit in the map (this contract).
- **#12 RESOLVED** — experimental-profile failures flag the job for review
  (REVIEW_REQUIRED), never FAIL, never AI target. Verdict keys on standard profiles
  only. No profile-metadata verification needed.
- **#13 RESOLVED** — one shared review-artifact format (generalize the alt-text
  HTML) serves all four rule types, but "emits artifact" and "gates verdict" are
  separate: alt text emits + PASS (report is advisory, operator spot-checks per
  document); contrast/fonts/widgets emit + gate → REVIEW_REQUIRED, routed to
  `output/{ticket}_remediated/review/`.

**No open decisions remain in this contract.** Remaining cross-cutting items live in
ORCHESTRATOR_REVIEW Part 5 (#1 deployed branch, #3 resume re-derives plan, #4
attempt caps, #9 alt-text policy [resolved here], #10 taxonomy) — those belong to
M4/roadmap, not to this contract's executability.
