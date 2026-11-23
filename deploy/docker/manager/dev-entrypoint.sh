#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

set -e

# Re-install frontend deps if the named volume is empty (first run) or
# node_modules is missing. The named volume (frontend-node-modules) creates
# the directory but leaves it empty on first run.
if [ ! -d /app/manager/frontend/node_modules ] || [ -z "$(ls -A /app/manager/frontend/node_modules 2>/dev/null)" ]; then
    echo "--- Installing frontend dependencies... ---"
    npm ci --prefix /app/manager/frontend
fi

# Start Angular dev server in the background
# Note: init: true in the compose file ensures this process receives SIGTERM on shutdown
echo "--- Starting Angular dev server on :4200 ---"
npx --prefix /app/manager/frontend ng serve \
    --host 0.0.0.0 \
    --port 4200 \
    --proxy-config /app/manager/frontend/proxy.conf.json &

# Start Caddy (TLS on :8080) and the manager in dev mode (foreground).
# The container entrypoint passes --dev on to run_manager.py.
echo "--- Starting Sethlans Manager (DEV) on :8080 ---"
exec python /app/launcher/container_entrypoint.py --dev
