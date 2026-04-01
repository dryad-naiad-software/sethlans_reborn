<#
.SYNOPSIS
    First-time setup for the Sethlans Manager.

.DESCRIPTION
    This script performs all first-time setup tasks:
    1. Generates manager.ini with SECRET_KEY and enrollment key (if missing).
    2. Installs Python dependencies.
    3. Runs database migrations.
    4. Creates an admin superuser (interactive).
    5. Installs frontend dependencies and builds the Angular UI.
    6. Collects static files for WhiteNoise.

    Safe to re-run: skips steps that are already complete.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2026-04-01
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$ManagerDir = Join-Path $ProjectRoot "manager"
$FrontendDir = Join-Path $ManagerDir "frontend"
$ConfigFile = Join-Path $ManagerDir "manager.ini"
$ManagePy = Join-Path $ManagerDir "manage.py"

Write-Host "============================================================"
Write-Host "  Sethlans Manager - First Time Setup"
Write-Host "============================================================"

# --- Step 1: Generate manager.ini ---
Write-Host ""
Write-Host "--- Step 1: Configuration ---"
python (Join-Path $ManagerDir "setup.py") 2>$null
if ($LASTEXITCODE -ne 0) {
    # Fallback: generate config via inline Python
    python -c @"
import configparser, secrets
from pathlib import Path

config_path = Path(r'$ConfigFile')
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
    print('=' * 60)
    print('  ENROLLMENT KEY (copy to each worker config.ini):')
    print('  ' + key)
    print('=' * 60)
else:
    print('[OK] Enrollment key already configured')

if not config.get('security', 'debug', fallback=''):
    config.set('security', 'debug', 'true')
    print('[OK] Set DEBUG=true (development mode)')

with open(config_path, 'w') as f:
    config.write(f)
"@
}

# --- Step 2: Python dependencies ---
Write-Host ""
Write-Host "--- Step 2: Python dependencies ---"
if (Test-Path (Join-Path $ManagerDir "requirements.txt")) {
    pip install -q -r (Join-Path $ManagerDir "requirements.txt")
    Write-Host "[OK] Manager dependencies installed"
}

# --- Step 3: Database migrations ---
Write-Host ""
Write-Host "--- Step 3: Database migrations ---"
python $ManagePy migrate
Write-Host "[OK] Migrations applied"

# --- Step 4: Admin user ---
Write-Host ""
Write-Host "--- Step 4: Create admin account ---"
Write-Host "(Skip with Ctrl+C if admin already exists)"
Write-Host ""
try {
    python $ManagePy createsuperuser
} catch {
    Write-Host "[SKIP] Admin creation skipped or user already exists"
}

# --- Step 5: Frontend build ---
Write-Host ""
Write-Host "--- Step 5: Frontend build ---"
if (Test-Path $FrontendDir) {
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "Installing frontend dependencies..."
        npm install --prefix $FrontendDir
    }
    Write-Host "Building Angular frontend..."
    npm run build --prefix $FrontendDir
    Write-Host "[OK] Frontend built"
} else {
    Write-Host "[SKIP] Frontend directory not found"
}

# --- Step 6: Collect static files ---
Write-Host ""
Write-Host "--- Step 6: Static files ---"
python $ManagePy collectstatic --noinput
Write-Host "[OK] Static files collected"

Write-Host ""
Write-Host "============================================================"
Write-Host "  Setup complete!"
Write-Host ""
Write-Host "  Start the manager:  python $ManagePy runserver 7075"
Write-Host "  Or:                 .\tools\start-manager.ps1"
Write-Host "============================================================"
