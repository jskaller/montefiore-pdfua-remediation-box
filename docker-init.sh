#!/bin/bash
# docker-init.sh
# Runs on every container start.
# 1. Clones veraPDF validation profiles if not already present
# 2. Copies workspace control files from code tree into workspace
#    (overrides OpenClaw defaults which would otherwise lose our config)
# 3. Executes the main command (openclaw gateway run --force)
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

# ── Copy workspace control files from code tree ───────────────────────────────
# OpenClaw writes its own versions of these files to workspace/ on first init.
# We overwrite them on every start to ensure our remediation config is always
# current. Files live in /app/ (code tree) and are copied to /app/workspace/.

for f in AGENTS.md SOUL.md IDENTITY.md TOOLS.md; do
    if [ -f "/app/${f}" ]; then
        cp "/app/${f}" "/app/workspace/${f}"
        echo "[init] Copied ${f} to workspace."
    fi
done

# ── Execute main command ──────────────────────────────────────────────────────

echo "[init] Starting: $@"
exec "$@"
