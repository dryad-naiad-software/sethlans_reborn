#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# Verify the worker production Docker image boots cleanly through
# its critical-path startup phases AND, with no
# SETHLANS_WORKER_ENROLLMENT_KEY and no saved token, keeps running
# and waits for setup instead of exiting (setup-rebuild FR-WK-7,
# FR-DK-5: it logs "not enrolled; run setup" and checks again every
# 60 s). This is a "ship-readiness" smoke for #144 — narrower than
# HEALTHCHECK validation, which would need a stub manager.
#
# The container gets its worker page password the way the compose
# file gives it: SETHLANS_WORKER_UI_PASSWORD_FILE naming a read-only
# file mounted under /run/secrets/ (as a Compose secret is), mode 600
# and owned by the runtime user (uid 1000). The password is a random
# 20-character throwaway made here (not a real secret) that passes the
# page password rules. The smoke checks that the worker took it: a
# "page password" log line other than "No worker page password is
# set", a log line naming SETHLANS_WORKER_UI_PASSWORD_FILE, a
# ui_password_hash stored under /data, and the password itself in
# neither the logs nor /data.
#
# What this catches:
#   - Missing repo packages (e.g. shared/ — see #157)
#   - Wrong artifact paths (e.g. Caddy at /usr/bin vs source-mode
#     .venv-build path — see #158)
#   - Import-time exceptions in the worker entrypoint chain
#   - Caddy supervisor mis-spawn
#   - Enrollment-start regressions (the unenrolled worker exiting)
#   - The page password file not read, rejected, logged or stored
#     in plain text
#
# What this does NOT catch:
#   - HEALTHCHECK route bugs (e.g. /api/health/ typo). For that we
#     would need a stub manager + valid enrollment key. Out of scope.
#
# The container is started here and removed on exit, with the
# temporary password folder. Needs docker and openssl (both on
# GitHub's Ubuntu runners).
#
# Usage:
#   tools/worker_boot_smoke.sh <image> <container-name> [deadline_seconds]
#
# Exit codes:
#   0 — all checks passed and the container still running
#   1 — a check failed, or the container exited
#   2 — usage error

set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "Usage: $0 <image> <container-name> [deadline_seconds]" >&2
    exit 2
fi

IMAGE="$1"
CONTAINER="$2"
DEADLINE_S="${3:-30}"
UI_SECRET_IN=/run/secrets/worker_ui_password
SECRET_DIR=$(mktemp -d)

cleanup() {
    docker rm -fv "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${SECRET_DIR}" || true
}
trap cleanup EXIT
# A cancelled or timed-out CI step sends SIGTERM/SIGINT: exit so the
# EXIT trap still runs.
trap 'exit 143' INT TERM

# Each line is a regex (extended) that MUST appear in the
# container's combined stdout/stderr before the deadline.
EXPECTED_PATTERNS=(
    "Sethlans Reborn Worker Agent Starting"
    "Generated self-signed TLS certificate"
    "Worker Web UI \(Waitress\) started on http"
    "Spawning Caddy: /app/\.venv-build/caddy/caddy"
    "not enrolled; run setup"
    "SETHLANS_WORKER_UI_PASSWORD_FILE"
)

# The worker page password file. The folder (from mktemp -d) stays
# 700; umask 077 keeps the file 600 from the moment it is written. The
# runner is not uid 1000, so a root container hands the file over;
# cleanup can still delete it because the runner owns the folder.
UI_PASSWORD="$(openssl rand -base64 15)"
chmod 700 "${SECRET_DIR}"
(umask 077 && printf '%s\n' "${UI_PASSWORD}" > "${SECRET_DIR}/worker_ui_password")
if ! docker run --rm --user 0:0 --entrypoint sh -v "${SECRET_DIR}:/x" "${IMAGE}" \
        -c 'chown 1000:1000 /x/worker_ui_password && chmod 0600 /x/worker_ui_password'; then
    echo "FAIL: could not hand the worker page password file to uid 1000" >&2
    exit 1
fi

docker run -d --name "${CONTAINER}" \
    -e SETHLANS_WORKER_UI_PASSWORD_FILE="${UI_SECRET_IN}" \
    -v "${SECRET_DIR}/worker_ui_password:${UI_SECRET_IN}:ro" \
    "${IMAGE}" >/dev/null

# The "page password" lines, without the one that says none is set.
password_set_lines() {
    grep -iE "page password" <<< "$1" | grep -viE "no worker page password" || true
}

all_markers_present() {
    local logs="$1" pattern
    for pattern in "${EXPECTED_PATTERNS[@]}"; do
        grep -qE "${pattern}" <<< "${logs}" || return 1
    done
    [ -n "$(password_set_lines "${logs}")" ]
}

echo "Worker boot smoke: waiting up to ${DEADLINE_S}s for '${CONTAINER}' to wait for setup..."

end=$((SECONDS + DEADLINE_S))
state="unknown"
logs=""
while [ "$SECONDS" -lt "$end" ]; do
    state=$(docker inspect --format='{{.State.Status}}' "${CONTAINER}" 2>/dev/null || echo "missing")
    logs=$(docker logs "${CONTAINER}" 2>&1 || true)
    if [ "${state}" = "exited" ] || all_markers_present "${logs}"; then
        break
    fi
    sleep 2
done

echo "--- container logs ---"
echo "${logs}"
echo "--- end container logs ---"

failed=0
for pattern in "${EXPECTED_PATTERNS[@]}"; do
    if ! grep -qE "${pattern}" <<< "${logs}"; then
        echo "MISSING expected log marker: ${pattern}" >&2
        failed=1
    fi
done
matched=$(password_set_lines "${logs}")
if [ -z "${matched}" ]; then
    echo "MISSING a 'page password' log line (any case) other than 'No worker page password is set'" >&2
    failed=1
else
    echo "Page password log line(s) matched:"
    echo "${matched}"
fi
if grep -qiF "No worker page password is set" <<< "${logs}"; then
    echo "UNEXPECTED: the worker says no page password is set" >&2
    failed=1
fi
if grep -qF -- "${UI_PASSWORD}" <<< "${logs}"; then
    echo "UNEXPECTED: the worker page password appears in the logs" >&2
    failed=1
fi

if [ "${state}" != "running" ]; then
    rc=$(docker inspect --format='{{.State.ExitCode}}' "${CONTAINER}" 2>/dev/null || echo "?")
    echo >&2
    echo "FAIL: container '${CONTAINER}' is '${state}' (rc=${rc}); an unenrolled worker must keep running." >&2
    exit 1
fi

# Only a hash is stored: some file under /data holds ui_password_hash
# and none holds the password (fed on stdin, so it is not on the exec
# command line).
if ! docker exec "${CONTAINER}" grep -rqs ui_password_hash /data; then
    echo "MISSING a stored ui_password_hash under /data" >&2
    failed=1
fi
search_rc=0
printf '%s\n' "${UI_PASSWORD}" | docker exec -i "${CONTAINER}" grep -rqsF -f - /data || search_rc=$?
if [ "${search_rc}" -eq 0 ]; then
    echo "UNEXPECTED: the worker page password is stored in plain text under /data" >&2
    failed=1
elif [ "${search_rc}" -ne 1 ]; then
    echo "FAIL: could not search /data for the password (grep rc=${search_rc})" >&2
    failed=1
fi

docker stop "${CONTAINER}" >/dev/null 2>&1 || true

if [ "${failed}" -ne 0 ]; then
    echo >&2
    echo "FAIL: worker boot smoke: one or more checks failed (see above)." >&2
    exit 1
fi

echo
echo "PASS: worker boot smoke (still running, all ${#EXPECTED_PATTERNS[@]} markers present," \
    "page password taken from the file, only its hash stored)."
exit 0
