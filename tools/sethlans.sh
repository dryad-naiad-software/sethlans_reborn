#!/usr/bin/env bash
#
# SYNOPSIS
#     Unified CLI for Sethlans Reborn development tasks.
#
# USAGE
#     bash tools/sethlans.sh <command> [target] [options]
#
#     Commands:
#       dev                      Full dev environment: setup + build + start manager
#       dev --clean              Clean first, then setup + build + start
#       setup                    First-time manager setup (config, deps, DB, admin, frontend)
#       start manager            Start the Django manager server
#       start worker             Start the worker agent
#       build                    Build Angular frontend and collect static files
#       clean [manager|worker]   Remove generated artifacts (default: all)
#
#     Options:
#       --clean                  Wipe all artifacts before setup (dev only)
#       --force, -f              Skip confirmation prompts (clean only)
#
# NOTES
#     Author: Sethlans Reborn Development
#     Last Modified: 2026-04-01
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
MANAGER_DIR="$PROJECT_ROOT/manager"
FRONTEND_DIR="$MANAGER_DIR/frontend"
CONFIG_FILE="$MANAGER_DIR/manager.ini"
MANAGE_PY="$MANAGER_DIR/manage.py"
WORKER_DIR="$PROJECT_ROOT/worker"
AGENT_DIR="$WORKER_DIR/sethlans_worker_agent"

# ── Helpers ──────────────────────────────────────────────────────

usage() {
    echo "Usage: bash tools/sethlans.sh <command> [target] [options]"
    echo ""
    echo "Commands:"
    echo "  dev                      Full dev environment: setup + build + start"
    echo "  dev --clean              Clean everything first, then setup from scratch"
    echo "  setup                    First-time manager setup (config, deps, DB, admin, frontend)"
    echo "  start manager            Start the Django manager server"
    echo "  start worker             Start the worker agent"
    echo "  build                    Build Angular frontend + collect static files"
    echo "  clean                    Clean all generated artifacts"
    echo "  clean manager            Clean manager artifacts only"
    echo "  clean worker             Clean worker artifacts only"
    echo ""
    echo "Options:"
    echo "  --clean                  Wipe all artifacts before setup (dev only)"
    echo "  --force, -f              Skip confirmation prompts (clean only)"
}

remove_if_exists() {
    local path="$1" label="$2" is_dir="${3:-false}"
    if [ "$is_dir" = true ]; then
        if [ -d "$path" ]; then
            rm -rf "$path"
            echo "[OK] Removed $label"
        else
            echo "[--] $label not found (already clean)"
        fi
    else
        if [ -f "$path" ]; then
            rm "$path"
            echo "[OK] Removed $label"
        else
            echo "[--] $label not found (already clean)"
        fi
    fi
}

remove_pycache() {
    local dir="$1" label="$2"
    local count
    count=$(find "$dir" -type d -name "__pycache__" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        find "$dir" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        echo "[OK] Removed $count __pycache__/ dirs under $label"
    else
        echo "[--] No __pycache__/ dirs under $label"
    fi
}

# ── generate_config ──────────────────────────────────────────────

generate_config() {
    python "$MANAGER_DIR/setup.py" --config-only 2>/dev/null || python -c "
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
else:
    print('[OK] SECRET_KEY already configured')

if not config.get('security', 'enrollment_key', fallback=''):
    key = secrets.token_urlsafe(32)
    config.set('security', 'enrollment_key', key)
    print('[OK] Generated enrollment key')
    print()
    print('============================================================')
    print('  ENROLLMENT KEY (copy to each worker config.ini):')
    print('  ' + key)
    print('============================================================')
else:
    print('[OK] Enrollment key already configured')

if not config.get('security', 'debug', fallback=''):
    config.set('security', 'debug', 'true')
    print('[OK] Set DEBUG=true (development mode)')

with open(config_path, 'w') as f:
    config.write(f)
"
}

# ── install_deps ─────────────────────────────────────────────────

install_deps() {
    if [ -f "$MANAGER_DIR/requirements.txt" ]; then
        pip install -q -r "$MANAGER_DIR/requirements.txt"
        echo "[OK] Manager dependencies installed"
    fi
}

# ── run_migrations ───────────────────────────────────────────────

run_migrations() {
    python "$MANAGE_PY" migrate
    echo "[OK] Migrations applied"
}

# ── build_frontend ───────────────────────────────────────────────

build_frontend() {
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo "[SKIP] Frontend directory not found"
        return
    fi

    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm install --prefix "$FRONTEND_DIR"
    fi

    echo "Building Angular frontend..."
    npm run build --prefix "$FRONTEND_DIR"
    echo "[OK] Frontend built"
}

# ── collect_static ───────────────────────────────────────────────

collect_static() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found — cannot collect static files"
        return 1
    fi
    python "$MANAGE_PY" collectstatic --noinput
    echo "[OK] Static files collected"
}

# ── dev ──────────────────────────────────────────────────────────

cmd_dev() {
    local do_clean="$1"

    echo "============================================================"
    echo "  Sethlans Reborn - Development Environment"
    echo "============================================================"

    # Step 0: Clean (optional)
    if [ "$do_clean" = true ]; then
        echo ""
        echo "--- Cleaning all artifacts ---"
        clean_manager
        echo ""
        clean_worker
        echo ""
        clean_shared
        echo ""
        echo "[OK] Clean complete."
    fi

    # Step 1: Configuration
    echo ""
    echo "--- Configuration ---"
    generate_config

    # Step 2: Python dependencies
    echo ""
    echo "--- Python dependencies ---"
    install_deps

    # Step 3: Database
    echo ""
    echo "--- Database migrations ---"
    run_migrations

    # Step 4: Admin user (only if fresh DB)
    if ! python "$MANAGE_PY" shell -c "from django.contrib.auth import get_user_model; exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)" 2>/dev/null; then
        echo ""
        echo "--- Create admin account ---"
        python "$MANAGE_PY" createsuperuser || echo "[SKIP] Admin creation skipped"
    else
        echo "[OK] Admin account exists"
    fi

    # Step 5: Frontend
    echo ""
    echo "--- Frontend build ---"
    build_frontend

    # Step 6: Static files
    echo ""
    echo "--- Static files ---"
    collect_static

    # Step 7: Start
    echo ""
    echo "============================================================"
    echo "  Starting Sethlans Manager on port 7075"
    echo "============================================================"
    echo ""
    python "$MANAGE_PY" runserver 7075
}

# ── setup ────────────────────────────────────────────────────────

cmd_setup() {
    echo "============================================================"
    echo "  Sethlans Manager - First Time Setup"
    echo "============================================================"

    echo ""
    echo "--- Step 1: Configuration ---"
    generate_config

    echo ""
    echo "--- Step 2: Python dependencies ---"
    install_deps

    echo ""
    echo "--- Step 3: Database migrations ---"
    run_migrations

    echo ""
    echo "--- Step 4: Create admin account ---"
    echo "(Skip with Ctrl+C if admin already exists)"
    echo ""
    python "$MANAGE_PY" createsuperuser || echo "[SKIP] Admin creation skipped or user already exists"

    echo ""
    echo "--- Step 5: Frontend build ---"
    build_frontend

    echo ""
    echo "--- Step 6: Static files ---"
    collect_static

    echo ""
    echo "============================================================"
    echo "  Setup complete! Run: bash tools/sethlans.sh start manager"
    echo "============================================================"
}

# ── start ────────────────────────────────────────────────────────

cmd_start_manager() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh setup"
        exit 1
    fi

    echo "--- Applying migrations ---"
    python "$MANAGE_PY" migrate

    echo ""
    echo "--- Starting Sethlans Manager ---"
    python "$MANAGE_PY" runserver 7075
}

cmd_start_worker() {
    local worker_config="$WORKER_DIR/config.ini"
    if [ ! -f "$worker_config" ]; then
        echo "[ERROR] worker/config.ini not found."
        echo ""
        echo "Create it from the example:"
        echo "  cp $WORKER_DIR/config.ini.example $worker_config"
        echo ""
        echo "Then set the enrollment_key from the manager setup output."
        exit 1
    fi

    echo "--- Starting Sethlans Worker Agent ---"
    python "$WORKER_DIR/run_worker.py"
}

# ── build ────────────────────────────────────────────────────────

cmd_build() {
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo "[ERROR] Frontend directory not found at $FRONTEND_DIR"
        exit 1
    fi

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] manager.ini not found. Run: bash tools/sethlans.sh setup"
        exit 1
    fi

    echo "--- Frontend ---"
    build_frontend

    echo ""
    echo "--- Static files ---"
    collect_static
}

# ── clean ────────────────────────────────────────────────────────

clean_manager() {
    echo "--- Manager artifacts ---"
    remove_if_exists "$CONFIG_FILE" "manager.ini"
    remove_if_exists "$MANAGER_DIR/db.sqlite3" "db.sqlite3"
    remove_if_exists "$MANAGER_DIR/db.sqlite3-journal" "db.sqlite3-journal"
    remove_if_exists "$MANAGER_DIR/staticfiles" "staticfiles/" true
    remove_if_exists "$FRONTEND_DIR/dist" "frontend/dist/" true
    remove_if_exists "$FRONTEND_DIR/.angular" "frontend/.angular/" true
    remove_if_exists "$FRONTEND_DIR/node_modules" "frontend/node_modules/" true
    remove_if_exists "$MANAGER_DIR/logs" "manager/logs/" true
    remove_if_exists "$PROJECT_ROOT/media" "media/" true
    remove_pycache "$MANAGER_DIR" "manager/"
}

clean_worker() {
    echo "--- Worker artifacts ---"
    remove_if_exists "$WORKER_DIR/config.ini" "config.ini"
    remove_if_exists "$AGENT_DIR/managed_tools" "managed_tools/" true
    remove_if_exists "$AGENT_DIR/managed_assets" "managed_assets/" true
    remove_if_exists "$AGENT_DIR/worker_output" "worker_output/" true
    remove_if_exists "$AGENT_DIR/temp" "temp/" true
    remove_if_exists "$AGENT_DIR/logs" "worker/logs/" true
    remove_pycache "$WORKER_DIR" "worker/"
}

clean_shared() {
    echo "--- Shared artifacts ---"
    remove_if_exists "$PROJECT_ROOT/temp" "temp/" true
    remove_if_exists "$PROJECT_ROOT/.pytest_cache" ".pytest_cache/" true
    remove_if_exists "$PROJECT_ROOT/sethlans_e2e_cache" "sethlans_e2e_cache/" true
    remove_if_exists "$PROJECT_ROOT/test_e2e_db.sqlite3" "test_e2e_db.sqlite3"
    remove_if_exists "$PROJECT_ROOT/test_artifacts" "test_artifacts/" true
    remove_if_exists "$PROJECT_ROOT/manual_test_output" "manual_test_output/" true
    remove_if_exists "$PROJECT_ROOT/tools/results" "tools/results/" true
}

cmd_clean() {
    local target="${1:-all}"
    local force="${2:-false}"

    echo "============================================================"
    echo "  Sethlans Reborn - Clean ($target)"
    echo "============================================================"
    echo ""

    if [ "$force" = false ]; then
        read -r -p "Remove all $target artifacts? [y/N] " response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            echo "Aborted."
            exit 0
        fi
        echo ""
    fi

    case "$target" in
        manager) clean_manager ;;
        worker)  clean_worker ;;
        all)
            clean_manager
            echo ""
            clean_worker
            echo ""
            clean_shared
            ;;
    esac

    echo ""
    echo "[OK] Clean complete ($target)."
}

# ── Main dispatch ────────────────────────────────────────────────

COMMAND="${1:-}"
TARGET="${2:-}"
FORCE=false
CLEAN=false

# Parse flags from any position
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=true ;;
        --clean)    CLEAN=true ;;
    esac
done

case "$COMMAND" in
    dev)
        cmd_dev "$CLEAN"
        ;;
    setup)
        cmd_setup
        ;;
    start)
        case "$TARGET" in
            manager) cmd_start_manager ;;
            worker)  cmd_start_worker ;;
            *)
                echo "[ERROR] Usage: bash tools/sethlans.sh start [manager|worker]"
                exit 1
                ;;
        esac
        ;;
    build)
        cmd_build
        ;;
    clean)
        case "$TARGET" in
            manager|worker) cmd_clean "$TARGET" "$FORCE" ;;
            ""|--force|-f|--clean) cmd_clean "all" "$FORCE" ;;
            *)
                echo "[ERROR] Usage: bash tools/sethlans.sh clean [manager|worker]"
                exit 1
                ;;
        esac
        ;;
    *)
        usage
        exit 1
        ;;
esac
