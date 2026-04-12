#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Thin wrapper around scripts/test.py so muscle memory works:
#   ./scripts/test.sh unit
#   ./scripts/test.sh fast -x
#   ./scripts/test.sh full --verbose
#
# Locates the project venv Python itself so the wrapper works from any
# cwd and without requiring a globally-activated venv.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -x "$PROJECT_ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON="$PROJECT_ROOT/.venv/Scripts/python.exe"
elif [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    echo "ERROR: Could not find venv Python under $PROJECT_ROOT/.venv/" >&2
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/test.py" "$@"
