#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# Poll a Docker container's HEALTHCHECK status until it reports
# "healthy", or fail with logs after a deadline.
#
# Used by `.github/workflows/docker-test.yml` to validate that the
# worker-cpu image's HEALTHCHECK actually reaches the worker's
# /api/health/ endpoint (#144). Reusable for the nvidia/rocm worker
# images and any future containerized service that publishes a
# HEALTHCHECK.
#
# Usage:
#   tools/wait_for_docker_health.sh <container-name> [deadline_seconds]
#
# Exit codes:
#   0 — container reported "healthy" within the deadline
#   1 — timeout, "unhealthy", or container disappeared
#   2 — usage error

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <container-name> [deadline_seconds]" >&2
    exit 2
fi

CONTAINER="$1"
DEADLINE_S="${2:-180}"
POLL_INTERVAL_S=5

echo "Waiting up to ${DEADLINE_S}s for container '${CONTAINER}' to report healthy..."

end=$((SECONDS + DEADLINE_S))
while [ "$SECONDS" -lt "$end" ]; do
    if ! status=$(
        docker inspect \
            --format='{{.State.Health.Status}}' \
            "${CONTAINER}" 2>/dev/null
    ); then
        echo "Container '${CONTAINER}' not found (yet?); retrying..."
        sleep "${POLL_INTERVAL_S}"
        continue
    fi

    case "${status}" in
        healthy)
            echo "Container '${CONTAINER}' reported healthy after ${SECONDS}s."
            exit 0
            ;;
        unhealthy)
            echo "Container '${CONTAINER}' reported UNHEALTHY — failing fast." >&2
            echo "--- container logs ---" >&2
            docker logs "${CONTAINER}" >&2 || true
            exit 1
            ;;
        starting|"")
            # Still inside HEALTHCHECK --start-period; keep polling.
            echo "  status=${status:-<empty>} (elapsed ${SECONDS}s)"
            ;;
        *)
            echo "  unexpected status=${status} (elapsed ${SECONDS}s)"
            ;;
    esac

    sleep "${POLL_INTERVAL_S}"
done

echo "TIMEOUT after ${DEADLINE_S}s; last status='${status:-unknown}'." >&2
echo "--- container logs ---" >&2
docker logs "${CONTAINER}" >&2 || true
exit 1
