# V6 Runbook

1. Confirm active source PDFs.
2. Run classification and source audit.
3. Preserve native text wherever possible.
4. Repair tags/structure, tables, annotations, figures, language, metadata, and contrast.
5. Run qpdf.
6. Run veraPDF PDF/UA.
7. If PDF/UA fails, stop and remediate, do not hand off.
8. Run pinned WCAG profile.
9. Run metadata Info + XMP parity audit after final save.
10. Run contrast/table/native text/preservation/visual QA gates.
11. Package outputs with logs, status JSON, rules copy, manifest, and checksums.
12. Report external axesCheck/PAC separately.
