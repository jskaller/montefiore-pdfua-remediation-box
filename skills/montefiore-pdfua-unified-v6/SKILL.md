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

## How to run a remediation job

Every job uses a single orchestrator script. Do not run individual audit
or repair scripts manually — the orchestrator handles everything.

**Step 1: Derive metadata from the document**

Use PyMuPDF (fitz) — always available:
```bash
python3 -c "
import fitz
doc = fitz.open('/app/workspace/input/{TICKET}/{basename}.pdf')
for page in doc: print(page.get_text())
"
```

Derive from the text:
- `--title`: main visible heading (not a footer or filename)
- `--subject`: one sentence describing the document purpose
- `--keywords`: 4-8 comma-separated terms

**Step 2: Run the orchestrator**

```bash
python3 tools/orchestrate/remediate.py \
  /app/workspace \
  {TICKET} \
  "{source-pdf-basename}" \
  --title    "Document Title" \
  --subject  "One sentence subject" \
  --keywords "keyword1, keyword2, ..."
```

The orchestrator streams JSON progress. Watch for `DEVIATION` lines —
these are the only steps requiring your reasoning.

**Step 3: Handle deviations only**

The orchestrator signals three layers:

| Layer | Meaning | Your action |
|-------|---------|-------------|
| 1 | Script failed, file missing | Fix the execution error |
| 2 | Script ran but rule still fails | Reason why — map may be wrong |
| 3 | Novel failure, plan insufficient | Full reasoning, document outcome |

**Step 4: Confirm completion**

When orchestrator outputs `"phase": "COMPLETE"`:
- Confirm `result` is `PASS` or `REVIEW_REQUIRED`
- Confirm deliverables exist in `output/{TICKET}_remediated/`
- Report the final summary

---

## Reference

Full governing rules: `/app/AGENTS.md`
Controlling ruleset: `/app/skills/montefiore-pdfua-unified-v6/rules/V6_CONTROLLING_RULESET.md`
Pre-handoff checklist: `/app/skills/montefiore-pdfua-unified-v6/checklists/PRE_HANDOFF_CHECKLIST.md`

Do not attempt to replicate the orchestrator's gate sequence manually.
If `remediate.py` is not found at `tools/orchestrate/remediate.py`, stop
and report — do not fall back to manual execution.

