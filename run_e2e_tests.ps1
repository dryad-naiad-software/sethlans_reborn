<#
.SYNOPSIS
    Runs the end-to-end (E2E) test suite for the Sethlans Reborn project.

.DESCRIPTION
    This script is dedicated to running the full E2E workflow tests, which are
    resource-intensive and test the live interaction between the Django manager
    and the Python worker agent.

    It specifically targets the tests located in the 'tests/e2e/' directory.
    The test results, including verbose output and setup/teardown logs, are
    saved to 'e2e_test_results.txt'.

.NOTES
    Author: Sethlans Reborn Development
    Last Modified: 2025-07-25
#>

# Announce the start of the E2E test run
Write-Host "Starting the end-to-end (E2E) test suite..."

# Define the command to run pytest specifically on the E2E test directory.
# -s: Shows print statements from tests.
# -v: Provides verbose output, listing each test function.
# tests/e2e/: The path to the end-to-end tests.
$command = "pytest -s -v tests/e2e/"

# Execute the command and redirect all output (both stdout and stderr) to the results file.
# The Tee-Object cmdlet is used to simultaneously display the output in the console.
Invoke-Expression $command 2>&1 | Tee-Object -FilePath "e2e_test_results.txt"

# Announce completion
Write-Host "E2E test run complete. Results saved to e2e_test_results.txt"
