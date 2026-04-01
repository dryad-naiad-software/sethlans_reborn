#!/usr/bin/env bash
#
# SYNOPSIS
#     Builds the Angular frontend and collects static files.
#
# DESCRIPTION
#     Installs npm dependencies (if needed), runs the Angular production
#     build, and collects static files for WhiteNoise serving.
#
# NOTES
#     Author: Sethlans Reborn Development
#     Last Modified: 2026-04-01
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
FRONTEND_DIR="$PROJECT_ROOT/manager/frontend"
MANAGE_PY="$PROJECT_ROOT/manager/manage.py"

if [ ! -d "$FRONTEND_DIR" ]; then
    echo "[ERROR] Frontend directory not found at $FRONTEND_DIR"
    exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "--- Installing frontend dependencies ---"
    npm install --prefix "$FRONTEND_DIR"
fi

echo "--- Building Angular frontend ---"
npm run build --prefix "$FRONTEND_DIR"
echo "[OK] Frontend built"

echo ""
echo "--- Collecting static files ---"
python "$MANAGE_PY" collectstatic --noinput
echo "[OK] Static files collected"
