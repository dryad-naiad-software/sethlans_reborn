#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Unified CLI for Sethlans Reborn development.
#
# Post-Waitress-migration topology (matches production minus the tray
# helper and launcher): Caddy terminates TLS on :8080 and reverse-
# proxies to two loopback Waitress listeners (public 8090, internal
# 8088). The worker connects to https://127.0.0.1:8080 just like a
# real deployment.
#
# Commands:
#   dev      Full dev environment: config + deps + admin + frontend +
#            Caddyfile + start (manager + caddy + worker)
#   clean    Nuke all development artifacts (no confirmation)
#   start    Start caddy + manager + worker (assumes already set up)
#   manager  Start caddy + manager only
#   worker   Start worker only
#   stop     Stop background caddy + manager + worker
#   status   Show running caddy / manager / worker processes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
MANAGER_DIR="$PROJECT_ROOT/manager"
FRONTEND_DIR="$MANAGER_DIR/frontend"
CONFIG_FILE="$MANAGER_DIR/manager.ini"
MANAGE_PY="$MANAGER_DIR/manage.py"
WORKER_DIR="$PROJECT_ROOT/worker"
AGENT_DIR="$WORKER_DIR/sethlans_worker_agent"
PID_DIR="$PROJECT_ROOT/.pids"
CADDY_DIR="$PROJECT_ROOT/.venv-build/caddy"
CADDY_BIN="$CADDY_DIR/caddy"
CADDYFILE="$MANAGER_DIR/caddy/Caddyfile"
DEV_BOOTSTRAP="$SCRIPT_DIR/_dev_bootstrap.py"

# Port layout — mirrors production defaults in manager.ini.example.
PUBLIC_TLS_PORT=8080
CADDY_LOOPBACK_PORT=8089
WAITRESS_PUBLIC_PORT=8090
WAITRESS_INTERNAL_PORT=8088

usage() {
    echo "Usage: bash tools/sethlans.sh <command>"
    echo ""
    echo "  dev      Setup everything from scratch and start services"
    echo "  clean    Remove all development artifacts"
    echo "  start    Start caddy + manager + worker (must run dev first)"
    echo "  manager  Start caddy + manager in the background"
    echo "  worker   Start worker in the background"
    echo "  stop     Stop background caddy + manager + worker"
    echo "  status   Show running caddy/manager/worker processes"
}

ensure_dirs() { mkdir -p "$PID_DIR"; }

get_saved_pid() {
    local pid_file="$PID_DIR/$1.pid"
    if [ -f "$pid_file" ]; then
        local pid; pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then echo "$pid"; return; fi
        rm -f "$pid_file"
    fi
    echo ""
}

save_pid() { ensure_dirs; echo "$2" > "$PID_DIR/$1.pid"; }
remove_pid() { rm -f "$PID_DIR/$1.pid"; }

read_enrollment_key() {
    # Canonical key from the ManagerSettings DB row.
    python "$DEV_BOOTSTRAP" enrollment-key --manager-dir "$MANAGER_DIR" \
        2>/dev/null
}

ensure_caddy_binary() {
    if [ -x "$CADDY_BIN" ]; then return; fi
    echo "--- Fetching Caddy binary into $CADDY_DIR ---"
    python "$SCRIPT_DIR/fetch_caddy.py" --target-dir "$CADDY_DIR"
}

render_caddyfile() {
    python "$DEV_BOOTSTRAP" render-caddyfile \
        --manager-dir "$MANAGER_DIR" \
        --public-tls-port "$PUBLIC_TLS_PORT" \
        --loopback-plaintext-port "$CADDY_LOOPBACK_PORT" \
        --waitress-public-port "$WAITRESS_PUBLIC_PORT" \
        --waitress-internal-port "$WAITRESS_INTERNAL_PORT" \
        > /dev/null
    echo "[OK] Caddyfile rendered: $CADDYFILE"
}

generate_config() {
    # Delegated to the Python helper so MSYS/Git-Bash path translation
    # applies to the argv (string literals inside `python -c` are NOT
    # translated and blew up on /c/... paths under Git Bash).
    python "$DEV_BOOTSTRAP" generate-config \
        --manager-dir "$MANAGER_DIR" \
        --port "$PUBLIC_TLS_PORT"
}

start_services() {
    echo ""; cmd_manager; echo ""; cmd_worker; echo ""; cmd_status
    echo ""
    echo "============================================================"
    echo "  Manager UI:  https://127.0.0.1:$PUBLIC_TLS_PORT (Caddy TLS)"
    echo "  Worker UI:   https://127.0.0.1:8081 (Caddy TLS)"
    echo "  Swagger API: https://127.0.0.1:$PUBLIC_TLS_PORT/api/docs/"
    echo "  Admin login: testuser / test12345"
    echo "============================================================"
}

# -- dev -----------------------------------------------------------------
cmd_dev() {
    echo "============================================================"
    echo "  Sethlans Reborn -- Dev Environment"
    echo "============================================================"
    echo ""
    echo "--- Configuration ---"
    generate_config
    echo ""
    echo "--- Python dependencies ---"
    pip install -r "$MANAGER_DIR/requirements.txt" \
                -r "$WORKER_DIR/requirements.txt" \
                -r "$PROJECT_ROOT/requirements-dev.txt"
    echo ""
    echo "--- Caddy binary ---"
    ensure_caddy_binary
    echo ""
    echo "--- Database migrations ---"
    # Migration 0017 seeds the ManagerSettings row with a fresh
    # enrollment key the first time it runs — no extra step needed.
    python "$MANAGE_PY" migrate
    echo ""
    echo "--- Admin account ---"
    # SetupGateMiddleware's defense-in-depth treats "superuser exists"
    # as equivalent to sentinel-present, so the gate stays open.
    DJANGO_SUPERUSER_PASSWORD=test12345 python "$MANAGE_PY" createsuperuser \
        --username testuser --email "" --noinput 2>/dev/null || true
    echo "[OK] Admin ready (testuser / test12345)"
    echo ""
    echo "--- Caddyfile ---"
    render_caddyfile
    if [ -d "$FRONTEND_DIR" ]; then
        echo ""
        echo "--- Frontend ---"
        export NG_CLI_ANALYTICS=false
        # npm --prefix is unreliable on Windows/MSYS (silently ignored,
        # so it walks back to $PWD/package.json); pushd is portable.
        pushd "$FRONTEND_DIR" > /dev/null
        if [ ! -d "node_modules" ]; then
            npm install --no-progress --no-fund --no-audit
        fi
        npm run build --no-progress
        popd > /dev/null
        echo "[OK] Frontend built"
    fi
    start_services
}

# -- clean ---------------------------------------------------------------
cmd_clean() {
    echo "============================================================"
    echo "  Sethlans Reborn -- Clean"
    echo "============================================================"
    echo ""
    # Stop any sethlans python processes scoped to this project root.
    local victims
    victims=$(pgrep -f "$PROJECT_ROOT.*(manage\.py runserver|run_manager\.py|run_worker\.py)" 2>/dev/null || true)
    if [ -n "$victims" ]; then
        for pid in $victims; do
            echo "[KILL] Stopping PID $pid"; kill "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
    # Caddy processes whose config points at our Caddyfile.
    local caddy_victims
    caddy_victims=$(pgrep -f "caddy.*$MANAGER_DIR/caddy/Caddyfile" 2>/dev/null || true)
    if [ -n "$caddy_victims" ]; then
        for pid in $caddy_victims; do
            echo "[KILL] Stopping Caddy PID $pid"; kill "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
    rm -f "$PID_DIR/manager.pid" "$PID_DIR/worker.pid" "$PID_DIR/caddy.pid"
    # Manager artifacts — source-mode state lives inside manager/.
    rm -f "$CONFIG_FILE" "$MANAGER_DIR/db.sqlite3-journal"
    rm -f "$MANAGER_DIR/broadcaster_params.json" \
          "$MANAGER_DIR/topology.json" \
          "$MANAGER_DIR/.setup_complete"
    if [ -e "$MANAGER_DIR/db.sqlite3" ]; then
        if ! rm -f "$MANAGER_DIR/db.sqlite3"; then
            echo "[ERROR] Failed to delete database file: $MANAGER_DIR/db.sqlite3"
            local still
            still=$(pgrep -f "$PROJECT_ROOT.*(manage\.py runserver|run_manager\.py|run_worker\.py)" 2>/dev/null || true)
            [ -n "$still" ] && echo "        Still running: PID(s) $(echo "$still" | tr '\n' ' ')"
            exit 1
        fi
    fi
    [ -e "$MANAGER_DIR/db.sqlite3" ] && echo "[ERROR] DB still exists after delete" && exit 1
    rm -rf "$MANAGER_DIR/staticfiles" "$MANAGER_DIR/logs" "$MANAGER_DIR/tls" \
           "$MANAGER_DIR/caddy"
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/.angular" "$FRONTEND_DIR/node_modules"
    rm -rf "${MANAGER_DIR:?}/media"
    find "$MANAGER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Manager artifacts removed"
    # Worker (in-tree legacy + OS data dir)
    rm -rf "$AGENT_DIR/managed_tools" "$AGENT_DIR/managed_assets" "$AGENT_DIR/worker_output"
    rm -rf "$AGENT_DIR/temp" "$AGENT_DIR/logs"
    find "$WORKER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    # Wipe shared per-user data dir (worker + manager sub-dirs + shared
    # logs). This covers both the worker's enrollment/certs/tools cache
    # and any state left behind by a frozen-mode run.
    local shared_data_dir
    case "$(uname -s)" in
        Darwin) shared_data_dir="$HOME/Library/Application Support/Sethlans" ;;
        *)      shared_data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/sethlans" ;;
    esac
    if [ -d "$shared_data_dir" ]; then
        rm -rf "$shared_data_dir"
        echo "[OK] Shared data dir removed ($shared_data_dir)"
    fi
    echo "[OK] Worker artifacts removed"
    # Shared artifacts
    rm -rf "$PROJECT_ROOT/temp" "$PROJECT_ROOT/.pytest_cache" "$PROJECT_ROOT/sethlans_e2e_cache"
    rm -f "$PROJECT_ROOT/test_e2e_db.sqlite3"
    rm -rf "$PROJECT_ROOT/test_artifacts" "$PROJECT_ROOT/manual_test_output" "$PROJECT_ROOT/tools/results"
    echo "[OK] Shared artifacts removed"
    echo ""
    echo "[OK] Clean complete."
}

# -- start ---------------------------------------------------------------
cmd_start() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    start_services
}

# -- caddy (internal, invoked by cmd_manager) ----------------------------
start_caddy() {
    local existing; existing=$(get_saved_pid "caddy")
    if [ -n "$existing" ]; then
        echo "[OK] Caddy already running (PID $existing)"; return
    fi
    if [ ! -x "$CADDY_BIN" ]; then
        echo "[ERROR] Caddy binary not found at $CADDY_BIN"
        echo "        Run: bash tools/sethlans.sh dev (or python tools/fetch_caddy.py --target-dir $CADDY_DIR)"
        exit 1
    fi
    if [ ! -f "$CADDYFILE" ]; then
        echo "[ERROR] Caddyfile not found at $CADDYFILE"
        echo "        Run: bash tools/sethlans.sh dev"
        exit 1
    fi
    ensure_dirs
    nohup "$CADDY_BIN" run --config "$CADDYFILE" --adapter caddyfile \
        > "$PID_DIR/caddy.out.log" 2>&1 &
    local pid=$!; sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Caddy failed to start (see $PID_DIR/caddy.out.log)"; exit 1
    fi
    save_pid "caddy" "$pid"
    echo "[OK] Caddy started in background (PID $pid)"
}

# -- manager -------------------------------------------------------------
cmd_manager() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    local existing; existing=$(get_saved_pid "manager")
    if [ -n "$existing" ]; then
        echo "[OK] Manager already running (PID $existing)"
    else
        ensure_dirs
        nohup python "$MANAGER_DIR/run_manager.py" > /dev/null 2>&1 &
        local pid=$!; sleep 2
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "[ERROR] Manager failed to start"; exit 1
        fi
        save_pid "manager" "$pid"
        echo "[OK] Manager started in background (PID $pid)"
    fi
    # Caddy comes up after Waitress is bound so the first proxy attempt
    # doesn't hit a closed socket and log spurious errors.
    start_caddy
    echo "     Stop: bash tools/sethlans.sh stop"
}

# -- worker --------------------------------------------------------------
cmd_worker() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    local existing; existing=$(get_saved_pid "worker")
    if [ -n "$existing" ]; then
        echo "[OK] Worker already running (PID $existing)"; return
    fi
    local enrollment_key; enrollment_key=$(read_enrollment_key)
    if [ -z "$enrollment_key" ]; then
        echo "[ERROR] Could not read enrollment key from ManagerSettings DB."
        echo "        Run 'bash tools/sethlans.sh dev' first to migrate the DB."
        exit 1
    fi
    ensure_dirs
    SETHLANS_WORKER_ENROLLMENT_KEY="$enrollment_key" \
    SETHLANS_MANAGER_HOST="127.0.0.1" \
    SETHLANS_MANAGER_PORT="$PUBLIC_TLS_PORT" \
    SETHLANS_IDLE_DETECTION_ENABLED="false" \
    SETHLANS_WORKER_UI_ENABLED="true" \
    nohup python "$WORKER_DIR/run_worker.py" > /dev/null 2>&1 &
    local pid=$!; sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Worker failed to start"; exit 1
    fi
    save_pid "worker" "$pid"
    echo "[OK] Worker started in background (PID $pid)"
    echo "     Stop: bash tools/sethlans.sh stop"
}

# -- stop ----------------------------------------------------------------
cmd_stop() {
    local stopped=false
    # Stop worker first so it doesn't log enrollment failures when
    # Caddy/Manager go away mid-heartbeat.
    local worker_pid; worker_pid=$(get_saved_pid "worker")
    if [ -n "$worker_pid" ]; then
        kill "$worker_pid" 2>/dev/null; wait "$worker_pid" 2>/dev/null || true
        remove_pid "worker"; echo "[OK] Worker stopped (PID $worker_pid)"; stopped=true
    fi
    local caddy_pid; caddy_pid=$(get_saved_pid "caddy")
    if [ -n "$caddy_pid" ]; then
        kill "$caddy_pid" 2>/dev/null; wait "$caddy_pid" 2>/dev/null || true
        remove_pid "caddy"; echo "[OK] Caddy stopped (PID $caddy_pid)"; stopped=true
    fi
    local manager_pid; manager_pid=$(get_saved_pid "manager")
    if [ -n "$manager_pid" ]; then
        kill "$manager_pid" 2>/dev/null; wait "$manager_pid" 2>/dev/null || true
        remove_pid "manager"; echo "[OK] Manager stopped (PID $manager_pid)"; stopped=true
    fi
    [ "$stopped" = false ] && echo "[OK] No running services found"
}

# -- status --------------------------------------------------------------
cmd_status() {
    local manager_pid; manager_pid=$(get_saved_pid "manager")
    [ -n "$manager_pid" ] && echo "Manager:  running (PID $manager_pid)" || echo "Manager:  not running"
    local caddy_pid; caddy_pid=$(get_saved_pid "caddy")
    [ -n "$caddy_pid" ] && echo "Caddy:    running (PID $caddy_pid)" || echo "Caddy:    not running"
    local worker_pid; worker_pid=$(get_saved_pid "worker")
    [ -n "$worker_pid" ] && echo "Worker:   running (PID $worker_pid)" || echo "Worker:   not running"
}

# -- Main ----------------------------------------------------------------
case "${1:-}" in
    dev)     cmd_dev ;;
    clean)   cmd_clean ;;
    start)   cmd_start ;;
    manager) cmd_manager ;;
    worker)  cmd_worker ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    *)       usage; exit 1 ;;
esac
