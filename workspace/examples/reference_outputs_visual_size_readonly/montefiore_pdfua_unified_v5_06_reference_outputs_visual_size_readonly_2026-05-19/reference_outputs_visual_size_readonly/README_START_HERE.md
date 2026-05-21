# Montefiore Einstein PDF/UA Remediation Handoff

This is the clean, current new-context handoff package for the Montefiore Einstein PDF/UA remediation workflow.

## Start here

1. Open `prompts/START_NEW_CONTEXT_PROMPT_DETAILED.md` and paste it into a new ChatGPT context.
2. Attach the relevant handoff zips from this clean package set.
3. In the new context, read:
   - `rules/CURRENT_PDFUA_REMEDIATION_RULESET.md`
   - `rules/METADATA_RULESET_RECAP.md`
   - `docs/RUNBOOK.md`
   - `checklists/PRE_HANDOFF_CHECKLIST.md`
4. Upload qpdf and veraPDF separately if the new context does not already have them.

## Folder structure

- `rules/` — current PDF/UA and metadata rules.
- `docs/` — runbook and continuation reference.
- `prompts/` — detailed new-context prompt.
- `checklists/` — pre-handoff checklist.
- `templates/` — metadata audit and status JSON templates.
- `scripts/` — reusable setup, validation, metadata, preservation, render comparison, and package scaffolding scripts.
- `skills/` — packaged skill plus editable source.
- `examples/` — source examples and QA crops illustrating prior failures/fixes.
- `reference_outputs/` — complete prior output packages used as reference examples.
- `manifests/` — content summary and checksums.

## Intentional exclusions

qpdf and veraPDF are not bundled here. The user has separate copies and can upload them when needed.

## Important packaging rule

No single example document or reference output package is split across multiple zips. If the handoff is divided into multiple zip downloads, each contained file remains whole within exactly one zip.
