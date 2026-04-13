# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
# Unified CLI for Sethlans Reborn development.
# Usage: .\tools\sethlans.ps1 {dev|clean|start|manager|worker|stop|status}
param([Parameter(Position = 0)] [string]$Command)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$ManagerDir = Join-Path $ProjectRoot "manager"
$FrontendDir = Join-Path $ManagerDir "frontend"
$ConfigFile = Join-Path $ManagerDir "manager.ini"
$ManagePy = Join-Path $ManagerDir "manage.py"
$WorkerDir = Join-Path $ProjectRoot "worker"
$AgentDir = Join-Path $WorkerDir "sethlans_worker_agent"
$PidDir = Join-Path $ProjectRoot ".pids"

# -- Helpers ---------------------------------------------------------------
function Show-Usage {
    Write-Host "Usage: .\tools\sethlans.ps1 <command>"
    Write-Host "  dev      Setup everything from scratch and start services"
    Write-Host "  clean    Remove all development artifacts"
    Write-Host "  start    Start manager + worker (must run dev first)"
    Write-Host "  manager  Start manager in the background"
    Write-Host "  worker   Start worker in the background"
    Write-Host "  stop     Stop background manager and/or worker"
    Write-Host "  status   Show running manager/worker processes"
}
function Ensure-Dirs {
    if (-not (Test-Path $PidDir)) { New-Item -ItemType Directory -Path $PidDir -Force | Out-Null }
}
function Get-SavedPid($name) {
    $pidFile = Join-Path $PidDir "$name.pid"
    if (Test-Path $pidFile) {
        $savedId = (Get-Content $pidFile -Raw).Trim()
        if ($savedId -and (Get-Process -Id $savedId -ErrorAction SilentlyContinue)) {
            return [int]$savedId
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    return $null
}
function Save-Pid($name, $procId) {
    Ensure-Dirs; Set-Content -Path (Join-Path $PidDir "$name.pid") -Value $procId
}
function Remove-Pid($name) {
    $pidFile = Join-Path $PidDir "$name.pid"
    if (Test-Path $pidFile) { Remove-Item $pidFile -ErrorAction SilentlyContinue }
}
function Read-EnrollmentKey {
    python -c @"
import configparser; c = configparser.ConfigParser(); c.read(r'$ConfigFile')
print(c.get('security', 'enrollment_key', fallback=''))
"@ 2>$null
}

function Invoke-GenerateConfig {
    python -c @"
import sys, os, configparser, secrets
from pathlib import Path
sys.path.insert(0, r'$ManagerDir')
os.environ['DJANGO_SETTINGS_MODULE'] = 'sethlans_manager.settings'
from workers.enrollment_key import generate_key
config_path = Path(r'$ConfigFile')
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
"@
}

function Invoke-StartServices {
    Write-Host ""; Invoke-Manager; Write-Host ""; Invoke-Worker; Write-Host ""; Invoke-Status
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Manager UI:  https://127.0.0.1:8080"
    Write-Host "  Swagger API: https://127.0.0.1:8080/api/docs/"
    Write-Host "  Admin login: testuser / test12345"
    Write-Host "============================================================"
}

# -- Sethlans process filter (used by clean) -------------------------------
function Get-SethlansProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            $cmd -and ($cmd -like "*$ProjectRoot*") -and (
                ($cmd -like "*manage.py*runserver*") -or
                ($cmd -like "*run_manager.py*") -or
                ($cmd -like "*run_worker.py*")
            )
        }
}

# -- dev -------------------------------------------------------------------
function Invoke-Dev {
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn -- Dev Environment"
    Write-Host "============================================================"
    Write-Host ""; Write-Host "--- Configuration ---"
    Invoke-GenerateConfig
    Write-Host ""; Write-Host "--- Python dependencies ---"
    pip install -r (Join-Path $ManagerDir "requirements.txt") `
                -r (Join-Path $WorkerDir "requirements.txt") `
                -r (Join-Path $ProjectRoot "requirements-dev.txt")
    Write-Host ""; Write-Host "--- Database migrations ---"
    python $ManagePy migrate
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Migrations failed"; exit 1 }
    Write-Host ""; Write-Host "--- Admin account ---"
    $env:DJANGO_SUPERUSER_PASSWORD = "test12345"
    python $ManagePy createsuperuser --username testuser --email "" --noinput 2>$null
    Remove-Item Env:\DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
    Write-Host "[OK] Admin ready (testuser / test12345)"
    if (Test-Path $FrontendDir) {
        Write-Host ""; Write-Host "--- Frontend ---"
        $env:NG_CLI_ANALYTICS = "false"
        Push-Location $FrontendDir
        if (-not (Test-Path "node_modules")) {
            npm install --no-progress --no-fund --no-audit
        }
        npm run build --no-progress
        Pop-Location
        Remove-Item Env:\NG_CLI_ANALYTICS -ErrorAction SilentlyContinue
        Write-Host "[OK] Frontend built"
    }
    Invoke-StartServices
}

# -- clean -----------------------------------------------------------------
function Invoke-Clean {
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn -- Clean"
    Write-Host "============================================================"
    Write-Host ""
    # Stop sethlans python processes scoped to this project root
    $victims = Get-SethlansProcesses
    foreach ($v in $victims) {
        Write-Host "[KILL] Stopping PID $($v.ProcessId): $($v.CommandLine)"
        Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($victims) { Start-Sleep -Milliseconds 500 }
    Remove-Pid "manager"; Remove-Pid "worker"
    # Manager artifacts
    $dbFile = Join-Path $ManagerDir "db.sqlite3"
    foreach ($f in @($ConfigFile, (Join-Path $ManagerDir "db.sqlite3-journal"))) {
        if (Test-Path $f) { Remove-Item -Force $f -ErrorAction SilentlyContinue }
    }
    if (Test-Path $dbFile) {
        try { Remove-Item -Force $dbFile -ErrorAction Stop }
        catch {
            Write-Host "[ERROR] Failed to delete database file: $dbFile"
            Write-Host "        $($_.Exception.Message)"
            $still = Get-SethlansProcesses
            if ($still) {
                $pids = ($still | ForEach-Object { $_.ProcessId }) -join ", "
                Write-Host "        Sethlans python processes still running: PID(s) $pids"
            }
            exit 1
        }
    }
    foreach ($d in @(
        (Join-Path $ManagerDir "staticfiles"), (Join-Path $ManagerDir "logs"),
        (Join-Path $ManagerDir "tls"),
        (Join-Path $FrontendDir "dist"), (Join-Path $FrontendDir ".angular"),
        (Join-Path $FrontendDir "node_modules"), (Join-Path $ManagerDir "media")
    )) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $ManagerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Manager artifacts removed"
    # Worker (in-tree only; worker state now lives in OS data dir)
    foreach ($d in @("managed_tools", "managed_assets", "worker_output", "temp", "logs")) {
        $p = Join-Path $AgentDir $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $WorkerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Worker in-tree artifacts removed"
    Write-Host "     NOTE: Worker state is in the OS data dir (%LOCALAPPDATA%\Sethlans\worker)."
    Write-Host "           Remove it manually if a full reset is needed."
    # Shared artifacts
    foreach ($item in @("temp", ".pytest_cache", "sethlans_e2e_cache", "test_artifacts", "manual_test_output")) {
        $p = Join-Path $ProjectRoot $item
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
    $testDb = Join-Path $ProjectRoot "test_e2e_db.sqlite3"
    if (Test-Path $testDb) { Remove-Item -Force $testDb -ErrorAction SilentlyContinue }
    $toolsResults = Join-Path (Join-Path $ProjectRoot "tools") "results"
    if (Test-Path $toolsResults) { Remove-Item -Recurse -Force $toolsResults -ErrorAction SilentlyContinue }
    Write-Host "[OK] Shared artifacts removed"
    Write-Host ""; Write-Host "[OK] Clean complete."
}

# -- start -----------------------------------------------------------------
function Invoke-Start {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    Invoke-StartServices
}

# -- manager ---------------------------------------------------------------
function Invoke-Manager {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    $existing = Get-SavedPid "manager"
    if ($existing) { Write-Host "[OK] Manager already running (PID $existing)"; return }
    Ensure-Dirs
    $outLog = Join-Path $env:TEMP "sethlans_manager_out.log"
    $errLog = Join-Path $env:TEMP "sethlans_manager_err.log"
    $proc = Start-Process -FilePath "python" `
        -ArgumentList (Join-Path $ManagerDir "run_manager.py") `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { Write-Host "[ERROR] Manager failed to start"; exit 1 }
    Save-Pid "manager" $proc.Id
    Write-Host "[OK] Manager started in background (PID $($proc.Id))"
    Write-Host "     Stop: .\tools\sethlans.ps1 stop"
}

# -- worker ----------------------------------------------------------------
function Invoke-Worker {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] manager.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    $existing = Get-SavedPid "worker"
    if ($existing) { Write-Host "[OK] Worker already running (PID $existing)"; return }
    $enrollmentKey = Read-EnrollmentKey
    if (-not $enrollmentKey -or $enrollmentKey -eq "") {
        Write-Host "[ERROR] No enrollment key found in manager.ini"; exit 1
    }
    Ensure-Dirs
    $outLog = Join-Path $env:TEMP "sethlans_worker_out.log"
    $errLog = Join-Path $env:TEMP "sethlans_worker_err.log"
    # Set env vars for worker enrollment
    $env:SETHLANS_WORKER_ENROLLMENT_KEY = $enrollmentKey
    $env:SETHLANS_MANAGER_HOST = "127.0.0.1"
    $env:SETHLANS_MANAGER_PORT = "8080"
    $env:SETHLANS_IDLE_DETECTION_ENABLED = "false"
    # Create an empty file for stdin so isatty() returns False (unattended wizard)
    $stdinFile = Join-Path $env:TEMP "sethlans_worker_stdin.txt"
    Set-Content -Path $stdinFile -Value ""
    $proc = Start-Process -FilePath "python" `
        -ArgumentList (Join-Path $WorkerDir "run_worker.py") `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -RedirectStandardInput $stdinFile
    # Clean up env vars from current shell
    foreach ($v in @("SETHLANS_WORKER_ENROLLMENT_KEY", "SETHLANS_MANAGER_HOST",
                     "SETHLANS_MANAGER_PORT", "SETHLANS_IDLE_DETECTION_ENABLED")) {
        Remove-Item "Env:\$v" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { Write-Host "[ERROR] Worker failed to start"; exit 1 }
    Save-Pid "worker" $proc.Id
    Write-Host "[OK] Worker started in background (PID $($proc.Id))"
    Write-Host "     Stop: .\tools\sethlans.ps1 stop"
}

# -- stop ------------------------------------------------------------------
function Invoke-Stop {
    $stopped = $false
    $managerPid = Get-SavedPid "manager"
    if ($managerPid) {
        Stop-Process -Id $managerPid -Force -ErrorAction SilentlyContinue
        Remove-Pid "manager"; Write-Host "[OK] Manager stopped (PID $managerPid)"; $stopped = $true
    }
    $workerPid = Get-SavedPid "worker"
    if ($workerPid) {
        Stop-Process -Id $workerPid -Force -ErrorAction SilentlyContinue
        Remove-Pid "worker"; Write-Host "[OK] Worker stopped (PID $workerPid)"; $stopped = $true
    }
    if (-not $stopped) { Write-Host "[OK] No running services found" }
}

# -- status ----------------------------------------------------------------
function Invoke-Status {
    $managerPid = Get-SavedPid "manager"
    if ($managerPid) { Write-Host "Manager:  running (PID $managerPid)" }
    else { Write-Host "Manager:  not running" }
    $workerPid = Get-SavedPid "worker"
    if ($workerPid) { Write-Host "Worker:   running (PID $workerPid)" }
    else { Write-Host "Worker:   not running" }
}

# -- Main dispatch ---------------------------------------------------------
switch ($Command) {
    "dev"     { Invoke-Dev }
    "clean"   { Invoke-Clean }
    "start"   { Invoke-Start }
    "manager" { Invoke-Manager }
    "worker"  { Invoke-Worker }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    default   { Show-Usage; exit 1 }
}
