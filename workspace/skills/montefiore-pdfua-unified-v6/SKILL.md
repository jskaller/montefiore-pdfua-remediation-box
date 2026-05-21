---
name: montefiore-pdfua-unified-v6
description: montefiore pdf/ua remediation workflow for active source pdfs using the clean v6 quickstart rules. Use when the user asks to remediate, validate, preflight, package, or perform targeted accessibility fixes on montefiore/einstein pdf files. Enforces qpdf, verapdf pdf/ua, pinned wcag profile, metadata/xmp parity, contrast, table semantics, native text preservation, visual qa, and separate external axescheck/pac status. Does not process examples unless explicitly named active.
---

# Montefiore PDF/UA Unified V6

Use this skill for Montefiore PDF/UA remediation and targeted PDF accessibility fixes.

## Required behavior

1. Process only PDFs explicitly uploaded or explicitly named as active source PDFs.
2. Read and obey the V6 rules in `references/` before handoff.
3. Treat veraPDF PDF/UA failure as stop-and-remediate, not as a normal handoff state.
4. Use pinned WCAG profile `PDF_UA/WCAG-2-2-Machine.xml` for PDF/UA-1.
5. Enforce metadata Info + XMP parity after final save.
6. Run and report local gates separately from axesCheck/PAC.
7. Do not claim axesCheck/PAC pass unless actually run.
8. Use font replacement only as a last resort and only after geometry matching.
9. Package outputs with reports, logs, status JSON, visual QA artifacts, rules copy, manifest, and checksums.

## Reference loading

- Read `CURRENT_PDFUA_REMEDIATION_RULESET.md` for the controlling workflow.
- Read `METADATA_XMP_PARITY_HARD_GATE.md` before any handoff.
- Read `FONT_POLICY_EXPECTED_OPEN_FONTS_AND_GEOMETRY.md` before any font replacement.
- Read `PRE_HANDOFF_CHECKLIST.md` before producing downloads.

## External tools

qpdf and veraPDF binaries are required but not bundled. The validation profile XML repository is bundled in the assets package.
