<#
.SYNOPSIS
    Starts the Sethlans Worker Agent.

.DESCRIPTION
    Starts the worker agent which connects to the manager, enrolls
    if needed, downloads required Blender versions, and polls for jobs.

    Requires worker\config.ini with the manager host/port and either
    an API token or enrollment key.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2026-04-01
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$WorkerDir = Join-Path $ProjectRoot "worker"
$ConfigFile = Join-Path $WorkerDir "config.ini"

if (-not (Test-Path $ConfigFile)) {
    Write-Host "[ERROR] worker\config.ini not found."
    Write-Host ""
    Write-Host "Create it from the example:"
    Write-Host "  Copy-Item $WorkerDir\config.ini.example $ConfigFile"
    Write-Host ""
    Write-Host "Then set the enrollment_key from the manager setup output."
    exit 1
}

Write-Host "--- Starting Sethlans Worker Agent ---"
python (Join-Path $WorkerDir "run_worker.py")
