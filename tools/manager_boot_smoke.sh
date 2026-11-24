#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# Boot smoke for the manager production Docker image (#296, #297, #298).
#
# Starts the image the way a deployment would (random secret key, named
# volumes for /data, /config and /certs, the port published on loopback)
# and checks, in order:
#
#   A. First container, with the image's own HEALTHCHECK settings:
#      Docker reports "healthy"; https://127.0.0.1:<mapped>/api/health/
#      answers from the runner; Caddy serves /data/tls/cert.pem; the
#      FFmpeg parts check uses the copy in /data and nothing lands in the
#      runtime user's home; /data is mode 700; the loopback-only status
#      endpoint answers 200 with a JSON object; `docker stop` exits 0.
#   B. Recreate (#297): `docker rm`, then a new container on the same
#      volumes. Healthy again, same cert fingerprint, same FFmpeg file
#      (same inode: not downloaded again), state files still in /data,
#      loopback status endpoint answers again. Killing the manager
#      process ends the container (exit 137).
#   C. Other port (#298): SETHLANS_MANAGER_PORT=9443 -> healthy, and
#      HTTPS answers on the mapped 9443.
#   D. Root-owned bind mount (#297): /data on a host folder owned by
#      root -> the container exits 3 with the chown fix, no traceback.
#
# FFmpeg: before the first start a stand-in `ffmpeg` (a shell script
# that answers `-version`) is put where the image says the bundled copy
# lives. This keeps the smoke offline and fast (no ~150 MB download, no
# dependency on the upstream build), and makes "found the copy in /data"
# checkable: the file's inode must not change, and nothing may appear
# under the runtime user's home.
#
# Containers, volumes and the temporary host folder are always removed;
# logs are printed on failure. Needs docker, curl, openssl and timeout
# (all on GitHub's Ubuntu runners). No GPU.
#
# Usage:
#   tools/manager_boot_smoke.sh <image> [deadline_seconds]
#
# Exit codes: 0 all checks passed, 1 a check failed, 2 usage error.

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <image> [deadline_seconds]" >&2
    exit 2
fi

IMAGE="$1"
DEADLINE_S="${2:-180}"
PREFIX="sethlans-manager-smoke-${GITHUB_RUN_ID:-local}-$$"
VOLUMES=("${PREFIX}-data" "${PREFIX}-config" "${PREFIX}-certs")
VOLUME_ARGS=(-v "${VOLUMES[0]}:/data" -v "${VOLUMES[1]}:/config" -v "${VOLUMES[2]}:/certs")
# Faster probes for the later containers; the first one keeps the
# image's own HEALTHCHECK timing.
FAST_HEALTH=(--health-interval 5s --health-start-period 60s)
HTTPS_WAIT_S=90
EXIT_WAIT_S=30
LOG_WAIT_S=90
HOME_STATE=/home/sethlans/.local/share/sethlans
CUR=""        # container the helpers below act on
CPORT=8080    # container port Caddy serves on for CUR
HOST_DIR=""   # root-owned host folder for step D

remove_host_dir() {
    [ -n "${HOST_DIR}" ] && [ -d "${HOST_DIR}" ] || return 0
    # Root-owned: empty it and hand it back from a root container that
    # mounts only this folder, then remove it on the host.
    docker run --rm --user 0:0 --entrypoint sh -v "${HOST_DIR}:/x" "${IMAGE}" \
        -c "find /x -mindepth 1 -delete && chown $(id -u):$(id -g) /x" >/dev/null 2>&1 || true
    rm -rf "${HOST_DIR}" || true
    HOST_DIR=""
}

cleanup() {
    local n
    for n in a b c d; do
        # -v: also drop the anonymous volumes of image VOLUMEs left unmounted.
        docker rm -fv "${PREFIX}-${n}" >/dev/null 2>&1 || true
    done
    docker volume rm "${VOLUMES[@]}" >/dev/null 2>&1 || true
    remove_host_dir
}
trap cleanup EXIT
# A cancelled or timed-out CI step sends SIGTERM/SIGINT: exit so the
# EXIT trap still runs.
trap 'exit 143' INT TERM

fail() {
    echo >&2
    echo "FAIL: $*" >&2
    if [ -n "${CUR}" ]; then
        echo "--- logs of ${CUR} ---"
        docker logs "${CUR}" 2>&1 || echo "(could not fetch logs)"
        echo "--- end logs ---"
        echo "--- health-check log ---"
        docker inspect --format '{{json .State.Health}}' "${CUR}" 2>&1 || true
    fi
    exit 1
}

state_of() {
    docker inspect --format "$1" "${CUR}" 2>/dev/null || echo "missing"
}

host_port() {
    local hostport
    hostport=$(docker port "${CUR}" "${CPORT}/tcp" | head -n 1)
    echo "${hostport##*:}"
}

https_url() {
    echo "https://127.0.0.1:$(host_port)/api/health/"
}

served_fingerprint() {
    timeout 10 openssl s_client -connect "127.0.0.1:$(host_port)" </dev/null 2>/dev/null \
        | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true
}

stored_fingerprint() {
    docker exec "${CUR}" cat /data/tls/cert.pem \
        | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true
}

start_container() {  # <suffix> <container port> [extra docker run args...]
    CUR="${PREFIX}-$1"
    CPORT="$2"
    shift 2
    docker run -d --name "${CUR}" -e SETHLANS_SECURITY_SECRET_KEY \
        "${VOLUME_ARGS[@]}" -p "127.0.0.1::${CPORT}" "$@" "${IMAGE}" >/dev/null \
        || fail "docker run failed"
}

wait_healthy() {
    local end=$((SECONDS + DEADLINE_S)) status health=""
    echo "Waiting up to ${DEADLINE_S}s for ${CUR} to become healthy..."
    while [ "${SECONDS}" -lt "${end}" ]; do
        status=$(state_of '{{.State.Status}}')
        if [ "${status}" != "running" ]; then
            fail "${CUR} is '${status}' (exit code $(state_of '{{.State.ExitCode}}')) before becoming healthy"
        fi
        health=$(state_of '{{if .State.Health}}{{.State.Health.Status}}{{end}}')
        if [ "${health}" = "healthy" ]; then
            echo "OK: ${CUR} healthy (t=${SECONDS}s)"
            return 0
        fi
        sleep 3
    done
    fail "health status '${health}' after ${DEADLINE_S}s, expected 'healthy'"
}

check_https() {
    local body
    body=$(curl -fsSk --max-time 10 "$(https_url)") || fail "GET $(https_url) failed from the runner"
    grep -q '"boot_id"' <<< "${body}" || fail "unexpected /api/health/ body: ${body}"
    echo "OK: $(https_url) -> ${body}"
}

wait_for_exit() {
    local end=$((SECONDS + EXIT_WAIT_S))
    while [ "${SECONDS}" -lt "${end}" ]; do
        [ "$(state_of '{{.State.Status}}')" = "exited" ] && return 0
        sleep 1
    done
    fail "$1"
}

# Wait for this container's FFmpeg parts-check result and require that
# it used the bundled copy (the stand-in in /data), not a download.
check_ffmpeg_from_data() {
    local end=$((SECONDS + LOG_WAIT_S)) line=""
    while [ "${SECONDS}" -lt "${end}" ]; do
        line=$(docker logs "${CUR}" 2>&1 | grep -m 1 "parts_check: ffmpeg -> status=" || true)
        [ -n "${line}" ] && break
        sleep 2
    done
    [ -n "${line}" ] || fail "no FFmpeg parts-check result in the logs within ${LOG_WAIT_S}s"
    grep -q "status=ready source=bundled" <<< "${line}" \
        || fail "FFmpeg parts check did not use the copy in /data: ${line}"
    docker exec "${CUR}" test ! -e "${HOME_STATE}" \
        || fail "manager state was written under ${HOME_STATE} instead of /data"
    echo "OK: FFmpeg found in /data; nothing under the home folder"
}

ffmpeg_inode() {
    docker exec "${CUR}" stat -c %i "${FFMPEG_PATH}" 2>/dev/null || echo "missing"
}

# Ask the manager's loopback-only status endpoint (internal Waitress
# listener, reachable only from inside the container). The health
# checks reach only the public listener through Caddy, so this is the
# one check that the internal listener came up and serves its route.
check_loopback_status() {
    local out
    out=$(docker exec "${CUR}" python -c '
import json, sys, urllib.request
sys.path[:0] = ["/app/manager", "/app"]
from sethlans_manager.tls_setup import get_config_file_path
from sethlans_manager.waitress_config import get_waitress_internal_port
port = get_waitress_internal_port(get_config_file_path())
url = f"http://127.0.0.1:{port}/api/status/public/"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(url, timeout=10) as resp:
        code = resp.status
        body = resp.read().decode("utf-8", "replace")
except OSError as exc:
    sys.exit(f"GET {url} failed: {exc}")
if code != 200:
    sys.exit(f"GET {url} answered HTTP {code}, expected 200: {body[:200]!r}")
try:
    data = json.loads(body)
except ValueError:
    data = None
if not isinstance(data, dict):
    sys.exit(f"GET {url} did not return a JSON object: {body[:200]!r}")
print(url)
' 2>&1) || fail "loopback status: ${out}"
    echo "OK: ${out} answers 200 with a JSON object"
}

for vol in "${VOLUMES[@]}"; do
    docker volume create "${vol}" >/dev/null
done

# Random per-run key, passed by name so it stays off the command line.
SETHLANS_SECURITY_SECRET_KEY="$(openssl rand -hex 32)"
export SETHLANS_SECURITY_SECRET_KEY

# Put the FFmpeg stand-in where the image itself says the bundled copy
# lives (so the path must resolve under /data).
FFMPEG_PATH=$(docker run --rm --entrypoint python -v "${VOLUMES[0]}:/data" "${IMAGE}" -c '
import sys
sys.path[:0] = ["/app", "/app/manager"]
from shared.frozen_paths import get_data_dir
from workers.services.parts_check.ffmpeg_download_pkg.locate import get_ffmpeg_dir
target = get_ffmpeg_dir(get_data_dir("manager"))
target.mkdir(parents=True, exist_ok=True)
stub = target / "ffmpeg"
stub.write_text("#!/bin/sh\necho ffmpeg version 0-smoke-stand-in\n")
stub.chmod(0o755)
print(stub)
') || fail "could not place the FFmpeg stand-in"
case "${FFMPEG_PATH}" in
    /data/*) echo "FFmpeg stand-in: ${FFMPEG_PATH}" ;;
    *) fail "image resolves the bundled FFmpeg outside /data: ${FFMPEG_PATH}" ;;
esac

# --- A. first container -------------------------------------------------
echo "== A: first start of ${IMAGE}"
start_container a 8080
wait_healthy
check_https
fingerprint=$(served_fingerprint)
[ -n "${fingerprint}" ] || fail "could not read the certificate served on port 8080"
[ "${fingerprint}" = "$(stored_fingerprint)" ] || fail "served cert (${fingerprint}) is not /data/tls/cert.pem"
echo "OK: Caddy serves /data/tls/cert.pem (${fingerprint})"
check_ffmpeg_from_data
inode=$(ffmpeg_inode)
mode=$(docker exec "${CUR}" stat -c %a /data 2>/dev/null || echo "?")
[ "${mode}" = "700" ] || fail "/data has mode ${mode}, expected 700"
echo "OK: /data is mode 700"
check_loopback_status
docker stop -t 20 "${CUR}" >/dev/null || fail "docker stop failed"
rc=$(state_of '{{.State.ExitCode}}')
[ "${rc}" = "0" ] || fail "exit code ${rc} after docker stop, expected 0"
echo "OK: docker stop -> exit code 0"

# --- B. recreate on the same volumes ------------------------------------
echo "== B: new container on the same volumes"
docker rm -v "${CUR}" >/dev/null || fail "docker rm failed"
start_container b 8080 "${FAST_HEALTH[@]}"
wait_healthy
[ "$(served_fingerprint)" = "${fingerprint}" ] || fail "cert fingerprint changed in the new container"
echo "OK: same cert fingerprint after recreate"
check_ffmpeg_from_data
[ "$(ffmpeg_inode)" = "${inode}" ] || fail "FFmpeg in /data was replaced (inode ${inode} -> $(ffmpeg_inode))"
docker exec "${CUR}" sh -c 'test -s /data/db.sqlite3 && test -s /data/tls/key.pem && ls /data/logs/*.log >/dev/null' \
    || fail "state files missing from /data after recreate"
echo "OK: FFmpeg not downloaded again; database, key and logs in /data"
check_loopback_status
killed=$(docker exec "${CUR}" python -c '
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
docker rm -v "${CUR}" >/dev/null || fail "docker rm failed"

# --- C. another port ----------------------------------------------------
echo "== C: SETHLANS_MANAGER_PORT=9443"
start_container c 9443 -e SETHLANS_MANAGER_PORT=9443 "${FAST_HEALTH[@]}"
wait_healthy
check_https
docker rm -fv "${CUR}" >/dev/null || fail "docker rm failed"

# --- D. root-owned bind mount -------------------------------------------
echo "== D: /data bind-mounted from a root-owned host folder"
HOST_DIR=$(mktemp -d)
# Same state Docker leaves a missing bind-mount folder in: root, 0755.
docker run --rm --user 0:0 --entrypoint sh -v "${HOST_DIR}:/x" "${IMAGE}" \
    -c 'chown 0:0 /x && chmod 0755 /x' || fail "could not make ${HOST_DIR} root-owned"
CUR="${PREFIX}-d"
set +e
timeout 60 docker run --name "${CUR}" -e SETHLANS_SECURITY_SECRET_KEY \
    -v "${HOST_DIR}:/data" "${IMAGE}" >/dev/null 2>&1
rc=$?
set -e
logs=$(docker logs "${CUR}" 2>&1 || true)
[ "${rc}" = "3" ] || fail "exit code ${rc} with a root-owned /data, expected 3"
grep -q "sudo chown -R 1000:1000" <<< "${logs}" || fail "no chown fix in the message"
if grep -q "Traceback" <<< "${logs}"; then
    fail "traceback printed for a root-owned /data"
fi
echo "OK: root-owned /data -> exit 3 with: $(grep -m 1 'chown' <<< "${logs}")"
docker rm -fv "${CUR}" >/dev/null || fail "docker rm failed"
remove_host_dir

echo
echo "PASS: manager boot smoke"
