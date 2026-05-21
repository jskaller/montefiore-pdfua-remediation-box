# Local and External Status Rule

Report local and external status separately.

Local statuses:

- `PRODUCTION_FINAL_LOCAL_PREFLIGHT`
- `REVIEW_REQUIRED`
- `TOOL_SETUP_BLOCKED`
- `DIAGNOSTIC_ONLY_DO_NOT_USE`

External statuses:

- `AXESCHECK_PASS`
- `AXESCHECK_FAIL_DETAILS_AVAILABLE`
- `AXESCHECK_FAIL_DETAILS_UNAVAILABLE`
- `AXESCHECK_NOT_RUN_PENDING`
- `PAC_PASS`
- `PAC_FAIL_DETAILS_AVAILABLE`
- `PAC_FAIL_DETAILS_UNAVAILABLE`
- `PAC_NOT_RUN_PENDING`

Never claim axesCheck or PAC pass unless that tool is actually run and passes. If external reports show only summary failures and expanded details are unavailable, do not perform blind destructive remediation. Preserve the best local-preflight PDF and mark the external status accordingly.
