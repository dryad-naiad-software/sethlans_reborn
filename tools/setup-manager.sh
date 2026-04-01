#!/usr/bin/env bash
#
# SYNOPSIS
#     First-time setup for the Sethlans Manager.
#
# DESCRIPTION
#     This script performs all first-time setup tasks:
#       1. Generates manager.ini with SECRET_KEY and enrollment key (if missing).
#       2. Installs Python dependencies.
#       3. Runs database migrations.
#       4. Creates an admin superuser (interactive).
#       5. Installs frontend dependencies and builds the Angular UI.
#       6. Collects static files for WhiteNoise.
#
#     Safe to re-run: skips steps that are already complete.
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

echo "============================================================"
echo "  Sethlans Manager - First Time Setup"
echo "============================================================"

# --- Step 1: Generate manager.ini ---
echo ""
echo "--- Step 1: Configuration ---"
python "$MANAGER_DIR/setup.py" --config-only 2>/dev/null || python -c "
import configparser, secrets, sys
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

# --- Step 2: Python dependencies ---
echo ""
echo "--- Step 2: Python dependencies ---"
if [ -f "$MANAGER_DIR/requirements.txt" ]; then
    pip install -q -r "$MANAGER_DIR/requirements.txt"
    echo "[OK] Manager dependencies installed"
fi

# --- Step 3: Database migrations ---
echo ""
echo "--- Step 3: Database migrations ---"
python "$MANAGE_PY" migrate
echo "[OK] Migrations applied"

# --- Step 4: Admin user ---
echo ""
echo "--- Step 4: Create admin account ---"
echo "(Skip with Ctrl+C if admin already exists)"
echo ""
python "$MANAGE_PY" createsuperuser || echo "[SKIP] Admin creation skipped or user already exists"

# --- Step 5: Frontend build ---
echo ""
echo "--- Step 5: Frontend build ---"
if [ -d "$FRONTEND_DIR" ]; then
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm install --prefix "$FRONTEND_DIR"
    fi
    echo "Building Angular frontend..."
    npm run build --prefix "$FRONTEND_DIR"
    echo "[OK] Frontend built"
else
    echo "[SKIP] Frontend directory not found"
fi

# --- Step 6: Collect static files ---
echo ""
echo "--- Step 6: Static files ---"
python "$MANAGE_PY" collectstatic --noinput
echo "[OK] Static files collected"

echo ""
echo "============================================================"
echo "  Setup complete!"
echo ""
echo "  Start the manager:  python $MANAGE_PY runserver 7075"
echo "  Or:                 bash tools/start-manager.sh"
echo "============================================================"
