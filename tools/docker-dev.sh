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
