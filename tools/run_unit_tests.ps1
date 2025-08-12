<#
.SYNOPSIS
    Runs all unit tests for the Sethlans Reborn project.

.DESCRIPTION
    This script executes the full unit test suite, which includes:
    1. Django application tests located in the 'workers/' directory.
    2. Python worker agent tests located in 'tests/unit/'.

    It explicitly excludes the end-to-end tests found in 'tests/e2e/'.
    The test results, including verbose output, are saved to tools\results\unit_test_results.txt.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2025-07-25
#>

# Determine script and project roots
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path

# Prepare results directory under tools/results
$ResultsDir = Join-Path $ScriptDir "results"
if (-not (Test-Path $ResultsDir)) {
    New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
}
$ResultsFile = Join-Path $ResultsDir "unit_test_results.txt"

# Announce the start of the unit test run
Write-Host "Starting all unit tests (Django + Worker Agent)..."

# Construct test directory paths
$WorkerTests = Join-Path $ProjectRoot "workers"
$AgentTests = Join-Path $ProjectRoot "tests/unit"

# Build and run pytest command
$command = "pytest -s -v `"$AgentTests`" `"$WorkerTests`""

# Execute the command and redirect all output (both stdout and stderr) to the results file.
Invoke-Expression $command 2>&1 | Tee-Object -FilePath $ResultsFile

# Announce completion
Write-Host "Unit test run complete. Results saved to $ResultsFile"
