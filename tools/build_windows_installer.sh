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
#       2. PyInstaller bundles: manager, worker, tray_helper, launcher,
#          wizard.
#       3. NSIS compile into a single installer exe.
#
#     Reads the authoritative version from VERSION at the repo root.
#     The same file is read by build_macos_installer.sh and
#     build_linux_installer.sh so all three platforms produce matching
#     installer artifacts for a given commit. Bump the version via
#     tools/bump_version.sh (patch/minor/major), or pass an explicit
#     --version=X.Y.Z to override (e.g. for a release tag without a
#     pre-commit).
#
#     Generated files (PyInstaller dist/build trees) land in dist/ and
#     build/ at the repo root (gitignored). Both trees are removed
#     after the installer exe is produced. The final installer exe is
#     emitted at packaging/windows/ so downstream tooling
#     (tests/unit/test_nsi_installer.py, CI artifact uploads) keeps
#     working unchanged.
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
#         * All of wizard/requirements.txt
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
#       -r worker/requirements.txt -r wizard/requirements.txt \
#       -r requirements-build.txt
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
    -h|--help) sed -n '2,45p' "${BASH_SOURCE[0]}"; exit 0 ;;
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

# Build version = semver + 5-char git commit hash. Identifies the
# exact commit the artifact was built from. All three platform build
# scripts compute this identically, so a given commit produces matching
# 0.X.Y.HHHHH artifacts across Windows / macOS / Linux.
GIT_HASH=$(git rev-parse --short=5 HEAD 2>/dev/null || true)
if [ -z "$GIT_HASH" ]; then
  echo "ERROR: not a git checkout (cannot resolve HEAD short hash for build version)."
  exit 1
fi
BUILD_VERSION="${VERSION}.${GIT_HASH}"
echo "=== Building Sethlans v${BUILD_VERSION} ==="

# --- Environment checks ---
VENV_PYI=".venv-build/Scripts/pyinstaller.exe"
NSIS="C:/Program Files (x86)/NSIS/Bin/makensis.exe"

# PyInstaller and NSIS both default to dist/ (gitignored); we let them
# use the default rather than override via --distpath / -DDIST_ROOT.
# Both trees are wiped after the installer exe is produced (see
# cleanup section below).
DIST_ROOT="dist"
BUILD_ROOT="build"
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
echo "=== 1/7 Angular build ==="
( cd manager/frontend && npm run build 2>&1 | tail -3 )

echo "=== 2/7 PyInstaller: manager ==="
"$VENV_PYI" packaging/pyinstaller/manager.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 3/7 PyInstaller: worker ==="
"$VENV_PYI" packaging/pyinstaller/worker.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 4/7 PyInstaller: tray_helper ==="
"$VENV_PYI" packaging/pyinstaller/tray_helper.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 5/7 PyInstaller: launcher ==="
"$VENV_PYI" packaging/pyinstaller/launcher.spec --noconfirm --clean 2>&1 | tail -3

echo "=== 6/7 PyInstaller: wizard ==="
"$VENV_PYI" packaging/pyinstaller/wizard.spec --noconfirm --clean 2>&1 | tail -3

# NF-4: wizard one-dir bundle MUST stay at or under 30 MB. Fail loudly
# on exceedance with the top 10 largest files in the bundle, so a
# regression is diagnosable from CI logs without re-running the build.
# C2 (sethlans.nsi) reads the wizard bundle directly from
# ${DIST_ROOT}/wizard, so no separate staging copy is needed here.
WIZARD_BUNDLE_DIR="${DIST_ROOT}/wizard"
WIZARD_SIZE_LIMIT=$((30 * 1024 * 1024))
if [ ! -d "$WIZARD_BUNDLE_DIR" ]; then
  echo "ERROR: wizard PyInstaller bundle not produced at $WIZARD_BUNDLE_DIR"
  exit 1
fi
WIZARD_SIZE=$(du -sb "$WIZARD_BUNDLE_DIR" | cut -f1)
echo "Wizard bundle size: ${WIZARD_SIZE} bytes (limit ${WIZARD_SIZE_LIMIT})"
if [ "$WIZARD_SIZE" -gt "$WIZARD_SIZE_LIMIT" ]; then
  echo "ERROR: wizard bundle exceeds NF-4 30 MB ceiling."
  echo "Top 10 largest files in $WIZARD_BUNDLE_DIR:"
  find "$WIZARD_BUNDLE_DIR" -type f -exec du -sh {} + | sort -rh | head -10
  exit 1
fi

echo "=== 7/7 NSIS (v$BUILD_VERSION) ==="
"$NSIS" -DPRODUCT_VERSION="$BUILD_VERSION" packaging/windows/sethlans.nsi 2>&1 | tail -3

OUTPUT="packaging/windows/sethlans-$BUILD_VERSION-windows-x64.exe"
if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: expected installer not produced at $OUTPUT"
  exit 1
fi
ls -lh "$OUTPUT"

# --- Cleanup ---
# Installer is produced; the PyInstaller intermediate trees are no
# longer needed. Selective removal (matches tools/build_macos_installer.sh):
# wipe build/ and only the five PyInstaller bundle dirs under dist/.
# Do NOT `rm -rf dist/` wholesale — the macOS builder emits its final
# dist/sethlans-<V>-macos-arm64.dmg into the same dist/ dir, and a
# heavy-handed cleanup here would nuke a sibling platform's deliverable
# in a cross-platform workspace (sync tool / CI matrix reuse). The
# Windows installer exe at $OUTPUT lives under packaging/windows/ so
# nothing in this block touches it. .tmp/build_version also stays so
# the auto-bump keeps working across runs.
echo "=== Cleanup ==="
rm -rf "${BUILD_ROOT:?}"
for component in launcher manager worker tray_helper wizard; do
  rm -rf "${DIST_ROOT:?}/$component"
done
echo "Removed $BUILD_ROOT and $DIST_ROOT/{launcher,manager,worker,tray_helper,wizard}"

echo ""
echo "Installer ready: $OUTPUT"
