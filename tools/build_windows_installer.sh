#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# SYNOPSIS
#     Builds the Sethlans Windows installer (NSIS) from source.
#
# DESCRIPTION
#     Dev-focused script. Orchestrates the full Windows build pipeline:
#       1. Angular frontend build (npm run build).
#       2. PyInstaller bundles: manager, worker, tray_helper, launcher.
#       3. NSIS compile into a single installer exe.
#
#     Auto-increments a patch version (0.1.1..0.1.199) tracked in
#     .tmp/build_version (gitignored). Pass an explicit version via
#     --version=X.Y.Z to override the auto-bump (e.g. for release tags).
#
#     Generated files (PyInstaller dist/build trees) land in .tmp/dist/
#     and .tmp/build/ so they stay out of the repo-root tree. The final
#     installer exe is produced at packaging/windows/ so downstream
#     tooling (tests/unit/test_nsi_installer.py, CI artifact uploads)
#     keeps working unchanged.
#
#     Output: packaging/windows/sethlans-<VERSION>-windows-x64.exe
#
# PLATFORM
#     Windows only (uses Windows-specific NSIS path + .venv-build layout).
#     Run under Git Bash, MSYS2, or WSL with Windows tool interop enabled.
#
# MINIMUM REQUIREMENTS
#     - Windows 10/11 x64
#     - Python 3.14 virtualenv at .venv-build/ with these packages installed:
#         * All of manager/requirements.txt
#         * All of worker/requirements.txt
#         * requirements-build.txt  (pyinstaller, pyinstaller-hooks-contrib)
#     - Node.js 20+ with npm on PATH (for Angular build)
#     - NSIS 3.11+ at "C:/Program Files (x86)/NSIS/Bin/makensis.exe"
#     - Bash (Git Bash 2.x+ or MSYS2)
#     - ~1 GB free disk for PyInstaller work dir + dist/ output
#     - Repo checked out at absolute path (no spaces, Git Bash path)
#
# BOOTSTRAP (one-time)
#     py -3.12 -m venv .venv-build
#     .venv-build/Scripts/pip install -r manager/requirements.txt \
#       -r worker/requirements.txt -r requirements-build.txt
#     choco install nsis -y   # or install NSIS manually
#
# USAGE
#     bash tools/build_windows_installer.sh
#     bash tools/build_windows_installer.sh --version=0.2.0
#
# NOTES
#     Last Modified: 2026-04-18

set -euo pipefail

# Resolve repo root (script lives in tools/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# --- Args ---
EXPLICIT_VERSION=""
for arg in "$@"; do
  case "$arg" in
    --version=*) EXPLICIT_VERSION="${arg#--version=}" ;;
    -h|--help) sed -n '2,42p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

# --- Version selection ---
mkdir -p .tmp
VERSION_FILE=".tmp/build_version"
if [ -n "$EXPLICIT_VERSION" ]; then
  VERSION="$EXPLICIT_VERSION"
elif [ -f "$VERSION_FILE" ]; then
  CURRENT=$(cat "$VERSION_FILE")
  PATCH=$(echo "$CURRENT" | awk -F. '{print $3}')
  NEXT_PATCH=$((PATCH + 1))
  if [ "$NEXT_PATCH" -gt 199 ]; then
    echo "ERROR: patch version 0.1.$NEXT_PATCH exceeds 199 cap. Bump minor manually with --version=0.2.0"
    exit 1
  fi
  VERSION="0.1.$NEXT_PATCH"
else
  VERSION="0.1.1"
fi
echo "$VERSION" > "$VERSION_FILE"
echo "=== Building Sethlans v$VERSION ==="

# --- Environment checks ---
VENV_PY=".venv-build/Scripts/python.exe"
VENV_PYI=".venv-build/Scripts/pyinstaller.exe"
NSIS="C:/Program Files (x86)/NSIS/Bin/makensis.exe"

# Generated files go under .tmp/ (gitignored). The NSIS script reads
# DIST_ROOT via /DDIST_ROOT=... so the four File /r lines can find
# the bundles at their new location.
DIST_ROOT=".tmp/dist"
BUILD_ROOT=".tmp/build"
NSIS_DIST_ROOT="..\\..\\.tmp\\dist"  # relative to packaging/windows/sethlans.nsi
if [ ! -x "$VENV_PYI" ]; then
  echo "ERROR: PyInstaller not found at $VENV_PYI"
  echo "See MINIMUM REQUIREMENTS at the top of this script."
  exit 1
fi
if [ ! -x "$NSIS" ]; then
  echo "ERROR: NSIS makensis.exe not found at $NSIS"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not on PATH (Node.js 20+ required)"
  exit 1
fi

# --- Build steps ---
echo "=== 1/6 Angular build ==="
( cd manager/frontend && npm run build 2>&1 | tail -3 )

PYI_PATHS=(--distpath "$DIST_ROOT" --workpath "$BUILD_ROOT")

echo "=== 2/6 PyInstaller: manager ==="
"$VENV_PYI" packaging/pyinstaller/manager.spec --noconfirm --clean "${PYI_PATHS[@]}" 2>&1 | tail -3

echo "=== 3/6 PyInstaller: worker ==="
"$VENV_PYI" packaging/pyinstaller/worker.spec --noconfirm --clean "${PYI_PATHS[@]}" 2>&1 | tail -3

echo "=== 4/6 PyInstaller: tray_helper ==="
"$VENV_PYI" packaging/pyinstaller/tray_helper.spec --noconfirm --clean "${PYI_PATHS[@]}" 2>&1 | tail -3

echo "=== 5/6 PyInstaller: launcher ==="
"$VENV_PYI" packaging/pyinstaller/launcher.spec --noconfirm --clean "${PYI_PATHS[@]}" 2>&1 | tail -3

echo "=== 6/6 NSIS (v$VERSION) ==="
"$NSIS" -DPRODUCT_VERSION="$VERSION" -DDIST_ROOT="$NSIS_DIST_ROOT" packaging/windows/sethlans.nsi 2>&1 | tail -3

OUTPUT="packaging/windows/sethlans-$VERSION-windows-x64.exe"
if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: expected installer not produced at $OUTPUT"
  exit 1
fi
ls -lh "$OUTPUT"

# --- Cleanup ---
# Installer is produced; the PyInstaller dist/ and build/ trees under
# .tmp/ are no longer needed. Removing them saves ~1 GB between runs
# and keeps .tmp/ from growing unbounded across repeated builds. The
# installer exe at $OUTPUT is preserved; .tmp/build_version stays so
# the auto-bump keeps working across runs.
echo "=== Cleanup ==="
rm -rf "$DIST_ROOT" "$BUILD_ROOT"
echo "Removed $DIST_ROOT and $BUILD_ROOT"

echo ""
echo "Installer ready: $OUTPUT"
