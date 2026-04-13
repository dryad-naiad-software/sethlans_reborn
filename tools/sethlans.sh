#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Unified CLI for Sethlans Reborn development.
#
# Commands:
#   dev      Full dev environment: config + deps + admin + frontend + start
#   clean    Nuke all development artifacts (no confirmation)
#   start    Start manager + worker (assumes already set up)
#   manager  Start manager only
#   worker   Start worker only
#   stop     Stop background manager and/or worker
#   status   Show running manager/worker processes

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

usage() {
    echo "Usage: bash tools/sethlans.sh <command>"
    echo ""
    echo "  dev      Setup everything from scratch and start services"
    echo "  clean    Remove all development artifacts"
    echo "  start    Start manager + worker (must run dev first)"
    echo "  manager  Start manager in the background"
    echo "  worker   Start worker in the background"
    echo "  stop     Stop background manager and/or worker"
    echo "  status   Show running manager/worker processes"
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
    python -c "
import configparser; c = configparser.ConfigParser(); c.read('$CONFIG_FILE')
print(c.get('security', 'enrollment_key', fallback=''))
" 2>/dev/null
}

generate_config() {
    python -c "
import sys, os, configparser, secrets
from pathlib import Path
sys.path.insert(0, '$MANAGER_DIR')
os.environ['DJANGO_SETTINGS_MODULE'] = 'sethlans_manager.settings'
from workers.enrollment_key import generate_key
config_path = Path('$CONFIG_FILE')
config = configparser.ConfigParser()
if config_path.exists():
    config.read(config_path); print('[OK] Found existing manager.ini')
else:
    print('[NEW] Creating manager.ini')
for s in ('server', 'security'):
    if not config.has_section(s): config.add_section(s)
if not config.has_option('server', 'port'): config.set('server', 'port', '8080')
if not config.get('security', 'secret_key', fallback=''):
    config.set('security', 'secret_key', secrets.token_urlsafe(50)); print('[OK] Generated SECRET_KEY')
if not config.get('security', 'enrollment_key', fallback=''):
    key = generate_key(); config.set('security', 'enrollment_key', key)
    print('[OK] Generated enrollment key: ' + key)
else:
    print('[OK] Enrollment key already configured')
if not config.get('security', 'debug', fallback=''): config.set('security', 'debug', 'true')
with open(config_path, 'w') as f: config.write(f)
"
}

start_services() {
    echo ""; cmd_manager; echo ""; cmd_worker; echo ""; cmd_status
    echo ""
    echo "============================================================"
    echo "  Manager UI:  https://127.0.0.1:8080"
    echo "  Worker UI:   http://127.0.0.1:8081"
    echo "  Swagger API: https://127.0.0.1:8080/api/docs/"
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
    echo "--- Database migrations ---"
    python "$MANAGE_PY" migrate
    echo ""
    echo "--- Admin account ---"
    DJANGO_SUPERUSER_PASSWORD=test12345 python "$MANAGE_PY" createsuperuser \
        --username testuser --email "" --noinput 2>/dev/null || true
    echo "[OK] Admin ready (testuser / test12345)"
    if [ -d "$FRONTEND_DIR" ]; then
        echo ""
        echo "--- Frontend ---"
        export NG_CLI_ANALYTICS=false
        if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
            npm install --prefix "$FRONTEND_DIR" --no-progress --no-fund --no-audit
        fi
        npm run build --prefix "$FRONTEND_DIR" --no-progress
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
    # Pattern includes the project root path so we never kill unrelated processes.
    local victims
    victims=$(pgrep -f "$PROJECT_ROOT.*(manage\.py runserver|run_manager\.py|run_worker\.py)" 2>/dev/null || true)
    if [ -n "$victims" ]; then
        for pid in $victims; do
            echo "[KILL] Stopping PID $pid"; kill "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
    rm -f "$PID_DIR/manager.pid" "$PID_DIR/worker.pid"
    # Manager artifacts
    rm -f "$CONFIG_FILE" "$MANAGER_DIR/db.sqlite3-journal"
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
    rm -rf "$MANAGER_DIR/staticfiles" "$MANAGER_DIR/logs" "$MANAGER_DIR/tls"
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/.angular" "$FRONTEND_DIR/node_modules"
    rm -rf "${MANAGER_DIR:?}/media"
    find "$MANAGER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Manager artifacts removed"
    # Worker (in-tree only; worker state now lives in OS data dir)
    rm -rf "$AGENT_DIR/managed_tools" "$AGENT_DIR/managed_assets" "$AGENT_DIR/worker_output"
    rm -rf "$AGENT_DIR/temp" "$AGENT_DIR/logs"
    find "$WORKER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Worker in-tree artifacts removed"
    echo "     NOTE: Worker state is in the OS data dir (XDG_DATA_HOME/sethlans/worker)."
    echo "           Remove it manually if a full reset is needed."
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

# -- manager -------------------------------------------------------------
cmd_manager() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    local existing; existing=$(get_saved_pid "manager")
    if [ -n "$existing" ]; then
        echo "[OK] Manager already running (PID $existing)"; return
    fi
    ensure_dirs
    nohup python "$MANAGER_DIR/run_manager.py" > /dev/null 2>&1 &
    local pid=$!; sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Manager failed to start"; exit 1
    fi
    save_pid "manager" "$pid"
    echo "[OK] Manager started in background (PID $pid)"
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
        echo "[ERROR] No enrollment key found in manager.ini"; exit 1
    fi
    ensure_dirs
    SETHLANS_WORKER_ENROLLMENT_KEY="$enrollment_key" \
    SETHLANS_MANAGER_HOST="127.0.0.1" \
    SETHLANS_MANAGER_PORT="8080" \
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
    local manager_pid; manager_pid=$(get_saved_pid "manager")
    if [ -n "$manager_pid" ]; then
        kill "$manager_pid" 2>/dev/null; wait "$manager_pid" 2>/dev/null || true
        remove_pid "manager"; echo "[OK] Manager stopped (PID $manager_pid)"; stopped=true
    fi
    local worker_pid; worker_pid=$(get_saved_pid "worker")
    if [ -n "$worker_pid" ]; then
        kill "$worker_pid" 2>/dev/null; wait "$worker_pid" 2>/dev/null || true
        remove_pid "worker"; echo "[OK] Worker stopped (PID $worker_pid)"; stopped=true
    fi
    [ "$stopped" = false ] && echo "[OK] No running services found"
}

# -- status --------------------------------------------------------------
cmd_status() {
    local manager_pid; manager_pid=$(get_saved_pid "manager")
    [ -n "$manager_pid" ] && echo "Manager:  running (PID $manager_pid)" || echo "Manager:  not running"
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
