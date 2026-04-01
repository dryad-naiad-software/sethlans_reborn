#!/usr/bin/env bash
#
# SYNOPSIS
#     Starts the Sethlans Worker Agent.
#
# DESCRIPTION
#     Starts the worker agent which connects to the manager, enrolls
#     if needed, downloads required Blender versions, and polls for jobs.
#
#     Requires worker/config.ini with the manager host/port and either
#     an API token or enrollment key.
#
# NOTES
#     Author: Sethlans Reborn Development
#     Last Modified: 2026-04-01
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
WORKER_DIR="$PROJECT_ROOT/worker"
CONFIG_FILE="$WORKER_DIR/config.ini"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[ERROR] worker/config.ini not found."
    echo ""
    echo "Create it from the example:"
    echo "  cp $WORKER_DIR/config.ini.example $CONFIG_FILE"
    echo ""
    echo "Then set the enrollment_key from the manager setup output."
    exit 1
fi

echo "--- Starting Sethlans Worker Agent ---"
python "$WORKER_DIR/run_worker.py"
