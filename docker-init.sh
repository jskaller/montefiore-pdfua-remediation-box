#!/bin/bash
# docker-init.sh
# Runs on every container start.
# 1. Clones veraPDF validation profiles if not already present
# 2. Executes the main command (openclaw gateway run --force)
set -euo pipefail

PROFILES_DIR="/app/workspace/assets/validation_profiles/veraPDF-validation-profiles-integration"
PROFILES_URL="https://github.com/veraPDF/veraPDF-validation-profiles.git"

# ── Clone veraPDF validation profiles if needed ───────────────────────────────

if [ ! -d "${PROFILES_DIR}/.git" ]; then
    echo "[init] veraPDF validation profiles not found — cloning..."
    mkdir -p "$(dirname ${PROFILES_DIR})"
    git clone \
        --branch integration \
        --depth 1 \
        "${PROFILES_URL}" \
        "${PROFILES_DIR}"
    echo "[init] Validation profiles cloned successfully."
else
    echo "[init] Validation profiles already present — skipping clone."
fi

# ── Execute main command ──────────────────────────────────────────────────────
# Default CMD: openclaw gateway run --force
# Override for one-off commands e.g.:
#   docker compose run --rm remediation python3 smoke_test.py

echo "[init] Starting: $@"
exec "$@"
