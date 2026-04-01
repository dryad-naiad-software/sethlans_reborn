<#
.SYNOPSIS
    Builds the Angular frontend and collects static files.

.DESCRIPTION
    Installs npm dependencies (if needed), runs the Angular production
    build, and collects static files for WhiteNoise serving.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2026-04-01
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$FrontendDir = Join-Path $ProjectRoot "manager" "frontend"
$ManagePy = Join-Path $ProjectRoot "manager" "manage.py"

if (-not (Test-Path $FrontendDir)) {
    Write-Host "[ERROR] Frontend directory not found at $FrontendDir"
    exit 1
}

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "--- Installing frontend dependencies ---"
    npm install --prefix $FrontendDir
}

Write-Host "--- Building Angular frontend ---"
npm run build --prefix $FrontendDir
Write-Host "[OK] Frontend built"

Write-Host ""
Write-Host "--- Collecting static files ---"
python $ManagePy collectstatic --noinput
Write-Host "[OK] Static files collected"
