<#
.SYNOPSIS
    Starts the Sethlans Manager server.

.DESCRIPTION
    Runs database migrations and starts the Django development server
    on the configured port (default: 7075).

    Requires manager.ini to exist. Run setup-manager.ps1 first if this
    is a fresh installation.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2026-04-01
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$ManagePy = Join-Path $ProjectRoot "manager" "manage.py"
$ConfigFile = Join-Path $ProjectRoot "manager" "manager.ini"

if (-not (Test-Path $ConfigFile)) {
    Write-Host "[ERROR] manager.ini not found. Run setup-manager.ps1 first."
    exit 1
}

Write-Host "--- Applying migrations ---"
python $ManagePy migrate

Write-Host ""
Write-Host "--- Starting Sethlans Manager ---"
python $ManagePy runserver 7075
