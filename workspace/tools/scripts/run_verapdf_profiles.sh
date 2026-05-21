#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 4 ]; then echo "usage: run_verapdf_profiles.sh <verapdf-bin> <profiles-root> <pdf> <out-dir>" >&2; exit 2; fi
VERAPDF="$1"; PROFILES="$2"; PDF="$3"; OUT="$4"
mkdir -p "$OUT"
WCAG="$PROFILES/PDF_UA/WCAG-2-2-Machine.xml"
ISO="$PROFILES/PDF_UA/ISO-32000-1-Tagged.xml"
if [ ! -f "$WCAG" ]; then echo "missing pinned WCAG profile: $WCAG" >&2; exit 3; fi
"$VERAPDF" --format xml --verbose --maxfailuresdisplayed -1 --flavour ua1 "$PDF" > "$OUT/verapdf_pdfua_ua1.xml"
"$VERAPDF" --format xml --verbose --maxfailuresdisplayed -1 --profile "$WCAG" "$PDF" > "$OUT/verapdf_wcag_2_2_machine.xml"
if [ -f "$ISO" ]; then "$VERAPDF" --format xml --verbose --maxfailuresdisplayed -1 --profile "$ISO" "$PDF" > "$OUT/verapdf_iso_32000_1_tagged.xml"; fi
