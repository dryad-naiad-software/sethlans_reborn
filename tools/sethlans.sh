#!/usr/bin/env bash
#
# Unified CLI for Sethlans Reborn development.
#
# Commands:
#   dev      Full dev environment: config + deps + db + admin + frontend + start
#   clean    Nuke all development artifacts (no confirmation)
#   start    Start manager + worker (assumes already set up)
#   manager  Start manager only
#   worker   Start worker only

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

ensure_dirs() {
    mkdir -p "$PID_DIR"
}

get_saved_pid() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return
        fi
        rm -f "$pid_file"
    fi
    echo ""
}

save_pid() {
    local name="$1" pid="$2"
    ensure_dirs
    echo "$pid" > "$PID_DIR/$name.pid"
}

remove_pid() {
    rm -f "$PID_DIR/$1.pid"
}

generate_config() {
    python -c "
import configparser, secrets
from pathlib import Path

config_path = Path('$CONFIG_FILE')
config = configparser.ConfigParser()
if config_path.exists():
    config.read(config_path)
    print('[OK] Found existing manager.ini')
else:
    print('[NEW] Creating manager.ini')

for section in ('server', 'security'):
    if not config.has_section(section):
        config.add_section(section)

if not config.has_option('server', 'port'):
    config.set('server', 'port', '7075')

if not config.get('security', 'secret_key', fallback=''):
    config.set('security', 'secret_key', secrets.token_urlsafe(50))
    print('[OK] Generated SECRET_KEY')

if not config.get('security', 'enrollment_key', fallback=''):
    key = secrets.token_urlsafe(32)
    config.set('security', 'enrollment_key', key)
    print('[OK] Generated enrollment key: ' + key)
else:
    print('[OK] Enrollment key already configured')

if not config.get('security', 'debug', fallback=''):
    config.set('security', 'debug', 'true')

with open(config_path, 'w') as f:
    config.write(f)
"
}

configure_worker() {
    local worker_config="$WORKER_DIR/config.ini"
    if [ -f "$worker_config" ]; then
        echo "[OK] Worker config.ini already exists"
        return
    fi

    local enrollment_key
    enrollment_key=$(python -c "
import configparser
c = configparser.ConfigParser()
c.read('$CONFIG_FILE')
print(c.get('security', 'enrollment_key', fallback=''))
" 2>/dev/null)

    if [ -z "$enrollment_key" ]; then
        echo "[SKIP] No enrollment key — configure worker manually"
        return
    fi

    cp "$WORKER_DIR/config.ini.example" "$worker_config"
    python -c "
import configparser
c = configparser.ConfigParser()
c.read('$worker_config')
c.set('manager', 'enrollment_key', '$enrollment_key')
with open('$worker_config', 'w') as f:
    c.write(f)
"
    echo "[OK] Worker config.ini created with enrollment key"
}

start_services() {
    echo ""
    cmd_manager

    local worker_config="$WORKER_DIR/config.ini"
    if [ -f "$worker_config" ]; then
        echo ""
        cmd_worker
    else
        echo "[!!] No worker config — manager running alone"
    fi

    echo ""
    cmd_status
}

# ── dev ─────────────────────────────────────────────────────────

cmd_dev() {
    echo "============================================================"
    echo "  Sethlans Reborn — Dev Environment"
    echo "============================================================"

    echo ""
    echo "--- Configuration ---"
    generate_config

    echo ""
    echo "--- Python dependencies ---"
    pip install -r "$MANAGER_DIR/requirements.txt" -r "$PROJECT_ROOT/requirements-dev.txt"

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

    echo ""
    echo "--- Static files ---"
    python "$MANAGE_PY" collectstatic --noinput

    echo ""
    echo "--- Worker configuration ---"
    configure_worker

    start_services
}

# ── clean ───────────────────────────────────────────────────────

cmd_clean() {
    echo "============================================================"
    echo "  Sethlans Reborn — Clean"
    echo "============================================================"
    echo ""

    # Manager
    rm -f "$CONFIG_FILE" "$MANAGER_DIR/db.sqlite3" "$MANAGER_DIR/db.sqlite3-journal"
    rm -rf "$MANAGER_DIR/staticfiles" "$MANAGER_DIR/logs"
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/.angular" "$FRONTEND_DIR/node_modules"
    rm -rf "$PROJECT_ROOT/media"
    find "$MANAGER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Manager artifacts removed"

    # Worker
    rm -f "$WORKER_DIR/config.ini"
    rm -rf "$AGENT_DIR/managed_tools" "$AGENT_DIR/managed_assets" "$AGENT_DIR/worker_output"
    rm -rf "$AGENT_DIR/temp" "$AGENT_DIR/logs"
    find "$WORKER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Worker artifacts removed"

    # Shared
    rm -rf "$PROJECT_ROOT/temp" "$PROJECT_ROOT/.pytest_cache" "$PROJECT_ROOT/sethlans_e2e_cache"
    rm -f "$PROJECT_ROOT/test_e2e_db.sqlite3"
    rm -rf "$PROJECT_ROOT/test_artifacts" "$PROJECT_ROOT/manual_test_output" "$PROJECT_ROOT/tools/results"
    echo "[OK] Shared artifacts removed"

    echo ""
    echo "[OK] Clean complete."
}

# ── start ───────────────────────────────────────────────────────

cmd_start() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"
        exit 1
    fi

    start_services
}

# ── manager ─────────────────────────────────────────────────────

cmd_manager() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh dev"
        exit 1
    fi

    local existing
    existing=$(get_saved_pid "manager")
    if [ -n "$existing" ]; then
        echo "[OK] Manager already running (PID $existing)"
        return
    fi

    echo "--- Applying migrations ---"
    python "$MANAGE_PY" migrate

    ensure_dirs
    nohup python "$MANAGE_PY" runserver 0.0.0.0:7075 > /dev/null 2>&1 &
    local pid=$!
    sleep 2

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Manager failed to start"
        exit 1
    fi

    save_pid "manager" "$pid"
    echo "[OK] Manager started in background (PID $pid)"
    echo "     Stop: bash tools/sethlans.sh stop"
}

# ── worker ──────────────────────────────────────────────────────

cmd_worker() {
    local worker_config="$WORKER_DIR/config.ini"
    if [ ! -f "$worker_config" ]; then
        echo "[ERROR] Worker config.ini not found. Run: bash tools/sethlans.sh dev"
        exit 1
    fi

    local existing
    existing=$(get_saved_pid "worker")
    if [ -n "$existing" ]; then
        echo "[OK] Worker already running (PID $existing)"
        return
    fi

    ensure_dirs
    nohup python "$WORKER_DIR/run_worker.py" > /dev/null 2>&1 &
    local pid=$!
    sleep 2

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Worker failed to start"
        exit 1
    fi

    save_pid "worker" "$pid"
    echo "[OK] Worker started in background (PID $pid)"
    echo "     Stop: bash tools/sethlans.sh stop"
}

# ── stop ────────────────────────────────────────────────────────

cmd_stop() {
    local stopped=false

    local manager_pid
    manager_pid=$(get_saved_pid "manager")
    if [ -n "$manager_pid" ]; then
        kill "$manager_pid" 2>/dev/null
        wait "$manager_pid" 2>/dev/null || true
        remove_pid "manager"
        echo "[OK] Manager stopped (PID $manager_pid)"
        stopped=true
    fi

    local worker_pid
    worker_pid=$(get_saved_pid "worker")
    if [ -n "$worker_pid" ]; then
        kill "$worker_pid" 2>/dev/null
        wait "$worker_pid" 2>/dev/null || true
        remove_pid "worker"
        echo "[OK] Worker stopped (PID $worker_pid)"
        stopped=true
    fi

    if [ "$stopped" = false ]; then
        echo "[OK] No running services found"
    fi
}

# ── status ──────────────────────────────────────────────────────

cmd_status() {
    local manager_pid
    manager_pid=$(get_saved_pid "manager")
    if [ -n "$manager_pid" ]; then
        echo "Manager:  running (PID $manager_pid)"
    else
        echo "Manager:  not running"
    fi

    local worker_pid
    worker_pid=$(get_saved_pid "worker")
    if [ -n "$worker_pid" ]; then
        echo "Worker:   running (PID $worker_pid)"
    else
        echo "Worker:   not running"
    fi
}

# ── Main ────────────────────────────────────────────────────────

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
