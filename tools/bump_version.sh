#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# SYNOPSIS
#     Bump the repo's VERSION file (patch, minor, or major) and commit.
#
# DESCRIPTION
#     The VERSION file at the repo root is the single source of truth for
#     build numbers across all three platform installer scripts (Windows,
#     macOS, Linux). All three read VERSION; none of them auto-bump
#     anymore. This helper performs an explicit version bump so the new
#     number lands in git history.
#
#     Default bump level is `patch` (0.1.10 -> 0.1.11). Pass --minor
#     (0.1.10 -> 0.2.0) or --major (0.1.10 -> 1.0.0) or --set=X.Y.Z to
#     pin an exact value (e.g. for release tags).
#
#     The script writes VERSION, stages it, and creates a commit on the
#     current branch. Nothing is pushed.
#
# USAGE
#     bash tools/bump_version.sh                # patch (default)
#     bash tools/bump_version.sh --minor
#     bash tools/bump_version.sh --major
#     bash tools/bump_version.sh --set=0.2.0

set -euo pipefail

# Resolve repo root (script lives in tools/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

LEVEL="patch"
EXPLICIT=""
for arg in "$@"; do
  case "$arg" in
    --patch) LEVEL="patch" ;;
    --minor) LEVEL="minor" ;;
    --major) LEVEL="major" ;;
    --set=*) EXPLICIT="${arg#--set=}" ;;
    -h|--help) sed -n '2,27p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

VERSION_FILE="VERSION"
if [ ! -f "$VERSION_FILE" ]; then
  echo "ERROR: $VERSION_FILE not found at repo root"
  exit 1
fi
CURRENT=$(tr -d '[:space:]' < "$VERSION_FILE")
if ! echo "$CURRENT" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: VERSION file contents '$CURRENT' are not X.Y.Z"
  exit 1
fi

if [ -n "$EXPLICIT" ]; then
  NEW="$EXPLICIT"
else
  MAJOR=$(echo "$CURRENT" | awk -F. '{print $1}')
  MINOR=$(echo "$CURRENT" | awk -F. '{print $2}')
  PATCH=$(echo "$CURRENT" | awk -F. '{print $3}')
  case "$LEVEL" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  esac
  NEW="${MAJOR}.${MINOR}.${PATCH}"
fi

if ! echo "$NEW" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: computed new version '$NEW' is not X.Y.Z"
  exit 1
fi

if [ "$CURRENT" = "$NEW" ]; then
  echo "No change: VERSION is already $CURRENT"
  exit 0
fi

echo "$NEW" > "$VERSION_FILE"
echo "Bumped VERSION: $CURRENT -> $NEW"

git add "$VERSION_FILE"
git commit -m "Bump VERSION to $NEW"
echo "Committed. Run 'git push' when ready to share."
