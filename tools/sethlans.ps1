<#
.SYNOPSIS
    Unified CLI for Sethlans Reborn development tasks.

.DESCRIPTION
    Commands:
      dev                      Full dev environment: setup + build + start manager
      dev -Clean               Clean first, then setup + build + start
      setup                    First-time manager setup (config, deps, DB, admin, frontend)
      start manager            Start the Django manager server
      start worker             Start the worker agent
      build                    Build Angular frontend and collect static files
      clean [manager|worker]   Remove generated artifacts (default: all)

.PARAMETER Command
    The action to perform: dev, setup, start, build, clean

.PARAMETER Target
    Target component for start/clean: manager, worker

.PARAMETER Clean
    Wipe all artifacts before setup (dev only).

.PARAMETER Force
    Skip confirmation prompts (clean only).

.EXAMPLE
    .\tools\sethlans.ps1 dev
    .\tools\sethlans.ps1 dev -Clean
    .\tools\sethlans.ps1 setup
    .\tools\sethlans.ps1 start manager
    .\tools\sethlans.ps1 start worker
    .\tools\sethlans.ps1 build
    .\tools\sethlans.ps1 clean
    .\tools\sethlans.ps1 clean manager -Force

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2026-04-01
#>

param(
    [Parameter(Position = 0)]
    [string]$Command,

    [Parameter(Position = 1)]
    [string]$Target,

    [switch]$Clean,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$ManagerDir = Join-Path $ProjectRoot "manager"
$FrontendDir = Join-Path $ManagerDir "frontend"
$ConfigFile = Join-Path $ManagerDir "manager.ini"
$ManagePy = Join-Path $ManagerDir "manage.py"
$WorkerDir = Join-Path $ProjectRoot "worker"
$AgentDir = Join-Path $WorkerDir "sethlans_worker_agent"

# ── Helpers ──────────────────────────────────────────────────────

function Show-Usage {
    Write-Host "Usage: .\tools\sethlans.ps1 <command> [target] [options]"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  dev                      Full dev environment: setup + build + start"
    Write-Host "  dev -Clean               Clean everything first, then setup from scratch"
    Write-Host "  setup                    First-time manager setup (config, deps, DB, admin, frontend)"
    Write-Host "  start manager            Start the Django manager server"
    Write-Host "  start worker             Start the worker agent"
    Write-Host "  build                    Build Angular frontend + collect static files"
    Write-Host "  clean                    Clean all generated artifacts"
    Write-Host "  clean manager            Clean manager artifacts only"
    Write-Host "  clean worker             Clean worker artifacts only"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Clean                   Wipe all artifacts before setup (dev only)"
    Write-Host "  -Force                   Skip confirmation prompts (clean only)"
}

function Remove-IfExists {
    param([string]$Path, [string]$Label, [switch]$IsDir)
    if ($IsDir) {
        if (Test-Path $Path) {
            try {
                Remove-Item -Recurse -Force $Path -ErrorAction Stop
                Write-Host "[OK] Removed $Label"
            } catch {
                Remove-Item -Recurse -Force $Path -ErrorAction SilentlyContinue
                if (Test-Path $Path) {
                    Write-Host "[!!] Partially removed $Label (some files locked by another process)"
                } else {
                    Write-Host "[OK] Removed $Label"
                }
            }
        } else {
            Write-Host "[--] $Label not found (already clean)"
        }
    } else {
        if (Test-Path $Path) {
            try {
                Remove-Item -Force $Path -ErrorAction Stop
                Write-Host "[OK] Removed $Label"
            } catch {
                Write-Host "[!!] Could not remove $Label (locked by another process)"
            }
        } else {
            Write-Host "[--] $Label not found (already clean)"
        }
    }
}

function Remove-PyCache {
    param([string]$Dir, [string]$Label)
    $caches = Get-ChildItem -Path $Dir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
    if ($caches.Count -gt 0) {
        $caches | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Removed $($caches.Count) __pycache__\ dirs under $Label"
    } else {
        Write-Host "[--] No __pycache__\ dirs under $Label"
    }
}

# ── Shared steps ─────────────────────────────────────────────────

function Invoke-GenerateConfig {
    python (Join-Path $ManagerDir "setup.py") --config-only 2>$null
    if ($LASTEXITCODE -ne 0) {
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
}

function Invoke-InstallDeps {
    $reqFile = Join-Path $ManagerDir "requirements.txt"
    if (Test-Path $reqFile) {
        pip install -q -r $reqFile
        Write-Host "[OK] Manager dependencies installed"
    }
}

function Invoke-RunMigrations {
    python $ManagePy migrate
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Migrations failed"
        exit 1
    }
    Write-Host "[OK] Migrations applied"
}

function Invoke-BuildFrontend {
    if (-not (Test-Path $FrontendDir)) {
        Write-Host "[SKIP] Frontend directory not found"
        return
    }

    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "Installing frontend dependencies..."
        npm install --prefix $FrontendDir
    }

    Write-Host "Building Angular frontend..."
    npm run build --prefix $FrontendDir
    Write-Host "[OK] Frontend built"
}

function Invoke-CollectStatic {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found - cannot collect static files"
        return
    }
    python $ManagePy collectstatic --noinput
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] collectstatic failed"
        exit 1
    }
    Write-Host "[OK] Static files collected"
}

# ── dev ──────────────────────────────────────────────────────────

function Invoke-Dev {
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn - Development Environment"
    Write-Host "============================================================"

    # Step 0: Clean (optional)
    if ($Clean) {
        Write-Host ""
        Write-Host "--- Cleaning all artifacts ---"
        Invoke-CleanManager
        Write-Host ""
        Invoke-CleanWorker
        Write-Host ""
        Invoke-CleanShared
        Write-Host ""
        Write-Host "[OK] Clean complete."
    }

    # Step 1: Configuration
    Write-Host ""
    Write-Host "--- Configuration ---"
    Invoke-GenerateConfig

    # Step 2: Python dependencies
    Write-Host ""
    Write-Host "--- Python dependencies ---"
    Invoke-InstallDeps

    # Step 3: Database
    Write-Host ""
    Write-Host "--- Database migrations ---"
    Invoke-RunMigrations

    # Step 4: Admin user (only if none exists)
    python $ManagePy shell -c "from django.contrib.auth import get_user_model; exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "--- Create admin account ---"
        try {
            python $ManagePy createsuperuser
        } catch {
            Write-Host "[SKIP] Admin creation skipped"
        }
    } else {
        Write-Host "[OK] Admin account exists"
    }

    # Step 5: Frontend
    Write-Host ""
    Write-Host "--- Frontend build ---"
    Invoke-BuildFrontend

    # Step 6: Static files
    Write-Host ""
    Write-Host "--- Static files ---"
    Invoke-CollectStatic

    # Step 7: Start
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Starting Sethlans Manager on port 7075"
    Write-Host "============================================================"
    Write-Host ""
    python $ManagePy runserver 7075
}

# ── setup ────────────────────────────────────────────────────────

function Invoke-Setup {
    Write-Host "============================================================"
    Write-Host "  Sethlans Manager - First Time Setup"
    Write-Host "============================================================"

    Write-Host ""
    Write-Host "--- Step 1: Configuration ---"
    Invoke-GenerateConfig

    Write-Host ""
    Write-Host "--- Step 2: Python dependencies ---"
    Invoke-InstallDeps

    Write-Host ""
    Write-Host "--- Step 3: Database migrations ---"
    Invoke-RunMigrations

    Write-Host ""
    Write-Host "--- Step 4: Create admin account ---"
    Write-Host "(Skip with Ctrl+C if admin already exists)"
    Write-Host ""
    try {
        python $ManagePy createsuperuser
    } catch {
        Write-Host "[SKIP] Admin creation skipped or user already exists"
    }

    Write-Host ""
    Write-Host "--- Step 5: Frontend build ---"
    Invoke-BuildFrontend

    Write-Host ""
    Write-Host "--- Step 6: Static files ---"
    Invoke-CollectStatic

    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Setup complete! Run: .\tools\sethlans.ps1 start manager"
    Write-Host "============================================================"
}

# ── start ────────────────────────────────────────────────────────

function Invoke-StartManager {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 setup"
        exit 1
    }

    Write-Host "--- Applying migrations ---"
    python $ManagePy migrate

    Write-Host ""
    Write-Host "--- Starting Sethlans Manager ---"
    python $ManagePy runserver 7075
}

function Invoke-StartWorker {
    $workerConfig = Join-Path $WorkerDir "config.ini"
    if (-not (Test-Path $workerConfig)) {
        Write-Host "[ERROR] worker\config.ini not found."
        Write-Host ""
        Write-Host "Create it from the example:"
        Write-Host "  Copy-Item $WorkerDir\config.ini.example $workerConfig"
        Write-Host ""
        Write-Host "Then set the enrollment_key from the manager setup output."
        exit 1
    }

    Write-Host "--- Starting Sethlans Worker Agent ---"
    python (Join-Path $WorkerDir "run_worker.py")
}

# ── build ────────────────────────────────────────────────────────

function Invoke-Build {
    if (-not (Test-Path $FrontendDir)) {
        Write-Host "[ERROR] Frontend directory not found at $FrontendDir"
        exit 1
    }

    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 setup"
        exit 1
    }

    Write-Host "--- Frontend ---"
    Invoke-BuildFrontend

    Write-Host ""
    Write-Host "--- Static files ---"
    Invoke-CollectStatic
}

# ── clean ────────────────────────────────────────────────────────

function Invoke-CleanManager {
    Write-Host "--- Manager artifacts ---"
    Remove-IfExists -Path $ConfigFile -Label "manager.ini"
    Remove-IfExists -Path (Join-Path $ManagerDir "db.sqlite3") -Label "db.sqlite3"
    Remove-IfExists -Path (Join-Path $ManagerDir "db.sqlite3-journal") -Label "db.sqlite3-journal"
    Remove-IfExists -Path (Join-Path $ManagerDir "staticfiles") -Label "staticfiles\" -IsDir
    Remove-IfExists -Path (Join-Path $FrontendDir "dist") -Label "frontend\dist\" -IsDir
    Remove-IfExists -Path (Join-Path $FrontendDir ".angular") -Label "frontend\.angular\" -IsDir
    Remove-IfExists -Path (Join-Path $FrontendDir "node_modules") -Label "frontend\node_modules\" -IsDir
    Remove-IfExists -Path (Join-Path $ManagerDir "logs") -Label "manager\logs\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot "media") -Label "media\" -IsDir
    Remove-PyCache -Dir $ManagerDir -Label "manager\"
}

function Invoke-CleanWorker {
    Write-Host "--- Worker artifacts ---"
    Remove-IfExists -Path (Join-Path $WorkerDir "config.ini") -Label "config.ini"
    Remove-IfExists -Path (Join-Path $AgentDir "managed_tools") -Label "managed_tools\" -IsDir
    Remove-IfExists -Path (Join-Path $AgentDir "managed_assets") -Label "managed_assets\" -IsDir
    Remove-IfExists -Path (Join-Path $AgentDir "worker_output") -Label "worker_output\" -IsDir
    Remove-IfExists -Path (Join-Path $AgentDir "temp") -Label "temp\" -IsDir
    Remove-IfExists -Path (Join-Path $AgentDir "logs") -Label "worker\logs\" -IsDir
    Remove-PyCache -Dir $WorkerDir -Label "worker\"
}

function Invoke-CleanShared {
    Write-Host "--- Shared artifacts ---"
    Remove-IfExists -Path (Join-Path $ProjectRoot "temp") -Label "temp\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot ".pytest_cache") -Label ".pytest_cache\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot "sethlans_e2e_cache") -Label "sethlans_e2e_cache\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot "test_e2e_db.sqlite3") -Label "test_e2e_db.sqlite3"
    Remove-IfExists -Path (Join-Path $ProjectRoot "test_artifacts") -Label "test_artifacts\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot "manual_test_output") -Label "manual_test_output\" -IsDir
    Remove-IfExists -Path (Join-Path $ProjectRoot "tools" "results") -Label "tools\results\" -IsDir
}

function Invoke-Clean {
    param([string]$CleanTarget)

    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn - Clean ($CleanTarget)"
    Write-Host "============================================================"
    Write-Host ""

    if (-not $Force) {
        $response = Read-Host "Remove all $CleanTarget artifacts? [y/N]"
        if ($response -ne "y" -and $response -ne "Y") {
            Write-Host "Aborted."
            exit 0
        }
        Write-Host ""
    }

    switch ($CleanTarget) {
        "manager" { Invoke-CleanManager }
        "worker"  { Invoke-CleanWorker }
        "all" {
            Invoke-CleanManager
            Write-Host ""
            Invoke-CleanWorker
            Write-Host ""
            Invoke-CleanShared
        }
    }

    Write-Host ""
    Write-Host "[OK] Clean complete ($CleanTarget)."
}

# ── Main dispatch ────────────────────────────────────────────────

switch ($Command) {
    "dev"   { Invoke-Dev }
    "setup" { Invoke-Setup }
    "start" {
        switch ($Target) {
            "manager" { Invoke-StartManager }
            "worker"  { Invoke-StartWorker }
            default {
                Write-Host "[ERROR] Usage: .\tools\sethlans.ps1 start [manager|worker]"
                exit 1
            }
        }
    }
    "build" { Invoke-Build }
    "clean" {
        switch ($Target) {
            "manager" { Invoke-Clean "manager" }
            "worker"  { Invoke-Clean "worker" }
            default   { Invoke-Clean "all" }
        }
    }
    default {
        Show-Usage
        exit 1
    }
}
