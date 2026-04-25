#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# SYNOPSIS
#     Trigger the self-hosted CI workflow on demand.
#
# DESCRIPTION
#     Self-hosted CI is trigger-only by design (#134). The self-hosted
#     Linux GPU box and self-hosted Apple Silicon MacBook are the
#     maintainer's personal hardware — they do NOT auto-fire on push
#     or PR. This wrapper invokes
#     `.github/workflows/python-ci-self-hosted.yml` via the GitHub CLI.
#
#     By default the wrapper targets the current branch (resolved with
#     `git symbolic-ref --short HEAD`); pass --ref to override (handy
#     for triggering against a tag or someone else's branch). The
#     workflow's `ref` input is also wired into actions/checkout so the
#     job tests the requested ref, not just the workflow file ref.
#
#     The cloud-runner CI (GitHub-hosted ubuntu/windows/macos matrix in
#     python-ci.yml, plus docker-test.yml / docker-publish.yml) still
#     auto-fires on push and PR — this script does NOT need to be run
#     for those.
#
# PLATFORM
#     Linux, macOS, or Windows (Git Bash / WSL). Pure shell + gh CLI.
#
# MINIMUM REQUIREMENTS
#     - gh (GitHub CLI) on PATH, authenticated against the
#       dryad-naiad-software/sethlans_reborn repo
#       (`gh auth status` to verify).
#     - git on PATH (only used to resolve the current branch when
#       --ref is omitted).
#
# USAGE
#     bash tools/run_ci.sh
#     bash tools/run_ci.sh --ref master
#     bash tools/run_ci.sh --ref my-feature-branch
#     bash tools/run_ci.sh --help
#
# NOTES
#     Last Modified: 2026-04-25
#     Tip: tail the run with `gh run watch` after dispatch.

set -euo pipefail

WORKFLOW="python-ci-self-hosted.yml"
REF=""

print_help() {
  sed -n '2,46p' "${BASH_SOURCE[0]}"
}

# --- Args ---
while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      if [ $# -lt 2 ]; then
        echo "ERROR: --ref requires a value (e.g. --ref master)" >&2
        exit 1
      fi
      REF="$2"
      shift 2
      ;;
    --ref=*)
      REF="${1#--ref=}"
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "ERROR: unknown arg: $1" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

# --- gh availability ---
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh (GitHub CLI) not found on PATH." >&2
  echo "Install from https://cli.github.com/ and run 'gh auth login'." >&2
  exit 1
fi

# --- Resolve ref ---
if [ -z "$REF" ]; then
  if REF=$(git symbolic-ref --short HEAD 2>/dev/null); then
    :
  else
    REF="master"
    echo "WARN: not on a branch (detached HEAD?) — falling back to 'master'." >&2
  fi
fi

echo "Dispatching $WORKFLOW on ref '$REF'..."
gh workflow run "$WORKFLOW" --ref "$REF" -f "ref=$REF"
echo ""
echo "Dispatched. Tip:"
echo "  gh run list --workflow=$WORKFLOW --limit 1"
echo "  gh run watch     # interactively follow the most recent run"
