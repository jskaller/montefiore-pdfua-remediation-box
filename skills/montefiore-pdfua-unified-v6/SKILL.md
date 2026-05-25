---
name: montefiore-pdfua-unified-v6
description: PDF/UA remediation workflow. Use when asked to remediate,
  validate, preflight, fix, or package a PDF for accessibility. Runs
  veraPDF PDF/UA-1 and WCAG-2-2-Machine validation, metadata/XMP parity,
  contrast audit, table semantics, native text preservation, visual QA,
  and produces a signed deliverable package.
user-invocable: true
metadata: {"openclaw":{"requires":{"bins":["qpdf","java"],"env":["LLM_API_KEY"]},"emoji":"♿"}}
---

# PDF/UA Remediation — V6

## Before you do anything

Read `/app/AGENTS.md` in full. It is the authoritative source for:

- Container layout and directory structure
- Tool routing table (what script to use for each task)
- Gate sequence: pre-flight → audit → repair → QA → packaging
- Repair order (critical — struct tree repairs must run last)
- Hard rules: PDF/UA-1 target, metadata enforcement, output destinations,
  misrepresentation prohibition

All rules in `/app/skills/montefiore-pdfua-unified-v6/rules/` apply.
Read the controlling ruleset before any repair work:

```
/app/skills/montefiore-pdfua-unified-v6/rules/V6_CONTROLLING_RULESET.md
```

Read the pre-handoff checklist before packaging:

```
/app/skills/montefiore-pdfua-unified-v6/checklists/PRE_HANDOFF_CHECKLIST.md
```

---

## Starting a job

The operator will tell you which ticket and file to process, for example:

```
Process input/MM-17893/consent_form.pdf
```

or:

```
Process all PDFs in input/MM-17893
```

Follow the gate sequence in AGENTS.md exactly. Do not skip gates.
Do not proceed past a hard-stop failure without operator instruction.

---

## Package scaffold — runs first, every time

Before touching any file, create the job structure:

```bash
python3 tools/packaging/package_scaffold.py \
  /app/workspace \
  <TICKET-ID> \
  <source-pdf-basename>
```

This creates two directories:

```
workspace/jobs/{TICKET}_{basename}/     ← ALL intermediate work
  audit/    repair/    qa/    reports/
  STATUS.json

workspace/output/{TICKET}_remediated/  ← final deliverables ONLY
```

Never write intermediate files to output/. Never write to input/.
Record the job_dir and output_dir returned by package_scaffold.py
and use them for all subsequent steps.

---

## Repair script invocation pattern

All repair scripts follow this pattern — positional args, new output
path each time, never overwrite:

```bash
python3 tools/repair/<script>.py <input.pdf> <output.pdf> [options]
```

Intermediate PDFs are named sequentially inside jobs/{job}/repair/:

```
pass0_source.pdf
pass1_metadata.pdf
pass2_cidset.pdf
...
pass8_final.pdf
```

---

## Alt text pipeline — branching on approved map

The approved map is per-job, per-file. It always lives at:
`jobs/{TICKET}_{basename}/reports/alt_map_approved.json`

**If the approved map already exists** → apply directly, no pause:
```bash
fix_figure_alt_text.py <input.pdf> <output.pdf> \
  --alt-map jobs/{job}/reports/alt_map_approved.json
```

**If no approved map exists** → generate drafts → pause for review:
```
1. generate_alt_text_drafts.py → jobs/{job}/reports/alt_text_drafts.json
2. generate_alt_text_review_report.py → jobs/{job}/reports/alt_text_review.html
3. [PAUSE] Show operator the path to review HTML. Wait for approval.
4. Operator saves approved map to jobs/{job}/reports/alt_map_approved.json
5. [RESUME] fix_figure_alt_text.py --alt-map jobs/{job}/reports/alt_map_approved.json
```

Never read from workspace/alt_map_approved.json — that location is not used.
Never share an approved map between jobs.
Never treat placeholder text as production-ready.

---

## Hard gates (summary — AGENTS.md has full rules)

| Gate | Result on FAIL |
|------|---------------|
| veraPDF PDF/UA-1 | Hard stop — repair then re-run |
| veraPDF WCAG-2-2-Machine | Hard stop — repair then re-run |
| qpdf structural check | Hard stop |
| preservation_audit | Hard stop (REVIEW if marginal) |
| metadata_xmp_parity_audit | Hard stop — run after all repairs, before packaging |
| contrast_audit | Flag for human review, document in STATUS.json |
| axesCheck / PAC 2024 | Not run in container — mark EXTERNAL in STATUS.json |

---

## Output destinations

| Result | Location |
|--------|----------|
| PASS | `output/{TICKET}_remediated/{name}_remediated.pdf` + `{name}_AUDIT_REPORT.md` |
| REVIEW_REQUIRED | `output/{TICKET}_remediated/review/` |
| FAIL | `output/{TICKET}_remediated/failed/` |
