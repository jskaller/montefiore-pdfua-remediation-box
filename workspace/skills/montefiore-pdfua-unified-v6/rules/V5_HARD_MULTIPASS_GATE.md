# Hard Multi-Pass Remediation Gate

A veraPDF PDF/UA failure is not a handoff state.

If veraPDF PDF/UA fails and the required tools are runnable:

1. Do not produce a normal user-facing remediated PDF deliverable.
2. Do not produce a final package.
3. Parse veraPDF XML.
4. Group failures by rule, check, object class, page, and likely repair class.
5. Remediate each actionable failure class.
6. Rerun veraPDF.
7. Repeat until PASS or documented exhaustion.

A failed PDF/UA package may be produced only when the user explicitly asks for a diagnostic package, and it must be labeled `DIAGNOSTIC_ONLY_DO_NOT_USE`.

Use `REVIEW_REQUIRED` only after targeted remediation attempts are exhausted and remaining blockers are documented with evidence showing why further remediation is unsafe, impossible, or tool-blocked.
