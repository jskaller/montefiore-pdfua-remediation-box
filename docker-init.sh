#!/bin/bash
# docker-init.sh
# Runs on every container start.
# 1. Clones veraPDF validation profiles if not already present
# 2. Copies AGENTS.md from code tree into workspace (overrides OpenClaw default)
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

# ── Copy AGENTS.md from code tree into workspace ──────────────────────────────
# OpenClaw writes its own AGENTS.md template to workspace/ on first init.
# We overwrite it with our remediation-specific gate sequence every start
# to ensure the agent always has the correct instructions.

if [ -f "/app/AGENTS.md" ]; then
    cp /app/AGENTS.md /app/workspace/AGENTS.md
    echo "[init] AGENTS.md copied to workspace."
fi

# ── Execute main command ──────────────────────────────────────────────────────

echo "[init] Starting: $@"
exec "$@"
