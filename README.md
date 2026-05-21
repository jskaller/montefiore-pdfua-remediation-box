# OpenClaw PDF/UA Remediation Environment

This repository contains an automated, containerized OpenClaw environment for executing PDF/UA and WCAG 2.2 remediation pipelines.

## First-Time Setup

Run these commands in your terminal to configure your local workspace and start the engine.

**1. Create your local working directories:**
mkdir -p ~/Desktop/remediation_jobs
mkdir -p openclaw_home

**2. Copy the configuration template:**
cp openclaw.json.template openclaw_home/openclaw.json

**3. Build the container:**
docker compose build

**4. Launch the engine:**
docker compose run --rm remediator


*Note: On your first run, the engine will drop an authentication link onto your Desktop inside `remediation_jobs/auth_link.txt`. Follow that link to authorize your API key.*
