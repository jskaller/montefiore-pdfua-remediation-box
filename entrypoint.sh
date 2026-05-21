#!/bin/bash
set -e

CONFIG_PATH="/root/.openclaw/openclaw.json"
SKILLS_LINK="/root/.openclaw/workspace/skills/montefiore-pdfua-unified-v6"

# 1. Enforce the workspace folder structure and custom skill symlink
mkdir -p /root/.openclaw/workspace/skills

if [ ! -L "$SKILLS_LINK" ] && [ ! -d "$SKILLS_LINK" ]; then
    echo "🔗 Mapping custom Montefiore pipeline into runtime engine..."
    ln -s /app/workspace/skills/montefiore-pdfua-unified-v6 "$SKILLS_LINK"
fi

# 2. Check if configuration file exists and contains data
if [ -s "$CONFIG_PATH" ]; then
    echo "✨ Valid OpenClaw configuration found. Launching remediation engine..."
    exec openclaw chat
else
    echo "🛑 No active configuration found. Launching security onboarding wizard..."
    exec openclaw onboard
fi
