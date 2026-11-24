#!/usr/bin/env sh
# ==============================================================================
# Enterprise Knowledge Assistant — Container Healthcheck Script
# Probes the FastAPI health endpoint: http://localhost:8000/api/v1/health
# ==============================================================================
set -e

HEALTH_URL="${HEALTH_URL:-http://localhost:8000/api/v1/health}"

if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null
    exit 0
elif command -v python3 >/dev/null 2>&1; then
    python3 -c "
import sys, urllib.request
try:
    with urllib.request.urlopen('${HEALTH_URL}', timeout=5) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
"
elif command -v wget >/dev/null 2>&1; then
    wget -q -T 5 -O - "$HEALTH_URL" >/dev/null
    exit 0
else
    echo "Error: Neither curl, python3, nor wget available for healthcheck." >&2
    exit 1
fi
