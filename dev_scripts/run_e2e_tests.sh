#!/usr/bin/env bash
#
# SYNOPSIS
#     Runs the end-to-end (E2E) test suite for the Sethlans Reborn project.
#
# DESCRIPTION
#     This script runs the full E2E workflow tests, which are
#     resource-intensive and test the live interaction between the
#     Django manager and the Python worker agent.
#
#     It specifically targets the tests located in the 'tests/e2e/' directory.
#     The test results, including verbose output and setup/teardown logs, are
#     saved to dev_scripts/results/e2e_test_results.txt.
#
# NOTES
#     Author: Sethlans Reborn Development
#     Last Modified: 2025-08-01
#

set -euo pipefail

# Resolve script and project roots
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"

# Prepare results directory under dev_scripts/results
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
RESULTS_FILE="$RESULTS_DIR/e2e_test_results.txt"

echo "Starting the end-to-end (E2E) test suite..."

# Construct path to E2E tests
E2E_TESTS="$PROJECT_ROOT/tests/e2e"

# Define and run the pytest command
command="pytest -s -v \"$E2E_TESTS\""

# Execute, teeing output to file
eval "$command" 2>&1 | tee "$RESULTS_FILE"

echo "E2E test run complete. Results saved to $RESULTS_FILE"
