#!/usr/bin/env bash
# run_verapdf_profiles.sh
# Runs required veraPDF profiles against a PDF and writes XML reports.
#
# Profiles always run:
#   PDF/UA-1    — via PDFUA-1.xml profile (primary compliance target)
#   WCAG-2-2    — via WCAG-2-2-Machine.xml (pinned, required)
#   ISO-32000-1 — via ISO-32000-1-Tagged.xml (if present)
#
# PDF/UA-2 is NOT run by default. This is a PDF/UA-1 workflow.
# Pass --pdfua2 explicitly if PDF/UA-2 validation is needed.
#
# Usage: run_verapdf_profiles.sh <verapdf-bin> <profiles-root> <pdf> <out-dir> [--pdfua2]
# Exit: 0 = all profiles passed, 1 = one or more failures, 2 = usage error, 3 = missing profile

set -euo pipefail

if [ "$#" -lt 4 ]; then
    echo "usage: run_verapdf_profiles.sh <verapdf-bin> <profiles-root> <pdf> <out-dir> [--pdfua2]" >&2
    exit 2
fi

VERAPDF="$1"
PROFILES="$2"
PDF="$3"
OUT="$4"
RUN_PDFUA2=false

# Check for optional --pdfua2 flag
for arg in "$@"; do
    [ "$arg" = "--pdfua2" ] && RUN_PDFUA2=true
done

mkdir -p "$OUT"

PDFUA1="$PROFILES/PDF_UA/PDFUA-1.xml"
WCAG="$PROFILES/PDF_UA/WCAG-2-2-Machine.xml"
ISO="$PROFILES/PDF_UA/ISO-32000-1-Tagged.xml"
PDFUA2="$PROFILES/PDF_UA/PDFUA-2.xml"

if [ ! -f "$WCAG" ]; then
    echo "ERROR: missing pinned WCAG profile: $WCAG" >&2
    exit 3
fi

PASS=0
FAIL=0

run_profile() {
    local label="$1"
    local outfile="$2"
    shift 2
    echo "  running: $label"
    if "$VERAPDF" --format xml --verbose --maxfailuresdisplayed -1 "$@" "$PDF" > "$outfile" 2>&1; then
        echo "  result:  PASS -> $outfile"
        PASS=$((PASS + 1))
    else
        echo "  result:  FAIL -> $outfile"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== veraPDF validation: $(basename "$PDF") ==="

if [ -f "$PDFUA1" ]; then
    run_profile "PDF/UA-1" \
        "$OUT/verapdf_pdfua_ua1.xml" \
        --profile "$PDFUA1"
fi

run_profile "WCAG-2-2-Machine (pinned)" \
    "$OUT/verapdf_wcag_2_2_machine.xml" \
    --profile "$WCAG"

if [ -f "$ISO" ]; then
    run_profile "ISO-32000-1-Tagged" \
        "$OUT/verapdf_iso_32000_1_tagged.xml" \
        --profile "$ISO"
fi

# PDF/UA-2 only on explicit request
if [ "$RUN_PDFUA2" = true ] && [ -f "$PDFUA2" ]; then
    run_profile "PDF/UA-2 (explicit request)" \
        "$OUT/verapdf_pdfua2.xml" \
        --profile "$PDFUA2"
fi

# Write summary JSON
RESULT="PASS"
[ "$FAIL" -gt 0 ] && RESULT="FAIL"

cat > "$OUT/verapdf_summary.json" <<JSONEOF
{
  "pdf": "$PDF",
  "result": "$RESULT",
  "target": "PDF/UA-1",
  "profiles_run": $((PASS + FAIL)),
  "profiles_passed": $PASS,
  "profiles_failed": $FAIL,
  "report_dir": "$OUT"
}
JSONEOF

echo "=== Summary: $RESULT (passed: $PASS, failed: $FAIL) ==="
[ "$FAIL" -eq 0 ]
