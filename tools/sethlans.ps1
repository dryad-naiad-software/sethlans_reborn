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
# Usage: .\tools\sethlans.ps1 {dev|clean [-All]|start|manager|worker|stop|status}
#
# `clean` removes the development artifacts inside the repo; `clean -All`
# (or `clean --all`) also removes the per-user data folder.
param(
    [Parameter(Position = 0)] [string]$Command,
    [switch]$All,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]]$ExtraArgs
)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$ManagerDir = Join-Path $ProjectRoot "manager"
$FrontendDir = Join-Path $ManagerDir "frontend"
$ConfigFile = Join-Path $ManagerDir "sethlans.ini"
$ManagePy = Join-Path $ManagerDir "manage.py"
$WorkerDir = Join-Path $ProjectRoot "worker"
$AgentDir = Join-Path $WorkerDir "sethlans_worker_agent"
# Keep all worker runtime state (logs, tools, assets, sentinel, TLS,
# enrollment config) inside the repo in dev mode so `clean` is a
# trivial rm and logs are next to the code. Honoured by the worker's
# config_store.get_data_dir() via SETHLANS_WORKER_DATA_DIR.
$WorkerDataDir = Join-Path $WorkerDir ".dev-data"
$PidDir = Join-Path $ProjectRoot ".pids"
$CaddyDir = Join-Path $ProjectRoot ".venv-build\caddy"
$CaddyBin = Join-Path $CaddyDir "caddy.exe"
$Caddyfile = Join-Path $ManagerDir "caddy\Caddyfile"
$DevBootstrap = Join-Path $ScriptDir "_dev_bootstrap.py"

# Port layout — mirrors production defaults in sethlans.ini.example.
$PublicTlsPort = 8080
$CaddyLoopbackPort = 8089
$WaitressPublicPort = 8090
$WaitressInternalPort = 8088

# -- Helpers ---------------------------------------------------------------
function Show-Usage {
    Write-Host "Usage: .\tools\sethlans.ps1 <command>"
    Write-Host "  dev      Setup everything from scratch and start services"
    Write-Host "  clean    Remove the development artifacts inside the repo"
    Write-Host "           -All (or --all)  also remove the per-user data folder"
    Write-Host "                            (identity files of an installed manager/worker)"
    Write-Host "  start    Start caddy + manager + worker (must run dev first)"
    Write-Host "  manager  Start caddy + manager in the background"
    Write-Host "  worker   Start worker in the background"
    Write-Host "  stop     Stop background caddy + manager + worker"
    Write-Host "  status   Show running caddy/manager/worker processes"
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
    # Canonical key from the ManagerSettings DB row.
    python $DevBootstrap enrollment-key --manager-dir $ManagerDir 2>$null
}

function Ensure-CaddyBinary {
    if (Test-Path $CaddyBin) { return }
    Write-Host "--- Fetching Caddy binary into $CaddyDir ---"
    python (Join-Path $ScriptDir "fetch_caddy.py") --target-dir $CaddyDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Caddy fetch failed (exit $LASTEXITCODE)"; exit 1
    }
}

function Render-Caddyfile {
    python $DevBootstrap render-caddyfile `
        --manager-dir $ManagerDir `
        --public-tls-port $PublicTlsPort `
        --loopback-plaintext-port $CaddyLoopbackPort `
        --waitress-public-port $WaitressPublicPort `
        --waitress-internal-port $WaitressInternalPort `
        | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Caddyfile render failed (exit $LASTEXITCODE)"; exit 1
    }
    Write-Host "[OK] Caddyfile rendered: $Caddyfile"
}

function Invoke-GenerateConfig {
    # Delegated to the Python helper for symmetry with the bash script
    # (both call the same backend, same behaviour on Windows + POSIX).
    python $DevBootstrap generate-config `
        --manager-dir $ManagerDir `
        --port $PublicTlsPort
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Config generation failed (exit $LASTEXITCODE)"; exit 1
    }
}

function Invoke-StartServices {
    Write-Host ""; Invoke-Manager; Write-Host ""; Invoke-Worker; Write-Host ""; Invoke-Status
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Manager UI:  https://127.0.0.1:$PublicTlsPort (Caddy TLS)"
    Write-Host "  Worker UI:   https://127.0.0.1:8081 (Caddy TLS)"
    Write-Host "  Swagger API: https://127.0.0.1:$PublicTlsPort/api/docs/"
    Write-Host "  Admin login: testuser / test12345"
    Write-Host "============================================================"
}

# -- Sethlans process filter (used by clean) -------------------------------
# Command lines are compared as fixed strings with '\' turned into '/' and
# case-insensitively: this script starts processes with D:\... paths, Git
# Bash starts them with D:/... paths. The match is on the full script /
# Caddyfile path under this checkout's root, anchored at the start of an
# argument (start of the line, or after a space or quote), so another
# checkout (including a sibling folder whose name starts with this one's,
# or a folder that merely ends with this path) never matches.
function ConvertTo-CmdKey([string]$Text) {
    return ($Text -replace '\\', '/').ToLowerInvariant()
}
$RootKey = (ConvertTo-CmdKey $ProjectRoot).TrimEnd('/')

function Test-ArgStartsWith([string]$Key, [string]$Path) {
    return $Key.StartsWith($Path) -or $Key.Contains(" $Path") -or
        $Key.Contains("`"$Path") -or $Key.Contains("'$Path")
}

function Test-OurPythonCmd([string]$CommandLine) {
    if (-not $CommandLine) { return $false }
    $c = ConvertTo-CmdKey $CommandLine
    if (Test-ArgStartsWith $c "$RootKey/manager/run_manager.py") { return $true }
    if (Test-ArgStartsWith $c "$RootKey/worker/run_worker.py") { return $true }
    if (-not (Test-ArgStartsWith $c "$RootKey/manager/manage.py")) { return $false }
    $i = $c.IndexOf("$RootKey/manager/manage.py")
    return $c.IndexOf("runserver", $i) -ge 0
}

function Test-OurCaddyCmd([string]$CommandLine) {
    if (-not $CommandLine) { return $false }
    return Test-ArgStartsWith (ConvertTo-CmdKey $CommandLine) "$RootKey/manager/caddy/caddyfile"
}

# True when PID $ProcId is this checkout's $Kind (python or caddy): image
# name python.exe / caddy.exe and a command line that passes the matcher.
# A process whose command line cannot be read is not ours.
function Test-OurPid([int]$ProcId, [string]$Kind) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    if ($Kind -eq "caddy") { return ($p.Name -eq "caddy.exe") -and (Test-OurCaddyCmd $p.CommandLine) }
    return ($p.Name -eq "python.exe") -and (Test-OurPythonCmd $p.CommandLine)
}

function Get-SethlansProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { Test-OurPythonCmd $_.CommandLine }
}

function Get-CaddyProcesses {
    Get-CimInstance Win32_Process -Filter "Name='caddy.exe'" -ErrorAction SilentlyContinue |
        Where-Object { Test-OurCaddyCmd $_.CommandLine }
}

# -- Per-user data folder (used by clean) ----------------------------------
# Identity files (TLS keys, secret key, worker enrollment) of an installed
# manager or worker on this account. Returns @{ Path; Error }: Error names
# the reason when LOCALAPPDATA gives no safe absolute path. An empty or
# relative LOCALAPPDATA is refused, never resolved against the current
# directory. [IO.Path] is used instead of Join-Path, which fails on a
# drive that does not exist.
function Resolve-UserDataDir {
    $name = "Sethlans"
    $base = $env:LOCALAPPDATA
    if (-not $base) { return @{ Path = $null; Error = "LOCALAPPDATA is empty" } }
    if ($base -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)') {
        return @{ Path = $null; Error = "LOCALAPPDATA is not an absolute path: '$base'" }
    }
    $dir = [System.IO.Path]::Combine($base, $name)
    if ([System.IO.Path]::GetFileName($dir) -cne $name) {
        return @{ Path = $null; Error = "last folder of '$dir' is not '$name'" }
    }
    return @{ Path = $dir; Error = $null }
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
    Write-Host ""; Write-Host "--- Caddy binary ---"
    Ensure-CaddyBinary
    Write-Host ""; Write-Host "--- Database migrations ---"
    # Migration 0017 seeds the ManagerSettings row with a fresh
    # enrollment key the first time it runs — no extra step needed.
    python $ManagePy migrate
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Migrations failed"; exit 1 }
    Write-Host ""; Write-Host "--- Admin account ---"
    # SetupGateMiddleware's defense-in-depth treats "superuser exists"
    # as equivalent to sentinel-present, so the gate stays open.
    $env:DJANGO_SUPERUSER_PASSWORD = "test12345"
    python $ManagePy createsuperuser --username testuser --email "" --noinput 2>$null
    Remove-Item Env:\DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
    Write-Host "[OK] Admin ready (testuser / test12345)"
    Write-Host ""; Write-Host "--- Caddyfile ---"
    Render-Caddyfile
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
function Invoke-Clean([bool]$RemoveUserDir) {
    $userDir = Resolve-UserDataDir
    # A junction or symlink as the folder itself is refused. Defence in
    # depth: Remove-Item -LiteralPath -Recurse -Force was checked not to
    # follow junctions (Windows PowerShell 5.1.26100, pwsh 7.6), but a link
    # here means the folder is not what clean expects. GetAttributes does
    # not follow the link, so a dangling one is caught too.
    if ($RemoveUserDir -and -not $userDir.Error) {
        $attrs = $null
        try { $attrs = [System.IO.File]::GetAttributes($userDir.Path) } catch { $attrs = $null }
        if ($null -ne $attrs -and ($attrs -band [System.IO.FileAttributes]::ReparsePoint)) {
            $userDir.Error = "$($userDir.Path) is a symbolic link or junction"
        }
    }
    # Refuse before touching anything when -All cannot name a safe folder.
    if ($RemoveUserDir -and $userDir.Error) {
        Write-Host "[ERROR] Refusing to remove the per-user data folder: $($userDir.Error)"
        Write-Host "        Nothing was removed."
        exit 1
    }
    Write-Host "============================================================"
    Write-Host "  Sethlans Reborn -- Clean"
    Write-Host "============================================================"
    Write-Host ""
    # Stop tracked services via PID files first (fast, direct, no WMI).
    Invoke-Stop
    Start-Sleep -Milliseconds 500
    # Belt-and-suspenders: WMI sweep for any orphaned run_manager.py /
    # run_worker.py / Caddy instances from prior sessions not covered
    # by PID files.
    $victims = Get-SethlansProcesses
    foreach ($v in $victims) {
        Write-Host "[KILL] Stopping orphan PID $($v.ProcessId): $($v.CommandLine)"
        Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($victims) { Start-Sleep -Milliseconds 500 }
    $caddyVictims = Get-CaddyProcesses
    foreach ($v in $caddyVictims) {
        Write-Host "[KILL] Stopping orphan Caddy PID $($v.ProcessId)"
        Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($caddyVictims) { Start-Sleep -Milliseconds 500 }
    Remove-Pid "manager"; Remove-Pid "worker"; Remove-Pid "caddy"
    # Manager artifacts — source-mode state lives inside manager/.
    $dbFile = Join-Path $ManagerDir "db.sqlite3"
    foreach ($f in @(
        $ConfigFile,
        (Join-Path $ManagerDir "db.sqlite3-journal"),
        (Join-Path $ManagerDir "broadcaster_params.json"),
        (Join-Path $ManagerDir "topology.json"),
        (Join-Path $ManagerDir ".setup_complete")
    )) {
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
        (Join-Path $ManagerDir "tls"), (Join-Path $ManagerDir "caddy"),
        (Join-Path $FrontendDir "dist"), (Join-Path $FrontendDir ".angular"),
        (Join-Path $FrontendDir "node_modules"), (Join-Path $ManagerDir "media"),
        (Join-Path $ManagerDir "blender")
    )) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $ManagerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Manager artifacts removed"
    # Worker — wipe the in-repo dev data dir (logs, tools, assets,
    # sentinel, TLS, enrollment config) and any legacy/stale caches.
    if (Test-Path $WorkerDataDir) {
        Remove-Item -Recurse -Force $WorkerDataDir -ErrorAction SilentlyContinue
    }
    foreach ($d in @("managed_tools", "managed_assets", "worker_output", "temp", "logs")) {
        $p = Join-Path $AgentDir $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Path $WorkerDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Worker artifacts removed"
    # The per-user data folder holds the identity files (TLS keys, secret
    # key, worker enrollment) of any installed manager or worker on this
    # account, so it is removed only with -All.
    if ($RemoveUserDir) {
        $dir = $userDir.Path
        if (Test-Path -LiteralPath $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath $dir) {
                Write-Host "[ERROR] Could not remove the per-user data folder ($dir)"
                exit 1
            }
            Write-Host "[OK] Shared data dir removed ($dir)"
        }
    } elseif ($userDir.Path -and (Test-Path -LiteralPath $userDir.Path -PathType Container)) {
        Write-Host "[OK] Shared data dir kept ($($userDir.Path)); remove it with: .\tools\sethlans.ps1 clean -All"
    }
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
        Write-Host "[ERROR] sethlans.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    Invoke-StartServices
}

# -- caddy (internal, invoked by Invoke-Manager) ---------------------------
function Start-Caddy {
    $existing = Get-SavedPid "caddy"
    if ($existing) { Write-Host "[OK] Caddy already running (PID $existing)"; return }
    if (-not (Test-Path $CaddyBin)) {
        Write-Host "[ERROR] Caddy binary not found at $CaddyBin"
        Write-Host "        Run: .\tools\sethlans.ps1 dev (or python tools\fetch_caddy.py --target-dir $CaddyDir)"
        exit 1
    }
    if (-not (Test-Path $Caddyfile)) {
        Write-Host "[ERROR] Caddyfile not found at $Caddyfile"
        Write-Host "        Run: .\tools\sethlans.ps1 dev"
        exit 1
    }
    Ensure-Dirs
    $outLog = Join-Path $PidDir "caddy.out.log"
    $errLog = Join-Path $PidDir "caddy.err.log"
    $proc = Start-Process -FilePath $CaddyBin `
        -ArgumentList @("run", "--config", $Caddyfile, "--adapter", "caddyfile") `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { Write-Host "[ERROR] Caddy failed to start (see $errLog)"; exit 1 }
    Save-Pid "caddy" $proc.Id
    Write-Host "[OK] Caddy started in background (PID $($proc.Id))"
}

# -- manager ---------------------------------------------------------------
function Invoke-Manager {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] sethlans.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    $existing = Get-SavedPid "manager"
    if ($existing) {
        Write-Host "[OK] Manager already running (PID $existing)"
    } else {
        Ensure-Dirs
        # Logs land in .pids/ so they're next to the code and survive
        # %TEMP% auto-cleanup — easier debugging of early-startup
        # crashes before Django's log handlers bind.
        $outLog = Join-Path $PidDir "manager.out.log"
        $errLog = Join-Path $PidDir "manager.err.log"
        $stdinFile = Join-Path $PidDir "manager.stdin.txt"
        Set-Content -Path $stdinFile -Value ""
        $proc = Start-Process -FilePath "python" `
            -ArgumentList (Join-Path $ManagerDir "run_manager.py") `
            -PassThru -NoNewWindow `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -RedirectStandardInput $stdinFile
        Start-Sleep -Seconds 2
        if ($proc.HasExited) { Write-Host "[ERROR] Manager failed to start (see $errLog)"; exit 1 }
        Save-Pid "manager" $proc.Id
        Write-Host "[OK] Manager started in background (PID $($proc.Id))"
    }
    # Caddy comes up after Waitress is bound so the first proxy attempt
    # doesn't hit a closed socket and log spurious errors.
    Start-Caddy
    Write-Host "     Stop: .\tools\sethlans.ps1 stop"
}

# -- worker ----------------------------------------------------------------
function Invoke-Worker {
    if (-not (Test-Path $ConfigFile)) {
        Write-Host "[ERROR] sethlans.ini not found. Run: .\tools\sethlans.ps1 dev"; exit 1
    }
    $existing = Get-SavedPid "worker"
    if ($existing) { Write-Host "[OK] Worker already running (PID $existing)"; return }
    $enrollmentKey = Read-EnrollmentKey
    if (-not $enrollmentKey -or $enrollmentKey -eq "") {
        Write-Host "[ERROR] Could not read enrollment key from ManagerSettings DB."
        Write-Host "        Run '.\tools\sethlans.ps1 dev' first to migrate the DB."
        exit 1
    }
    Ensure-Dirs
    # Logs land in .pids/ next to the code (same treatment as manager)
    # so startup crashes are debuggable.
    $outLog = Join-Path $PidDir "worker.out.log"
    $errLog = Join-Path $PidDir "worker.err.log"
    # Set env vars for worker enrollment. SETHLANS_WORKER_DATA_DIR
    # pins all runtime state into the repo (worker/.dev-data/) so
    # `clean` is a trivial rm and logs are next to the code.
    $env:SETHLANS_WORKER_ENROLLMENT_KEY = $enrollmentKey
    $env:SETHLANS_MANAGER_HOST = "127.0.0.1"
    $env:SETHLANS_MANAGER_PORT = "$PublicTlsPort"
    $env:SETHLANS_IDLE_DETECTION_ENABLED = "false"
    $env:SETHLANS_WORKER_UI_ENABLED = "true"
    $env:SETHLANS_WORKER_DATA_DIR = $WorkerDataDir
    # Create an empty file for stdin so isatty() returns False (unattended wizard)
    $stdinFile = Join-Path $PidDir "worker.stdin.txt"
    Set-Content -Path $stdinFile -Value ""
    $proc = Start-Process -FilePath "python" `
        -ArgumentList (Join-Path $WorkerDir "run_worker.py") `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -RedirectStandardInput $stdinFile
    # Clean up env vars from current shell
    foreach ($v in @("SETHLANS_WORKER_ENROLLMENT_KEY", "SETHLANS_MANAGER_HOST",
                     "SETHLANS_MANAGER_PORT", "SETHLANS_IDLE_DETECTION_ENABLED",
                     "SETHLANS_WORKER_UI_ENABLED", "SETHLANS_WORKER_DATA_DIR")) {
        Remove-Item "Env:\$v" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { Write-Host "[ERROR] Worker failed to start (see $errLog)"; exit 1 }
    Save-Pid "worker" $proc.Id
    Write-Host "[OK] Worker started in background (PID $($proc.Id))"
    Write-Host "     Stop: .\tools\sethlans.ps1 stop"
}

# -- stop ------------------------------------------------------------------
# Stop the process in PID file $Name when it is this checkout's $Kind
# (Test-OurPid). A live PID that is not ours is a stale PID file: warn,
# drop the file, leave the process. Returns $true only when it stopped one.
function Stop-Tracked([string]$Name, [string]$Kind, [string]$Label) {
    $procId = Get-SavedPid $Name
    if (-not $procId) { return $false }
    if (-not (Test-OurPid $procId $Kind)) {
        Write-Host "[WARN] Stale PID file for $Name (PID $procId is not ours); not stopping it"
        Remove-Pid $Name
        return $false
    }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Remove-Pid $Name; Write-Host "[OK] $Label stopped (PID $procId)"
    return $true
}

function Invoke-Stop {
    $stopped = $false
    # Stop worker first so it doesn't log enrollment failures when
    # Caddy/Manager go away mid-heartbeat.
    if (Stop-Tracked "worker" "python" "Worker") { $stopped = $true }
    if (Stop-Tracked "caddy" "caddy" "Caddy") { $stopped = $true }
    if (Stop-Tracked "manager" "python" "Manager") { $stopped = $true }
    if (-not $stopped) { Write-Host "[OK] No running services found" }
}

# -- status ----------------------------------------------------------------
function Invoke-Status {
    $managerPid = Get-SavedPid "manager"
    if ($managerPid) { Write-Host "Manager:  running (PID $managerPid)" }
    else { Write-Host "Manager:  not running" }
    $caddyPid = Get-SavedPid "caddy"
    if ($caddyPid) { Write-Host "Caddy:    running (PID $caddyPid)" }
    else { Write-Host "Caddy:    not running" }
    $workerPid = Get-SavedPid "worker"
    if ($workerPid) { Write-Host "Worker:   running (PID $workerPid)" }
    else { Write-Host "Worker:   not running" }
}

# -- Main dispatch ---------------------------------------------------------
# No command (e.g. only -All): the normal usage error.
if (-not $Command) { Show-Usage; exit 1 }
# Only `clean` takes options: -All, or --all for parity with sethlans.sh.
$CleanAll = [bool]$All
foreach ($arg in @($ExtraArgs | Where-Object { $_ -ne $null })) {
    if ($Command -eq "clean" -and $arg -ceq "--all") { $CleanAll = $true; continue }
    if ($Command -eq "clean") { Write-Host "[ERROR] Unknown option for clean: $arg" }
    else { Write-Host "[ERROR] '$Command' takes no options" }
    Show-Usage; exit 1
}
if ($All -and $Command -ne "clean") {
    Write-Host "[ERROR] '$Command' takes no options"; Show-Usage; exit 1
}
switch ($Command) {
    "dev"     { Invoke-Dev }
    "clean"   { Invoke-Clean $CleanAll }
    "start"   { Invoke-Start }
    "manager" { Invoke-Manager }
    "worker"  { Invoke-Worker }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    default   { Show-Usage; exit 1 }
}
