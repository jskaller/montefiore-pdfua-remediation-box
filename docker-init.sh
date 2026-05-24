#!/bin/bash
# docker-init.sh
# Runs on every container start before the main command.
# Clones veraPDF validation profiles if not already present in the
# workspace volume. This handles the case where the host workspace
# directory is empty on first run.
set -euo pipefail

PROFILES_DIR="/app/workspace/assets/validation_profiles/veraPDF-validation-profiles-integration"
PROFILES_URL="https://github.com/veraPDF/veraPDF-validation-profiles.git"

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

# Execute the main command
exec "$@"
