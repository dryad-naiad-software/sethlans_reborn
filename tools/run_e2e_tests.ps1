<#
.SYNOPSIS
    Runs the end-to-end (E2E) test suite for the Sethlans Reborn project.

.DESCRIPTION
    This script is dedicated to running the full E2E workflow tests, which are
    resource-intensive and test the live interaction between the Django manager
    and the Python worker agent.

    It specifically targets the tests located in the 'tests/e2e/' directory.
    The test results, including verbose output and setup/teardown logs, are
    saved to tools\results\e2e_test_results.txt.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2025-08-01
#>

# Determine script and project roots
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path

# Prepare results directory under tools/results
$ResultsDir = Join-Path $ScriptDir "results"
if (-not (Test-Path $ResultsDir)) {
    New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
}
$ResultsFile = Join-Path $ResultsDir "e2e_test_results.txt"

# Announce the start of the E2E test run
Write-Host "Starting the end-to-end (E2E) test suite..."

# Construct the path to the E2E tests
$E2ETests = Join-Path $ProjectRoot "tests/e2e"

# Build and run pytest command
$command = "pytest -s -v `"$E2ETests`""

# Execute the command and redirect all output (both stdout and stderr) to the results file.
Invoke-Expression $command 2>&1 | Tee-Object -FilePath $ResultsFile

# Announce completion
Write-Host "E2E test run complete. Results saved to $ResultsFile"
