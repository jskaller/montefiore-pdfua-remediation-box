# V6 Controlling Ruleset

Use this package as the controlling quickstart for Montefiore PDF/UA remediation.

Core principles:

- Process only PDFs explicitly uploaded or explicitly named as active source PDFs.
- Preserve native text, visual layout, reading order, tags, links, annotations, and meaningful images.
- Never claim axesCheck/PAC pass unless actually run.
- Do not hand off a failed veraPDF PDF/UA output except as explicit diagnostic-only output.
- Use pinned WCAG profile for PDF/UA-1.
- Enforce metadata Info + XMP parity after final save.
- Use font replacement only as a last resort after geometry matching.
