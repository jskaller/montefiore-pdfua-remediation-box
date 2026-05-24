#!/usr/bin/env bash
# run_verapdf_profiles.sh
# Runs all required veraPDF profiles against a PDF and writes XML reports.
# Profiles run: PDF/UA-1 (profile), WCAG-2-2-Machine (pinned), ISO-32000-1-Tagged, PDF/UA-2 (if present)
#
# Usage: run_verapdf_profiles.sh <verapdf-bin> <profiles-root> <pdf> <out-dir>
# Exit: 0 = all profiles passed, 1 = one or more failures, 2 = usage error, 3 = missing profile

set -euo pipefail

if [ "$#" -lt 4 ]; then
    echo "usage: run_verapdf_profiles.sh <verapdf-bin> <profiles-root> <pdf> <out-dir>" >&2
    exit 2
fi

VERAPDF="$1"
PROFILES="$2"
PDF="$3"
OUT="$4"

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
    else
        echo "  result:  FAIL -> $outfile"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== veraPDF validation: $(basename "$PDF") ==="

if [ -f "$PDFUA1" ]; then
    run_profile "PDF/UA-1 (profile)" \
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

if [ -f "$PDFUA2" ]; then
    run_profile "PDF/UA-2" \
        "$OUT/verapdf_pdfua2.xml" \
        --profile "$PDFUA2"
fi

# Write summary JSON
RESULT="PASS"
[ "$FAIL" -gt 0 ] && RESULT="FAIL"

cat > "$OUT/verapdf_summary.json" <<EOF
{
  "pdf": "$PDF",
  "result": "$RESULT",
  "profiles_run": $((FAIL + PASS + $(ls "$OUT"/*.xml 2>/dev/null | wc -l))),
  "failures": $FAIL,
  "report_dir": "$OUT"
}
EOF

echo "=== Summary: $RESULT (failures: $FAIL) ==="
[ "$FAIL" -eq 0 ]
