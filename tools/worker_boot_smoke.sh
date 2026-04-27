#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# Verify the worker production Docker image boots cleanly through
# its critical-path startup phases AND exits with the expected
# enrollment-config error code when no SETHLANS_WORKER_ENROLLMENT_KEY
# is provided. This is a "ship-readiness" smoke for #144 —
# specifically narrower than HEALTHCHECK validation because the
# worker is designed to refuse to start without enrollment config
# (production deployment expects the env var via docker-compose),
# so polling for HEALTHCHECK=healthy without standing up a real
# stub manager would never succeed.
#
# What this catches:
#   - Missing repo packages (e.g. shared/ — see #157)
#   - Wrong artifact paths (e.g. Caddy at /usr/bin vs source-mode
#     .venv-build path — see #158)
#   - Import-time exceptions in the worker entrypoint chain
#   - Caddy supervisor mis-spawn
#   - Setup gate / wizard regressions that change the unattended
#     exit path
#
# What this does NOT catch:
#   - HEALTHCHECK route bugs (e.g. /api/health/ typo). For that we
#     would need a stub manager + valid enrollment key. Out of scope.
#
# Usage:
#   tools/worker_boot_smoke.sh <container-name> [deadline_seconds]
#
# Exit codes:
#   0 — all expected log markers present, container exited rc=4
#   1 — missing markers, unexpected rc, or container did not exit
#   2 — usage error

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <container-name> [deadline_seconds]" >&2
    exit 2
fi

CONTAINER="$1"
DEADLINE_S="${2:-30}"

# Each line is a regex (extended) that MUST appear in the
# container's combined stdout/stderr by the time it exits.
# Order matters only for diagnostic output; the search is
# whole-log substring match.
EXPECTED_PATTERNS=(
    "Sethlans Reborn Worker Agent Starting"
    "Generated self-signed TLS certificate"
    "Worker Web UI \(Waitress\) started on http"
    "Spawning Caddy: /app/\.venv-build/caddy/caddy"
    "Unattended enrollment requires SETHLANS_WORKER_ENROLLMENT_KEY"
)

# Expected exit code from the unattended-key-missing branch.
EXPECTED_RC=4

echo "Worker boot smoke: waiting up to ${DEADLINE_S}s for '${CONTAINER}' to exit..."

end=$((SECONDS + DEADLINE_S))
state="unknown"
while [ "$SECONDS" -lt "$end" ]; do
    if state=$(
        docker inspect \
            --format='{{.State.Status}}' \
            "${CONTAINER}" 2>/dev/null
    ); then
        if [ "${state}" = "exited" ]; then
            break
        fi
    else
        echo "Container '${CONTAINER}' not found (yet?); retrying..."
        state="missing"
    fi
    sleep 2
done

# Always dump logs for diagnostics, regardless of pass/fail.
echo "--- container logs ---"
docker logs "${CONTAINER}" 2>&1 || echo "(could not fetch logs)"
echo "--- end container logs ---"

if [ "${state}" != "exited" ]; then
    echo >&2
    echo "FAIL: container '${CONTAINER}' did not exit within ${DEADLINE_S}s; final state='${state}'." >&2
    docker stop "${CONTAINER}" >/dev/null 2>&1 || true
    exit 1
fi

# Re-fetch logs into a variable for grep (avoid double docker logs call).
logs=$(docker logs "${CONTAINER}" 2>&1)

missing=0
for pattern in "${EXPECTED_PATTERNS[@]}"; do
    if ! echo "${logs}" | grep -qE "${pattern}"; then
        echo "MISSING expected log marker: ${pattern}" >&2
        missing=1
    fi
done

rc=$(
    docker inspect \
        --format='{{.State.ExitCode}}' \
        "${CONTAINER}" 2>/dev/null || echo "?"
)

if [ "${missing}" -ne 0 ]; then
    echo >&2
    echo "FAIL: worker boot smoke missing one or more expected log markers." >&2
    exit 1
fi

if [ "${rc}" != "${EXPECTED_RC}" ]; then
    echo >&2
    echo "FAIL: worker exited rc=${rc}, expected rc=${EXPECTED_RC}." >&2
    echo "All boot markers were present, but the exit branch shifted —" >&2
    echo "review whether the worker enrollment flow has changed." >&2
    exit 1
fi

echo
echo "PASS: worker boot smoke (rc=${rc}, all ${#EXPECTED_PATTERNS[@]} markers present)."
exit 0
