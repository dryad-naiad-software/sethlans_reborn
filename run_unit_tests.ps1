<#
.SYNOPSIS
    Runs all unit tests for the Sethlans Reborn project.

.DESCRIPTION
    This script executes the full unit test suite, which includes:
    1. Django application tests located in the 'workers/' directory.
    2. Python worker agent tests located in the 'tests/unit/' directory.

    It explicitly excludes the end-to-end tests found in 'tests/e2e/'.
    The test results, including verbose output, are saved to 'unit_test_results.txt'.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2025-07-25
#>

# Announce the start of the unit test run
Write-Host "Starting all unit tests (Django + Worker Agent)..."

# Define the command to run pytest on the specified directories.
# -s: Shows print statements from tests.
# -v: Provides verbose output, listing each test function.
# tests/unit/: The path to the worker agent's unit tests.
# workers/: The path to the Django app, where pytest will find tests.py.
$command = "pytest -s -v tests/unit/ workers/"

# Execute the command and redirect all output (both stdout and stderr) to the results file.
# The Tee-Object cmdlet is used to simultaneously display the output in the console.
Invoke-Expression $command 2>&1 | Tee-Object -FilePath "unit_test_results.txt"

# Announce completion
Write-Host "Unit test run complete. Results saved to unit_test_results.txt"