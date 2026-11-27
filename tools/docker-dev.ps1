<#
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

.SYNOPSIS
    Docker development convenience script.

.DESCRIPTION
    Commands:
      up          Start the dev stack (docker compose up --build)
      down        Stop the dev stack
      down -v     Stop the dev stack and remove volumes
      exec        Run a command in a running container
      logs        Tail logs from all services

    This script wraps docker compose with the correct -f flags so it works
    from any CWD within the repo.

.EXAMPLE
    .\tools\docker-dev.ps1 up
    .\tools\docker-dev.ps1 down
    .\tools\docker-dev.ps1 down -v
    .\tools\docker-dev.ps1 exec manager python manage.py shell
    .\tools\docker-dev.ps1 logs
#>

param(
    [Parameter(Position = 0)]
    [string]$Command,
    [Parameter(Position = 1, ValueFromRemainingArguments)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path

$ComposeBase = Join-Path $ProjectRoot "deploy" "docker" "docker-compose.yml"
$ComposeDev = Join-Path $ProjectRoot "deploy" "docker" "docker-compose.dev.yml"

# The production compose file uses ${SETHLANS_SECURITY_SECRET_KEY:?...} which
# is evaluated at YAML parse time BEFORE the dev override's static value is
# merged. Export a dev-only value so compose parsing succeeds.
$env:SETHLANS_SECURITY_SECRET_KEY = "dev-insecure-key-for-local-development-only"

# The manager exits 12 without an admin account. The dev compose file
# takes the first admin's password from this file (a Compose secret);
# make it once with a random password. The folder is limited to the
# current user before the file is written, so the file inherits that.
# On Windows: inherited entries removed, full control for the current
# user and SYSTEM only (Docker Desktop reads bind-mount files as the
# signed-in user). Elsewhere: mode 700, as tools/docker-dev.sh does.
# The worker page password (the worker_ui_password secret) is made the
# same way, in the same folder.
$DevAdminDir = Join-Path $ProjectRoot ".tmp" "docker-dev"
$env:SETHLANS_DEV_ADMIN_PASSWORD_FILE = Join-Path $DevAdminDir "admin_password"
$env:SETHLANS_DEV_WORKER_UI_PASSWORD_FILE = Join-Path $DevAdminDir "worker_ui_password"
if (-not $env:SETHLANS_ADMIN_USERNAME) { $env:SETHLANS_ADMIN_USERNAME = "admin" }

function Protect-DevAdminDir {
    if ($IsWindows) {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        # No /T: Windows passes the folder's new entries on to files that
        # inherit; /T would strip an existing file to an empty ACL.
        icacls $DevAdminDir /inheritance:r /grant:r "*${sid}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" /Q | Out-Null
    } else {
        chmod 700 $DevAdminDir
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Could not limit $DevAdminDir to the current user (exit $LASTEXITCODE)"; exit 1
    }
}

# Write a random 24-character password to $file unless it already holds one.
function Ensure-DevPasswordFile([string]$file) {
    if ((Test-Path $file) -and (Get-Item $file).Length -gt 0) { return }
    $bytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(18)
    [IO.File]::WriteAllText($file, [Convert]::ToBase64String($bytes))
}

function Ensure-DevPasswords {
    New-Item -ItemType Directory -Force -Path $DevAdminDir | Out-Null
    Protect-DevAdminDir
    Ensure-DevPasswordFile $env:SETHLANS_DEV_ADMIN_PASSWORD_FILE
    Ensure-DevPasswordFile $env:SETHLANS_DEV_WORKER_UI_PASSWORD_FILE
}

# The manager and worker images COPY a pre-fetched Caddy binary from
# .tmp/caddy/linux-<arch>/ in the build context (same as CI).
$CaddyDir = Join-Path $ProjectRoot ".tmp" "caddy"

function Ensure-CaddyBinaries {
    $amd64 = Join-Path $CaddyDir "linux-amd64" "caddy"
    $arm64 = Join-Path $CaddyDir "linux-arm64" "caddy"
    if ((Test-Path $amd64) -and (Test-Path $arm64)) { return }
    Write-Host "--- Fetching Caddy binaries into $CaddyDir ---"
    python (Join-Path $ScriptDir "fetch_caddy.py") --target-dir $CaddyDir `
        --multi-arch linux-amd64,linux-arm64
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Caddy fetch failed (exit $LASTEXITCODE)"; exit 1
    }
}

function Show-Usage {
    Write-Host "Usage: .\tools\docker-dev.ps1 <command> [args...]"
    Write-Host ""
    Write-Host "  up          Start the dev stack (docker compose up --build)"
    Write-Host "  down        Stop the dev stack"
    Write-Host "  down -v     Stop the dev stack and remove volumes"
    Write-Host "  exec        Run a command in a running container"
    Write-Host "  logs        Tail logs from all services"
}

if (-not $Command) {
    Show-Usage
    exit 1
}

$composeFlags = @("-f", $ComposeBase, "-f", $ComposeDev, "--project-directory", $ProjectRoot)

switch ($Command) {
    "up" {
        Ensure-CaddyBinaries
        Ensure-DevPasswords
        Write-Host "--- Dev admin: $env:SETHLANS_ADMIN_USERNAME, password in $env:SETHLANS_DEV_ADMIN_PASSWORD_FILE ---"
        Write-Host "--- Dev worker page password in $env:SETHLANS_DEV_WORKER_UI_PASSWORD_FILE ---"
        docker compose @composeFlags up --build @ExtraArgs
    }
    "down" {
        docker compose @composeFlags down @ExtraArgs
    }
    "exec" {
        docker compose @composeFlags exec @ExtraArgs
    }
    "logs" {
        docker compose @composeFlags logs -f @ExtraArgs
    }
    default {
        Write-Host "Unknown command: $Command"
        Show-Usage
        exit 1
    }
}
