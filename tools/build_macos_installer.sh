#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# SYNOPSIS
#     Builds the Sethlans macOS DMG installer from source.
#
# DESCRIPTION
#     Dev-focused script. Orchestrates the full macOS build pipeline:
#       1. Angular frontend build (npm run build).
#       2. PyInstaller bundles: manager, worker, tray_helper, launcher.
#       3. DMG assembly via packaging/macos/build_dmg.sh (hdiutil UDZO).
#
#     Reads the authoritative version from VERSION at the repo root.
#     The same file is read by build_windows_installer.sh and
#     build_linux_installer.sh so all three platforms produce matching
#     installer artifacts for a given commit. Bump the version via
#     tools/bump_version.sh (patch/minor/major), or pass an explicit
#     --version=X.Y.Z to override (e.g. for a release tag without a
#     pre-commit).
#
#     Output: dist/sethlans-<VERSION>-macos-arm64.dmg
#
# PLATFORM
#     macOS only (uses hdiutil + PyInstaller .app BUNDLE output).
#     Apple Silicon (arm64); the DMG is named -macos-arm64 accordingly.
#
# MINIMUM REQUIREMENTS
#     - macOS 13+ (Ventura or newer)
#     - Python 3.14 virtualenv at .venv-build/ with these packages installed:
#         * All of manager/requirements.txt
#         * All of worker/requirements.txt
#         * requirements-build.txt  (pyinstaller, pyinstaller-hooks-contrib,
#                                    PySide6-Essentials)
#     - Node.js 20+ with npm on PATH (for Angular build)
#     - Xcode Command Line Tools (provides hdiutil, codesign, sed, cp)
#     - ~1 GB free disk for PyInstaller work dir + dist/ output
#
# BOOTSTRAP (one-time)
#     /opt/homebrew/bin/python3.14 -m venv .venv-build
#     .venv-build/bin/pip install -r manager/requirements.txt \
#       -r worker/requirements.txt -r requirements-build.txt
#     xcode-select --install   # if CLT not already present
#
# USAGE
#     bash tools/build_macos_installer.sh
#     bash tools/build_macos_installer.sh --version=0.2.0
#
# NOTES
#     Not (yet) codesigned or notarized. The DMG installs but macOS
#     Gatekeeper will quarantine-block first launch until the user
#     right-click → Open or removes the quarantine xattr. Signing/
#     notarization is a separate workstream.

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
    -h|--help) sed -n '2,48p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

# --- Version selection ---
# Authoritative version lives in the repo-root VERSION file, which all
# three platform build scripts read. Bump it via tools/bump_version.sh
# (not auto-bumped here, so cross-platform builds of the same commit
# produce matching 0.1.X installers). An explicit --version=X.Y.Z
# override is still honored for release tags.
VERSION_FILE="VERSION"
if [ -n "$EXPLICIT_VERSION" ]; then
  VERSION="$EXPLICIT_VERSION"
elif [ -f "$VERSION_FILE" ]; then
  VERSION=$(tr -d '[:space:]' < "$VERSION_FILE")
else
  echo "ERROR: $VERSION_FILE not found at repo root. Create it (e.g. 'echo 0.1.0 > VERSION') or pass --version=X.Y.Z."
  exit 1
fi
if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: version '$VERSION' is not X.Y.Z"
  exit 1
fi
echo "=== Building Sethlans v$VERSION (macOS) ==="

# --- Environment checks ---
VENV_PY=".venv-build/bin/python"
VENV_PYI=".venv-build/bin/pyinstaller"
DMG_SCRIPT="packaging/macos/build_dmg.sh"
if [ ! -x "$VENV_PYI" ]; then
  echo "ERROR: PyInstaller not found at $VENV_PYI"
  echo "See MINIMUM REQUIREMENTS at the top of this script."
  exit 1
fi
if [ ! -f "$DMG_SCRIPT" ]; then
  echo "ERROR: DMG build script not found at $DMG_SCRIPT"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not on PATH (Node.js 20+ required)"
  exit 1
fi
if ! command -v hdiutil >/dev/null 2>&1; then
  echo "ERROR: hdiutil not on PATH (install Xcode Command Line Tools)"
  exit 1
fi

# --- Build steps ---
echo "=== 1/6 Angular build ==="
( cd manager/frontend && npm run build 2>&1 | tail -3 )

echo "=== 2/6 PyInstaller: manager ==="
"$VENV_PYI" packaging/pyinstaller/manager.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 3/6 PyInstaller: worker ==="
"$VENV_PYI" packaging/pyinstaller/worker.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 4/6 PyInstaller: tray_helper ==="
"$VENV_PYI" packaging/pyinstaller/tray_helper.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 5/6 PyInstaller: launcher ==="
"$VENV_PYI" packaging/pyinstaller/launcher.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 6/6 DMG (v$VERSION) ==="
bash "$DMG_SCRIPT" "$VERSION"

OUTPUT="dist/sethlans-$VERSION-macos-arm64.dmg"
if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: expected DMG not produced at $OUTPUT"
  exit 1
fi

echo "=== cleanup ==="
# DMG is self-contained; intermediate PyInstaller artifacts are no longer
# needed. Leave only the deliverable .dmg files in dist/. Only runs on
# success so failed builds retain artifacts for debugging.
rm -rf build
for component in launcher manager worker tray_helper; do
  rm -rf "dist/$component"
done
rm -rf dist/dmg-staging

ls -lh "$OUTPUT"
echo ""
echo "Installer ready: $OUTPUT"
