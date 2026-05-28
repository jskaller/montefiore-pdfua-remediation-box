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

- Model provider: PRIMARY_MODEL and VISION_MODEL are configured via env vars in `.env`. The provider is agnostic — the container talks to whichever endpoint the env vars point to.
- veraPDF, qpdf, ocrmypdf: installed in the container

Use these only if the task requires them.

## What you do NOT do under this document

- Do not invoke `remediate.py`
- Do not look for OPENCLAW_REQUIRED signals — those are remediation-specific
- Do not write to `jobs/` or `output/{TICKET}_remediated/` paths — those are remediation-specific
- Do not generate STATUS.json — that's remediation-specific
- Do not require an approved alt map, doc taxonomy classification, or rule map lookup — those are remediation-specific

## What you DO under this document

Whatever the operator asked you to do. Their prompt is the spec.
