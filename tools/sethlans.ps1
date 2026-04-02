<#
.SYNOPSIS
    Unified CLI for Sethlans Reborn development.

.DESCRIPTION
    Commands:
      dev     Full dev environment: config + deps + db + admin + frontend + start
      clean   Nuke all development artifacts (no confirmation)
      start   Start manager + worker (assumes already set up)

.EXAMPLE
    .\tools\sethlans.ps1 dev
    .\tools\sethlans.ps1 clean
    .\tools\sethlans.ps1 start
#>

param(
    [Parameter(Position = 0)]
    [string]$Command
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
    Write-Host "Usage: .\tools\sethlans.ps1 <command>"
    Write-Host ""
    Write-Host "  dev     Setup everything from scratch and start services"
    Write-Host "  clean   Remove all development artifacts"
    Write-Host "  start   Start manager + worker (must run dev first)"
}

function Invoke-GenerateConfig {
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
"@
}

function Invoke-ConfigureWorker {
    $workerConfig = Join-Path $WorkerDir "config.ini"
    if (Test-Path $workerConfig) {
        Write-Host "[OK] Worker config.ini already exists"
        return
    }

    $enrollmentKey = python -c @"
import configparser
c = configparser.ConfigParser()
c.read(r'$ConfigFile')
print(c.get('security', 'enrollment_key', fallback=''))
"@ 2>$null

    if (-not $enrollmentKey -or $enrollmentKey -eq "") {
        Write-Host "[SKIP] No enrollment key - configure worker manually"
        return
    }

    Copy-Item (Join-Path $WorkerDir "config.ini.example") $workerConfig
    python -c @"
import configparser
c = configparser.ConfigParser()
c.read(r'$workerConfig')
c.set('manager', 'enrollment_key', '$enrollmentKey')
with open(r'$workerConfig', 'w') as f:
    c.write(f)
"@
    Write-Host "[OK] Worker config.ini created with enrollment key"
}

function Invoke-StartServices {
    Write-Host ""
    Write-Host "--- Starting manager (background) on port 7075 ---"

    $managerProcess = Start-Process -FilePath "python" -ArgumentList "$ManagePy", "runserver", "7075" `
        -PassThru -NoNewWindow

    Start-Sleep -Seconds 2

    if ($managerProcess.HasExited) {
        Write-Host "[ERROR] Manager failed to start"
        exit 1
    }
    Write-Host "[OK] Manager running (PID $($managerProcess.Id))"

    $workerConfig = Join-Path $WorkerDir "config.ini"
    try {
        if (Test-Path $workerConfig) {
            Write-Host ""
            Write-Host "--- Starting worker (foreground) ---"
            python (Join-Path $WorkerDir "run_worker.py")
        } else {
            Write-Host "[!!] No worker config - manager running alone. Ctrl+C to stop."
            Wait-Process -Id $managerProcess.Id
        }
    } finally {
        if (-not $managerProcess.HasExited) {
            Stop-Process -Id $managerProcess.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] Manager stopped"
        }
    }
}

# ── dev ──────────────────────────────────────────────────────────

function Invoke-Dev {
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn - Dev Environment"
    Write-Host "============================================================"

    Write-Host ""
    Write-Host "--- Configuration ---"
    Invoke-GenerateConfig

    Write-Host ""
    Write-Host "--- Python dependencies ---"
    pip install -r (Join-Path $ManagerDir "requirements.txt") -r (Join-Path $ProjectRoot "requirements-dev.txt")

    Write-Host ""
    Write-Host "--- Database migrations ---"
    python $ManagePy migrate
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Migrations failed"; exit 1 }

    Write-Host ""
    Write-Host "--- Admin account ---"
    $env:DJANGO_SUPERUSER_PASSWORD = "test12345"
    python $ManagePy createsuperuser --username testuser --email "" --noinput 2>$null
    Remove-Item Env:\DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
    Write-Host "[OK] Admin ready (testuser / test12345)"

    if (Test-Path $FrontendDir) {
        Write-Host ""
        Write-Host "--- Frontend ---"
        $env:NG_CLI_ANALYTICS = "false"
        if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
            npm install --prefix $FrontendDir --no-progress --no-fund --no-audit
        }
        npm run build --prefix $FrontendDir --no-progress
        Remove-Item Env:\NG_CLI_ANALYTICS -ErrorAction SilentlyContinue
        Write-Host "[OK] Frontend built"
    }

    Write-Host ""
    Write-Host "--- Static files ---"
    python $ManagePy collectstatic --noinput
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] collectstatic failed"; exit 1 }

    Write-Host ""
    Write-Host "--- Worker configuration ---"
    Invoke-ConfigureWorker

    Invoke-StartServices
}

# ── clean ────────────────────────────────────────────────────────

function Invoke-Clean {
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn - Clean"
    Write-Host "============================================================"
    Write-Host ""

    # Manager
    foreach ($f in @($ConfigFile, (Join-Path $ManagerDir "db.sqlite3"), (Join-Path $ManagerDir "db.sqlite3-journal"))) {
        if (Test-Path $f) { Remove-Item -Force $f -ErrorAction SilentlyContinue }
    }
    foreach ($d in @(
        (Join-Path $ManagerDir "staticfiles"), (Join-Path $ManagerDir "logs"),
        (Join-Path $FrontendDir "dist"), (Join-Path $FrontendDir ".angular"), (Join-Path $FrontendDir "node_modules"),
        (Join-Path $ProjectRoot "media")
    )) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $ManagerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Manager artifacts removed"

    # Worker
    $wc = Join-Path $WorkerDir "config.ini"
    if (Test-Path $wc) { Remove-Item -Force $wc -ErrorAction SilentlyContinue }
    foreach ($d in @("managed_tools", "managed_assets", "worker_output", "temp", "logs")) {
        $p = Join-Path $AgentDir $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $WorkerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Worker artifacts removed"

    # Shared
    foreach ($item in @("temp", ".pytest_cache", "sethlans_e2e_cache", "test_artifacts", "manual_test_output")) {
        $p = Join-Path $ProjectRoot $item
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
    $testDb = Join-Path $ProjectRoot "test_e2e_db.sqlite3"
    if (Test-Path $testDb) { Remove-Item -Force $testDb -ErrorAction SilentlyContinue }
    $toolsResults = Join-Path $ProjectRoot "tools" "results"
    if (Test-Path $toolsResults) { Remove-Item -Recurse -Force $toolsResults -ErrorAction SilentlyContinue }
    Write-Host "[OK] Shared artifacts removed"

    Write-Host ""
    Write-Host "[OK] Clean complete."
}

# ── start ────────────────────────────────────────────────────────

function Invoke-Start {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 dev"
        exit 1
    }

    Write-Host "--- Applying migrations ---"
    python $ManagePy migrate

    Invoke-StartServices
}

# ── Main dispatch ────────────────────────────────────────────────

switch ($Command) {
    "dev"   { Invoke-Dev }
    "clean" { Invoke-Clean }
    "start" { Invoke-Start }
    default { Show-Usage; exit 1 }
}
