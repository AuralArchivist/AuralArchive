#!/bin/bash
# Development server: restarts on .py changes, with Flask debug mode on (local use only).
# Usage: bash dev/dev.sh (from app/)

set -e

# app.py is one level up; the venv is at the repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$APP_DIR"

export FLASK_DEBUG=true

# Activate venv if present and not already active
if [ -z "$VIRTUAL_ENV" ] && [ -f "../venv/bin/activate" ]; then
    source ../venv/bin/activate
elif [ -z "$VIRTUAL_ENV" ] && [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

echo "--------------------------------------------"
echo "  AuralArchive - dev mode"
echo "  Watching *.py for changes (auto-restart)"
echo "  FLASK_DEBUG=true (local dev only)"
echo "  Press Ctrl+C to stop"
echo "--------------------------------------------"

exec watchmedo auto-restart \
    --patterns="*.py" \
    --ignore-patterns="*/.venv/*;*/__pycache__/*;*/data/*" \
    --recursive \
    --signal SIGTERM \
    -- python app.py
