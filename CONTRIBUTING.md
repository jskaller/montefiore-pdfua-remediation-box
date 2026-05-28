# Contributing
## Montefiore PDF/UA Remediation Suite

This document covers how to make changes to the codebase — scripts, rules,
config, and documentation — and how to keep everything consistent.

---

## The core principle

Rules and scripts are two facets of the same thing. A rule without a script
implementing it is incomplete. A script without a rule governing it is
ungoverned. **Changes to a rule and the scripts it governs belong in the
same commit.**

`MANIFEST.md` is the map. Before touching anything, check MANIFEST.md to
find what travels with it.

---

## Commit convention

```
<type>(<scope>): <short description>

Scripts: <comma-separated list of changed scripts>
Rule:    <rule file if changed, or "none">
```

**Types:**
- `feat` — new capability (new script + new rule)
- `fix` — bug fix in existing script
- `rule` — rule clarification with no script change
- `config` — .env.example, docker-compose, docker-init.sh changes
- `docs` — WORKFLOW.md, CONTRIBUTING.md, MANIFEST.md, README

**Examples:**

```
feat(alt-text): add vision model draft generation pipeline

Scripts: tools/repair/fix_figure_alt_text.py,
         tools/repair/generate_alt_text_drafts.py,
         tools/repair/generate_alt_text_review_report.py
Rule:    skills/montefiore-pdfua-unified-v6/rules/ALT_TEXT_RULE.md
```

```
fix(contrast): correct luminance formula for dark backgrounds

Scripts: tools/repair/fix_contrast_color_runs.py
Rule:    none
```

```
rule(pikepdf): clarify proportional change limit applies to writes only

Scripts: none
Rule:    skills/montefiore-pdfua-unified-v6/rules/PIKEPDF_USAGE_RULE.md
```

The `Scripts:` and `Rule:` trailer lines in the commit body are what makes
`git log` useful as an audit trail. They are not optional for `feat` and
`fix` commits.

---

## Adding a new tool to the pipeline

Follow this checklist in order. Doing them out of order creates gaps.

1. **Write the rule first** — `skills/montefiore-pdfua-unified-v6/rules/NEW_RULE.md`
   Defines: when the tool is used, what it does, what it must not do,
   what goes in STATUS.json, pass/fail criteria.

2. **Write the script** — in the appropriate `tools/` subdirectory.
   Follow existing script conventions: argparse, JSON to stdout,
   `--out` flag for file output, exit codes 0/1/2.

3. **Add the gate** — in `tools/packaging/status_json_writer.py`
   `gate_files` dict. Gate name should match the script stem.

4. **Update TOOLS.md** — document the script's usage, flags, and
   stdout format. Teammates need to know how to call it.

5. **Update SKILL.md** — if the new rule changes when or how the
   agent makes decisions, update the skill's decision logic section.

6. **Update MANIFEST.md** — add the new rule and its governed scripts.

7. **Update smoke_test.py** — add a check for any new binary dependency
   (e.g. new apt package) so container validation catches missing deps.

8. **Update requirements.txt** — if the script needs a new Python package.

---

## Changing an existing script

1. Check MANIFEST.md — find the rule that governs this script.
2. Read that rule — confirm your change is consistent with it.
3. If your change requires a rule update, update the rule in the same commit.
4. If your change is purely a bug fix with no behavioural change,
   a rule update is not needed — note `Rule: none` in the commit.

---

## Changing a rule

1. Check MANIFEST.md — find the scripts this rule governs.
2. For each primary script: confirm the script still implements the rule
   correctly after your change.
3. If the rule change requires script updates, make them in the same commit.
4. If the rule change is a clarification only (no behavioural change),
   scripts do not need updating — note `Scripts: none` in the commit.

---

## Skills directory vs workspace

`skills/` is source code. It lives in the repo, gets committed, and is
version-controlled with the same discipline as scripts.

`workspace/` is runtime data. It lives on the host machine at `WORKSPACE_PATH`.
PDFs, job artifacts, and outputs go here. Never commit anything from
`workspace/input/`, `workspace/jobs/`, or `workspace/output/`.

The `.gitignore` enforces this. If git tries to stage something from
`workspace/input/` or `workspace/jobs/`, something is wrong — stop and
check before committing.

---

## Adding a new provider

All provider configuration lives in `.env.example`. Adding a new provider
means adding a new commented block there.

1. Add a commented block to `.env.example` showing the provider's required
   env vars (`PRIMARY_PROVIDER_BASE_URL`, `PRIMARY_PROVIDER_API_KEY`,
   `PRIMARY_MODEL`, and vision equivalents).
2. Include a link to the provider's API key page in the comment header.
3. Verify the model IDs against the provider's live catalog before committing —
   model ID strings change and wrong ones waste everyone's time.
4. Test against a real PDF before committing if possible.
5. Commit as `config(env): add <provider> example block`.

---

## Branching

Direct to main is fine for solo work. If you want review before merging:

```bash
git checkout -b feat/new-tool-name
# make changes
git push origin feat/new-tool-name
# open PR or ask for review
```

Branch naming: `feat/`, `fix/`, `rule/`, `config/`, `docs/` matching the
commit type conventions above.

---

## Rebuilding Docker

Most changes do NOT require a Docker rebuild:
- Rule changes → no rebuild (skills/ is a code mount)
- Script changes → no rebuild (tools/ is a code mount)
- Config/docs changes → no rebuild

Rebuild required only when:
- `requirements.txt` changes (new Python package)
- `Dockerfile` changes (new system dependency, veraPDF version bump)
- New binary tool added (apt package)

```bash
docker compose up --build
```

After rebuilding, run smoke_test.py to confirm nothing broke:
```bash
docker compose exec remediation python3 smoke_test.py
```
