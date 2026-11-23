#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# Boot smoke for the manager production Docker image (issue #296).
#
# Starts the image the way a deployment would (random secret key,
# named volumes for /data, /config and /certs, port 8080 published on
# loopback) and checks:
#
#   1. Docker's own HEALTHCHECK reports "healthy" (Caddy on 8080 in
#      front of the manager) within the deadline.
#   2. https://127.0.0.1:<mapped port>/api/health/ answers from the
#      runner, and Caddy serves the cert the manager keeps in
#      /data/tls/cert.pem.
#   3. `docker stop` (SIGTERM) stops Caddy and the manager cleanly:
#      exit code 0, not a SIGKILL after the stop timeout.
#   4. After `docker start`, the same cert is served (it persists in
#      the /data volume), and killing the manager process makes the
#      container exit (code 137) instead of leaving Caddy running alone.
#
# The container and volumes are always removed; logs are printed on
# failure. Needs docker, curl and openssl (all on GitHub's Ubuntu
# runners). No GPU.
#
# Usage:
#   tools/manager_boot_smoke.sh <image> [deadline_seconds]
#
# Exit codes:
#   0 - all checks passed
#   1 - a check failed
#   2 - usage error

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <image> [deadline_seconds]" >&2
    exit 2
fi

IMAGE="$1"
DEADLINE_S="${2:-180}"
NAME="sethlans-manager-smoke-${GITHUB_RUN_ID:-local}-$$"
VOLUMES=("${NAME}-data" "${NAME}-config" "${NAME}-certs")
# Bounded waits that do not depend on the health-check interval.
HTTPS_WAIT_S=90
EXIT_WAIT_S=30

cleanup() {
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
    docker volume rm "${VOLUMES[@]}" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# A cancelled or timed-out CI step sends SIGTERM/SIGINT: exit so the
# EXIT trap still removes the container and volumes.
trap 'exit 143' INT TERM

fail() {
    echo >&2
    echo "FAIL: $*" >&2
    echo "--- container logs ---"
    docker logs "${NAME}" 2>&1 || echo "(could not fetch logs)"
    echo "--- end container logs ---"
    echo "--- health-check log ---"
    docker inspect --format '{{json .State.Health}}' "${NAME}" 2>&1 || true
    exit 1
}

state_of() {
    docker inspect --format "$1" "${NAME}" 2>/dev/null || echo "missing"
}

https_url() {
    local hostport
    hostport=$(docker port "${NAME}" 8080/tcp | head -n 1)
    echo "https://127.0.0.1:${hostport##*:}/api/health/"
}

served_fingerprint() {
    local hostport
    hostport=$(docker port "${NAME}" 8080/tcp | head -n 1)
    timeout 10 openssl s_client -connect "127.0.0.1:${hostport##*:}" </dev/null 2>/dev/null \
        | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true
}

stored_fingerprint() {
    docker exec "${NAME}" cat /data/tls/cert.pem \
        | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true
}

wait_for_https() {
    local end=$((SECONDS + HTTPS_WAIT_S))
    while [ "${SECONDS}" -lt "${end}" ]; do
        if [ "$(state_of '{{.State.Status}}')" != "running" ]; then
            fail "container stopped while waiting for HTTPS (exit code $(state_of '{{.State.ExitCode}}'))"
        fi
        if curl -fsSk --max-time 5 "$(https_url)" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    fail "HTTPS health endpoint did not answer within ${HTTPS_WAIT_S}s"
}

wait_for_exit() {
    local end=$((SECONDS + EXIT_WAIT_S))
    while [ "${SECONDS}" -lt "${end}" ]; do
        if [ "$(state_of '{{.State.Status}}')" = "exited" ]; then
            return 0
        fi
        sleep 1
    done
    fail "$1"
}

for vol in "${VOLUMES[@]}"; do
    docker volume create "${vol}" >/dev/null
done

# Random per-run key, passed by name so it stays off the command line.
SETHLANS_SECURITY_SECRET_KEY="$(openssl rand -hex 32)"
export SETHLANS_SECURITY_SECRET_KEY

echo "Manager boot smoke: starting ${IMAGE} as ${NAME}"
docker run -d --name "${NAME}" \
    -e SETHLANS_SECURITY_SECRET_KEY \
    -v "${VOLUMES[0]}:/data" \
    -v "${VOLUMES[1]}:/config" \
    -v "${VOLUMES[2]}:/certs" \
    -p 127.0.0.1::8080 \
    "${IMAGE}" >/dev/null || fail "docker run failed"

# 1. Docker HEALTHCHECK must report healthy.
echo "Waiting up to ${DEADLINE_S}s for the Docker health status to become healthy..."
end=$((SECONDS + DEADLINE_S))
health=""
while [ "${SECONDS}" -lt "${end}" ]; do
    status=$(state_of '{{.State.Status}}')
    if [ "${status}" != "running" ]; then
        fail "container is '${status}' (exit code $(state_of '{{.State.ExitCode}}')) before becoming healthy"
    fi
    health=$(state_of '{{if .State.Health}}{{.State.Health.Status}}{{end}}')
    if [ "${health}" = "healthy" ]; then
        break
    fi
    sleep 3
done
[ "${health}" = "healthy" ] || fail "health status '${health}' after ${DEADLINE_S}s, expected 'healthy'"
echo "OK: container is healthy after ${SECONDS}s"

# 2. HTTPS from the runner, and Caddy serves the manager's cert.
body=$(curl -fsSk --max-time 10 "$(https_url)") || fail "GET $(https_url) failed from the runner"
echo "${body}" | grep -q '"boot_id"' || fail "unexpected /api/health/ body: ${body}"
echo "OK: $(https_url) -> ${body}"
fingerprint=$(served_fingerprint)
[ -n "${fingerprint}" ] || fail "could not read the certificate served on port 8080"
[ "${fingerprint}" = "$(stored_fingerprint)" ] \
    || fail "served cert (${fingerprint}) is not /data/tls/cert.pem"
echo "OK: Caddy serves /data/tls/cert.pem (${fingerprint})"

# 3. docker stop -> SIGTERM -> clean exit 0 well inside the timeout.
docker stop -t 20 "${NAME}" >/dev/null || fail "docker stop failed"
rc=$(state_of '{{.State.ExitCode}}')
[ "${rc}" = "0" ] || fail "exit code ${rc} after docker stop, expected 0"
echo "OK: docker stop -> exit code 0"

# 4. Restart: same cert from the volume; then a dead manager ends the container.
docker start "${NAME}" >/dev/null || fail "docker start failed"
wait_for_https
[ "$(served_fingerprint)" = "${fingerprint}" ] || fail "cert changed across a container restart"
echo "OK: restarted; same cert served"
killed=$(docker exec "${NAME}" python -c '
import os, signal
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        continue
    if argv[1:2] == [b"/app/manager/run_manager.py"]:
        os.kill(int(pid), signal.SIGKILL)
        print(pid)
') || true
[ -n "${killed}" ] || fail "no run_manager.py process found in the container"
wait_for_exit "container still running ${EXIT_WAIT_S}s after the manager (pid ${killed}) was killed"
rc=$(state_of '{{.State.ExitCode}}')
[ "${rc}" = "137" ] || fail "exit code ${rc} after the manager was killed, expected 137"
echo "OK: manager killed -> container exited with code 137"

echo
echo "PASS: manager boot smoke"
