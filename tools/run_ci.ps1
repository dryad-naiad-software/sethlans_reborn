# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
.SYNOPSIS
    Trigger the self-hosted CI workflow on demand.

.DESCRIPTION
    Self-hosted CI is trigger-only by design (#134). The self-hosted
    Linux GPU box and self-hosted Apple Silicon MacBook are the
    maintainer's personal hardware - they do NOT auto-fire on push
    or PR. This wrapper invokes
    .github/workflows/python-ci-self-hosted.yml via the GitHub CLI.

    By default the wrapper targets the current branch (resolved with
    `git symbolic-ref --short HEAD`); pass -Ref to override (handy
    for triggering against a tag or someone else's branch). The
    workflow's `ref` input is also wired into actions/checkout so the
    job tests the requested ref, not just the workflow file ref.

    The cloud-runner CI (GitHub-hosted ubuntu/windows/macos matrix in
    python-ci.yml, plus docker-test.yml / docker-publish.yml) still
    auto-fires on push and PR - this script does NOT need to be run
    for those.

.PARAMETER Ref
    Git ref (branch, tag, or SHA) to test. Defaults to the current
    branch, falling back to "master" if the working tree is in a
    detached-HEAD state.

.PARAMETER Help
    Show the help block and exit.

.EXAMPLE
    pwsh tools/run_ci.ps1
    pwsh tools/run_ci.ps1 -Ref master
    pwsh tools/run_ci.ps1 -Ref my-feature-branch
    pwsh tools/run_ci.ps1 -Help

.NOTES
    PLATFORM
        Windows, Linux, or macOS (PowerShell 7+).

    MINIMUM REQUIREMENTS
        - gh (GitHub CLI) on PATH, authenticated against the
          dryad-naiad-software/sethlans_reborn repo
          (`gh auth status` to verify).
        - git on PATH (only used to resolve the current branch when
          -Ref is omitted).

    Tip: tail the run with `gh run watch` after dispatch.

    Last Modified: 2026-04-25
#>

[CmdletBinding()]
param(
    [string]$Ref = "",
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Full
    exit 0
}

$Workflow = "python-ci-self-hosted.yml"

# --- gh availability ---
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "gh (GitHub CLI) not found on PATH. Install from https://cli.github.com/ and run 'gh auth login'."
    exit 1
}

# --- Resolve ref ---
if ([string]::IsNullOrEmpty($Ref)) {
    try {
        $Ref = (& git symbolic-ref --short HEAD 2>$null).Trim()
        if ([string]::IsNullOrEmpty($Ref)) { throw "empty" }
    } catch {
        $Ref = "master"
        Write-Warning "Not on a branch (detached HEAD?) - falling back to 'master'."
    }
}

Write-Host "Dispatching $Workflow on ref '$Ref'..."
& gh workflow run $Workflow --ref $Ref -f "ref=$Ref"
if ($LASTEXITCODE -ne 0) {
    Write-Error "gh workflow run failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Dispatched. Tip:"
Write-Host "  gh run list --workflow=$Workflow --limit 1"
Write-Host "  gh run watch     # interactively follow the most recent run"
