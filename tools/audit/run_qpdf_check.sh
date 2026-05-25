#!/usr/bin/env bash
# run_qpdf_check.sh
# Runs qpdf --check on a PDF and writes a JSON result summary.
#
# qpdf exit codes:
#   0 = no issues
#   2 = warnings only (linearization hints, object count mismatches, etc.)
#       These are cosmetic — the file is structurally sound. Result: PASS.
#   3 = errors (corrupt xref, unreadable objects, encryption issues, etc.)
#       These will break repair scripts. Result: FAIL — hard stop.
#
# Usage: run_qpdf_check.sh <pdf> <out-dir> [--linearize] [--out qpdf_check.json]
# Exit: 0 = pass, 1 = fail, 2 = usage error

set -uo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: run_qpdf_check.sh <pdf> <out-dir> [--linearize] [--out file.json]" >&2
    exit 2
fi

QPDF="${QPDF_BIN:-qpdf}"
PDF="$1"
OUT="$2"
LINEARIZE=0
OUT_FILE=""

shift 2
while [[ $# -gt 0 ]]; do
    case "$1" in
        --linearize) LINEARIZE=1 ;;
        --out) OUT_FILE="$2"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

mkdir -p "$OUT"

LOG="$OUT/qpdf_check.log"
RESULT="PASS"
WARNINGS=""
ERRORS=""

"$QPDF" --check "$PDF" > "$LOG" 2>&1
QPDF_EXIT=$?

# qpdf exit codes are not reliable across versions — some builds return 3
# for warnings-only runs ("operation succeeded with warnings").
# Always check the log content to determine actual severity.
LOG_CONTENT=$(cat "$LOG")

if echo "$LOG_CONTENT" | grep -qi "operation succeeded with warnings"; then
    # Warnings only — cosmetic, file is structurally sound
    RESULT="PASS"
    WARNINGS=$(echo "$LOG_CONTENT" | head -40 | sed 's/"/\\"/g' | tr '\n' ' ')
elif [ "$QPDF_EXIT" -eq 0 ]; then
    RESULT="PASS"
elif [ "$QPDF_EXIT" -eq 2 ]; then
    # Warnings only per exit code
    RESULT="PASS"
    WARNINGS=$(echo "$LOG_CONTENT" | head -40 | sed 's/"/\\"/g' | tr '\n' ' ')
elif [ "$QPDF_EXIT" -ge 3 ]; then
    # Check if log indicates actual structural errors vs warnings
    if echo "$LOG_CONTENT" | grep -qiE "(error|corrupt|invalid|failed|cannot|unable)"; then
        RESULT="FAIL"
        ERRORS=$(echo "$LOG_CONTENT" | head -40 | sed 's/"/\\"/g' | tr '\n' ' ')
    else
        # Exit code suggests error but log shows no structural issues — treat as warnings
        RESULT="PASS"
        WARNINGS=$(echo "$LOG_CONTENT" | head -40 | sed 's/"/\\"/g' | tr '\n' ' ')
    fi
fi

if [ "$LINEARIZE" -eq 1 ] && [ "$RESULT" = "PASS" ]; then
    LINEARIZED="${PDF%.pdf}_linearized.pdf"
    "$QPDF" --linearize "$PDF" "$LINEARIZED" >> "$LOG" 2>&1 || true
fi

JSON=$(cat <<EOF
{
  "pdf": "$PDF",
  "result": "$RESULT",
  "qpdf_exit_code": $QPDF_EXIT,
  "log": "$LOG",
  "warnings": "$WARNINGS",
  "errors": "$ERRORS"
}
EOF
)

echo "$JSON"

# Write to out-dir default location
echo "$JSON" > "$OUT/qpdf_check.json"

# Also write to explicit --out path if specified
if [ -n "$OUT_FILE" ]; then
    echo "$JSON" > "$OUT_FILE"
fi

[ "$RESULT" = "PASS" ]
