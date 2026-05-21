#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 2 ]; then echo "usage: run_qpdf_check.sh <qpdf-bin> <pdf>" >&2; exit 2; fi
QPDF="$1"; PDF="$2"
"$QPDF" --check "$PDF"
