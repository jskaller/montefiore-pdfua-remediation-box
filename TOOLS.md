# Tools and Conventions

This document captures stable conventions only. For per-script invocation
details, read the script's docstring directly — those are the source of truth
and will be kept in sync with code changes.

## Binary paths (inside Docker container)

| Tool | Path |
|------|------|
| qpdf | `/usr/bin/qpdf` |
| python3 | `/usr/bin/python3` |
| java | `/usr/bin/java` |
| bash | `/usr/bin/bash` |
| ocrmypdf | `/usr/bin/ocrmypdf` |

veraPDF binaries and validation profiles are configured by the container
environment. The orchestrator (`remediate.py`) resolves them; agents and
scripts should not hardcode their paths.

## Script conventions

All scripts under `tools/` follow these conventions:

- **Arguments:** positional `<input>` and `<output>` paths, optional flags
- **Output:** JSON to stdout
- **Exit codes:** 0 on pass, 1 on fail, 2 on error
- **Read-only inputs:** scripts never modify their input paths
- **Audit scripts** (under `tools/audit/`): read-only, no PDF modification
- **Repair scripts** (under `tools/repair/`): write to a new output path; never overwrite source
- **QA scripts** (under `tools/qa/`): comparative checks, write artifacts to a specified output directory

For exact invocation of a specific script, run it with `--help` or read the
docstring at the top of the file.

## Orchestration

The orchestrator (`tools/orchestrate/remediate.py`) is the entry point for
all remediation jobs. It owns invocation of audit, repair, QA, and
packaging scripts. Agents should not invoke individual scripts manually
during a job — the orchestrator handles ordering, dependencies, and
intermediate file management.

## Path conventions

```
workspace/
├── input/{TICKET}/         ← source PDFs (read-only)
├── jobs/{TICKET}_{basename}/
│   ├── audit/              ← audit JSON outputs, veraPDF XMLs, sidecars
│   ├── repair/             ← intermediate PDFs (pass0, pass1, ...)
│   ├── qa/                 ← render compare images, visual QA renders
│   ├── reports/            ← alt text review HTML, alt maps
│   └── STATUS.json
├── output/{TICKET}_remediated/
│   ├── {basename}_remediated.pdf       ← only on PASS
│   ├── {basename}_AUDIT_REPORT.md
│   ├── review/                         ← on REVIEW_REQUIRED
│   └── failed/                         ← on FAIL or ESCALATION
│       └── ESCALATION_REPORT.md
└── assets/
    ├── alt_maps/                       ← cross-job approved alt text maps
    └── validation_profiles/            ← veraPDF profiles
```

## Rules

- Never overwrite source PDFs — always write to a new path under `repair/`
- Never write to `workspace/output/` during remediation — the orchestrator owns packaging
- Intermediate PDFs in `jobs/{ticket}_{basename}/repair/` are named `pass0_source.pdf`, `pass1_<script>.pdf`, etc.
- Final deliverables in `output/` are named `{basename}_remediated.pdf` and `{basename}_AUDIT_REPORT.md`
