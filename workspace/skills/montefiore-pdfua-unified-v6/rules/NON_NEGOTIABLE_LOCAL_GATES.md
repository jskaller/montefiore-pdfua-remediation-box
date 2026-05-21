# Non-Negotiable Local Gates

A PDF may receive `PRODUCTION_FINAL_LOCAL_PREFLIGHT` only when all applicable local gates pass.

Required gates:

1. `qpdf --check` passes.
2. veraPDF PDF/UA target profile passes.
3. veraPDF PDF/UA failure is a stop-and-remediate condition, not a normal handoff state.
4. Pinned veraPDF WCAG profile runs and passes when the profile repository is available.
5. Metadata passes for both PDF Info dictionary and XMP packet parity after final save.
6. Catalog language is present and correct.
7. Native text preservation passes.
8. Link/widget/annotation/image preservation passes.
9. Alt text/artifacting audit passes for meaningful images.
10. Table semantics/header association audit passes for visible/tagged tables.
11. Contrast audit passes or contains only documented accepted exceptions.
12. File size and visual QA checks pass.
13. External axesCheck/PAC status is reported separately and never implied.

Failed PDF/UA output may only be packaged as `DIAGNOSTIC_ONLY_DO_NOT_USE` when the user explicitly requests a diagnostic package.
