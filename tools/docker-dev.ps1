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
