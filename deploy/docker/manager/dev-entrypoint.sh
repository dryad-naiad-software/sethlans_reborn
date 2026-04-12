#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

set -e

# Re-install frontend deps if the named volume is empty (first run) or
# node_modules is missing. The named volume (frontend-node-modules) creates
# the directory but leaves it empty on first run.
if [ ! -d /app/frontend/node_modules ] || [ -z "$(ls -A /app/frontend/node_modules 2>/dev/null)" ]; then
    echo "--- Installing frontend dependencies... ---"
    npm ci --prefix /app/frontend
fi

# Start Angular dev server in the background
# Note: init: true in the compose file ensures this process receives SIGTERM on shutdown
echo "--- Starting Angular dev server on :4200 ---"
npx --prefix /app/frontend ng serve \
    --host 0.0.0.0 \
    --port 4200 \
    --proxy-config /app/frontend/proxy.conf.json &

# Start Django manager in dev mode (foreground)
echo "--- Starting Sethlans Manager (DEV) on :8080 ---"
exec python run_manager.py --dev
