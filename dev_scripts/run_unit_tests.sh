#!/usr/bin/env bash
#
# SYNOPSIS
#     Runs all unit tests for the Sethlans Reborn project.
#
# DESCRIPTION
#     This script executes the full unit test suite, which includes:
#       1. Django application tests located in the 'workers/' directory.
#       2. Python worker agent tests located in the 'tests/unit/' directory.
#
#     It explicitly excludes the end-to-end tests found in 'tests/e2e/'.
#     The test results, including verbose output, are saved to dev_scripts/results/unit_test_results.txt.
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
RESULTS_FILE="$RESULTS_DIR/unit_test_results.txt"

echo "Starting all unit tests (Django + Worker Agent)..."

# Construct paths to test targets
WORKER_TESTS="$PROJECT_ROOT/workers"
AGENT_TESTS="$PROJECT_ROOT/tests/unit"

# Define and run the pytest command
command="pytest -s -v \"$AGENT_TESTS\" \"$WORKER_TESTS\""

# Execute, teeing output to file
eval "$command" 2>&1 | tee "$RESULTS_FILE"

echo "Unit test run complete. Results saved to $RESULTS_FILE"
