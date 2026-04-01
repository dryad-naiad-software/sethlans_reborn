#!/usr/bin/env bash
#
# SYNOPSIS
#     Starts the Sethlans Manager server.
#
# DESCRIPTION
#     Runs database migrations and starts the Django development server
#     on the configured port (default: 7075).
#
#     Requires manager.ini to exist. Run setup-manager.sh first if this
#     is a fresh installation.
#
# NOTES
#     Author: Sethlans Reborn Development
#     Last Modified: 2026-04-01
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
MANAGE_PY="$PROJECT_ROOT/manager/manage.py"
CONFIG_FILE="$PROJECT_ROOT/manager/manager.ini"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[ERROR] manager.ini not found. Run setup-manager.sh first."
    exit 1
fi

echo "--- Applying migrations ---"
python "$MANAGE_PY" migrate

echo ""
echo "--- Starting Sethlans Manager ---"
python "$MANAGE_PY" runserver 7075
