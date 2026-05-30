# General Agent Behavior — Non-Remediation Tasks

This document applies when AGENTS.md's task type check directs you here — i.e., when the operator's first message contains a `TASK_TYPE:` header that does not contain "REMEDIATION".

For PDF/UA remediation jobs, AGENTS.md is the authoritative document. Do not apply this document's rules to remediation work.

---

## Identity

You are an agent operating inside a self-contained Docker container for the
Montefiore PDF/UA accessibility project. Your default specialization is PDF
remediation (governed by AGENTS.md), but this document directs you when the
operator's task is something else.

Regardless of task type, the following always apply:
- Safety boundaries on file modification (see below)
- External service policies (see below)
- Honest reporting — never fabricate results or claim work was done that wasn't

## Communication

Natural prose is fine. You do not need to emit JSON between steps. You do not need to follow the "communication protocol" rules from AGENTS.md.

When the operator asks for a deliverable (a report, a summary, an analysis), produce that deliverable directly. Do not narrate steps unless the operator asks for that.

## The operator's prompt is authoritative

For non-remediation tasks, the operator's prompt defines the work. Follow their instructions as given. Do not invoke the remediation orchestrator (`tools/orchestrate/remediate.py`) unless the operator explicitly asks you to.

If the operator's prompt conflicts with this document, ask for clarification. If their prompt is silent on something, use reasonable judgment consistent with the safety boundaries below.

## File modification rules

- Never modify files in `workspace/input/` — source documents are read-only
- Never modify or overwrite existing scripts in `/app/tools/` unless the operator explicitly asks you to
- New files you produce go to `/app/workspace/output/` (or wherever the operator specifies)
- Do not delete files without explicit confirmation

## Output location

Write deliverables to `/app/workspace/output/` by default, unless the operator specifies otherwise. This directory is bind-mounted to the host — the operator can find the file on their host filesystem at the corresponding location.

When you finish a task, surface the container path to the deliverable in your response so the operator can find it.

## External services

- Model provider: PRIMARY_MODEL, VISION_MODEL, and CODING_MODEL are configured
  via env vars in `.env`. The provider is agnostic — the container talks to
  whichever endpoint the env vars point to.
- veraPDF, qpdf, ocrmypdf: installed in the container

Use these only if the task requires them.

## Model routing

| Task | Model |
|------|-------|
| General reasoning, analysis, decisions | PRIMARY_MODEL |
| Visual inspection, image analysis | VISION_MODEL |
| Writing, editing, or testing code | CODING_MODEL |

Switch to CODING_MODEL when the task involves writing or modifying Python scripts,
shell scripts, or JSON configuration files. Switch back to PRIMARY_MODEL for
reasoning, planning, or gate-check interpretation.

## Implementation tasks (TASK_TYPE: IMPLEMENTATION)

When the operator's message includes `TASK_TYPE: IMPLEMENTATION`, the task
involves writing or modifying pipeline code. Follow this procedure:

1. **Read the template first.** The operator will reference a specific template
   in `docs/OPENCLAW_PROMPT_TEMPLATES.md`. Read that section in full before
   writing any code.

2. **Read the reference documents.** The following documents in `docs/` define
   all locked architectural decisions. Do not deviate from them:
   - `docs/ORCHESTRATOR_REVIEW.md` — known bugs, milestone plan, file inventory
   - `docs/RESIDUAL_AND_CAPTURE_CONTRACT.md` — the capture/index architecture,
     all data contracts, all resolved decisions

3. **Confirm the prerequisite gate.** Each template specifies a prerequisite
   (a prior template whose gate must have passed). Confirm this before starting.
   If the prerequisite gate output is not provided, ask for it.

4. **Read the current file(s) fresh from disk** before writing any changes.
   Never rely on memory of a file's contents. Always read, then write.

5. **Switch to CODING_MODEL** for the implementation itself.

6. **Run every gate check** listed in the template's GATE section.
   - Run them in order.
   - Paste the output of each check before marking it passed.
   - If a check fails, iterate on the code and re-run — do not move on with
     a failing gate.
   - The MM-TEST2 regression check applies to every template. Always run it last.

7. **Do not exceed the template's scope.** Each template has a "do not"
   section or equivalent scope constraint. Honour it precisely. Changes to
   `remediate.py` in particular are milestone-sequenced — do not anticipate
   changes that belong to a later template.

8. **Commit on gate pass.** When all gates pass, commit the changed files to
   master with a message in the form:
   `[Template X-Y] <one-line description of what changed>`

9. **Report completion.** Paste the final gate output and the commit hash.
   Do not summarize what you did — paste the evidence.

## What you do NOT do under this document

- Do not invoke `remediate.py` as part of an IMPLEMENTATION task (only as a
  gate check when the template specifies it)
- Do not look for OPENCLAW_REQUIRED signals — those are remediation-specific
- Do not write to `jobs/` or `output/{TICKET}_remediated/` paths — those are
  remediation-specific
- Do not generate STATUS.json — that's remediation-specific
- Do not require an approved alt map, doc taxonomy classification, or rule map
  lookup — those are remediation-specific

## What you DO under this document

Whatever the operator asked you to do. Their prompt is the spec.
