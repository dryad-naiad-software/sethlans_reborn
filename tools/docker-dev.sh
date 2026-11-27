#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# -----------------------------------------------------------------------
# Docker development convenience script.
#
# Usage:
#   ./tools/docker-dev.sh up        Start the dev stack (builds if needed)
#   ./tools/docker-dev.sh down      Stop the dev stack
#   ./tools/docker-dev.sh down -v   Stop the dev stack and remove volumes
#   ./tools/docker-dev.sh exec <service> <cmd>  Run a command in a container
#   ./tools/docker-dev.sh logs      Tail logs from all services
#
# This script wraps docker compose with the correct -f flags so it works
# from any CWD within the repo.
# -----------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"

COMPOSE_BASE="$PROJECT_ROOT/deploy/docker/docker-compose.yml"
COMPOSE_DEV="$PROJECT_ROOT/deploy/docker/docker-compose.dev.yml"

# The production compose file uses ${SETHLANS_SECURITY_SECRET_KEY:?...} which
# is evaluated at YAML parse time BEFORE the dev override's static value is
# merged. Export a dev-only value so compose parsing succeeds.
export SETHLANS_SECURITY_SECRET_KEY="dev-insecure-key-for-local-development-only"

# The manager exits 12 without an admin account. The dev compose file
# takes the first admin's password from this file (a Compose secret);
# make it once with a random password. The folder is owner-only (700),
# which keeps other host users out. The file itself must be readable by
# the container's uid 1000, which is often not the host user (another
# Linux uid, rootless Docker, Docker Desktop), so it ends up 644; the
# Compose secret is a bind mount of the file, so the container never
# passes through the 700 folder.
DEV_ADMIN_DIR="$PROJECT_ROOT/.tmp/docker-dev"
export SETHLANS_DEV_ADMIN_PASSWORD_FILE="$DEV_ADMIN_DIR/admin_password"
export SETHLANS_ADMIN_USERNAME="${SETHLANS_ADMIN_USERNAME:-admin}"

ensure_dev_admin_password() {
    mkdir -p "$(dirname "$DEV_ADMIN_DIR")"
    (umask 077 && mkdir -p "$DEV_ADMIN_DIR")
    chmod 700 "$DEV_ADMIN_DIR"
    if [ -s "$SETHLANS_DEV_ADMIN_PASSWORD_FILE" ]; then
        return
    fi
    (umask 077 && head -c 18 /dev/urandom | base64 | tr -d '\n' > "$SETHLANS_DEV_ADMIN_PASSWORD_FILE")
    chmod 644 "$SETHLANS_DEV_ADMIN_PASSWORD_FILE"
}

# The manager and worker images COPY a pre-fetched Caddy binary from
# .tmp/caddy/linux-<arch>/ in the build context (same as CI).
CADDY_DIR="$PROJECT_ROOT/.tmp/caddy"

ensure_caddy_binaries() {
    if [ -x "$CADDY_DIR/linux-amd64/caddy" ] && [ -x "$CADDY_DIR/linux-arm64/caddy" ]; then
        return
    fi
    local python_bin
    python_bin="$(command -v python || command -v python3 || true)"
    if [ -z "$python_bin" ]; then
        echo "[ERROR] Neither python nor python3 found; needed to fetch Caddy" >&2
        exit 1
    fi
    echo "--- Fetching Caddy binaries into $CADDY_DIR ---"
    "$python_bin" "$SCRIPT_DIR/fetch_caddy.py" --target-dir "$CADDY_DIR" \
        --multi-arch linux-amd64,linux-arm64
}

usage() {
    echo "Usage: $0 <command> [args...]"
    echo ""
    echo "  up          Start the dev stack (docker compose up --build)"
    echo "  down        Stop the dev stack"
    echo "  down -v     Stop the dev stack and remove volumes"
    echo "  exec        Run a command in a running container"
    echo "  logs        Tail logs from all services"
}

if [ $# -eq 0 ]; then
    usage
    exit 1
fi

CMD="$1"
shift

case "$CMD" in
    up)
        ensure_caddy_binaries
        ensure_dev_admin_password
        echo "--- Dev admin: $SETHLANS_ADMIN_USERNAME, password in $SETHLANS_DEV_ADMIN_PASSWORD_FILE ---"
        docker compose \
            -f "$COMPOSE_BASE" \
            -f "$COMPOSE_DEV" \
            --project-directory "$PROJECT_ROOT" \
            up --build "$@"
        ;;
    down)
        docker compose \
            -f "$COMPOSE_BASE" \
            -f "$COMPOSE_DEV" \
            --project-directory "$PROJECT_ROOT" \
            down "$@"
        ;;
    exec)
        docker compose \
            -f "$COMPOSE_BASE" \
            -f "$COMPOSE_DEV" \
            --project-directory "$PROJECT_ROOT" \
            exec "$@"
        ;;
    logs)
        docker compose \
            -f "$COMPOSE_BASE" \
            -f "$COMPOSE_DEV" \
            --project-directory "$PROJECT_ROOT" \
            logs -f "$@"
        ;;
    *)
        echo "Unknown command: $CMD"
        usage
        exit 1
        ;;
esac
