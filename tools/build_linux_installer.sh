#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# SYNOPSIS
#     Builds the Sethlans Linux installer from source.
#
# DESCRIPTION
#     Dev-focused script. Orchestrates the full Linux build pipeline:
#       1. Angular frontend build (npm run build).
#       2. PyInstaller bundles: manager, worker, tray_helper, launcher.
#       3. Installer assembly (see PACKAGING below).
#
#     Reads the authoritative version from VERSION at the repo root.
#     The same file is read by build_windows_installer.sh and
#     build_macos_installer.sh so all three platforms produce matching
#     installer artifacts for a given commit. Bump the version via
#     tools/bump_version.sh (patch/minor/major), or pass an explicit
#     --version=X.Y.Z to override (e.g. for a release tag without a
#     pre-commit).
#
#     Generated files (PyInstaller dist/build trees) land in dist/ and
#     build/ at the repo root (gitignored). Both trees are selectively
#     wiped after the installer is produced, matching the macOS builder:
#     the final deliverable at dist/sethlans-<V>-linux-x64.<ext> is
#     preserved; build/ and dist/{launcher,manager,worker,tray_helper}
#     are removed. `dist/sethlans-*` artifacts from sibling-platform
#     builds are left untouched.
#
#     Output: dist/sethlans-<VERSION>-linux-x64.run
#             (makeself self-extracting archive; the .run wraps
#             packaging/linux/install.sh + the four PyInstaller bundles
#             and runs install.sh on extract.)
#
# PLATFORM
#     Linux only. Targets x86_64; the artifact is named -linux-x64
#     accordingly. If/when arm64 support is added, adjust the arch
#     token in the output filename.
#
# PACKAGING
#     Step 6/6 delegates to packaging/linux/build_run.sh, which builds
#     a `makeself` .run self-extractor from the four PyInstaller dist
#     dirs plus packaging/linux/{install.sh,uninstall.sh,sethlans.desktop,
#     sethlans.png} and the repo-root LICENSE. Mirrors the macOS
#     builder's delegation to packaging/macos/build_dmg.sh.
#
# MINIMUM REQUIREMENTS
#     - Ubuntu 22.04+ / Debian 12+ / Fedora 38+ / similar modern distro
#     - Python 3.14 virtualenv at .venv-build/ with these packages installed:
#         * All of manager/requirements.txt
#         * All of worker/requirements.txt
#         * requirements-build.txt  (pyinstaller, pyinstaller-hooks-contrib,
#                                    PySide6-Essentials)
#     - Node.js 20+ with npm on PATH (for Angular build)
#     - System libs needed by PySide6 at runtime:
#         libxkbcommon0, libegl1, libopengl0, libfontconfig1,
#         plus xcb platform plugin deps
#     - makeself (for the .run self-extractor)
#     - ~1 GB free disk for PyInstaller work dir + dist/ output
#
# BOOTSTRAP (one-time, Debian/Ubuntu example)
#     sudo apt update
#     sudo apt install -y python3.14 python3.14-venv nodejs npm \
#         libxkbcommon0 libegl1 libopengl0 libfontconfig1 makeself
#     python3.14 -m venv .venv-build
#     .venv-build/bin/pip install -r manager/requirements.txt \
#         -r worker/requirements.txt -r requirements-build.txt
#
# USAGE
#     bash tools/build_linux_installer.sh
#     bash tools/build_linux_installer.sh --version=0.2.0
#
# NOTES
#     Scaffolded from tools/build_macos_installer.sh and kept
#     structurally similar so the version bump / prereq checks / build
#     steps / cleanup patterns match across the three platforms.

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
    -h|--help) sed -n '2,80p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
echo "=== Building Sethlans v${BUILD_VERSION} (Linux) ==="

# --- Environment checks ---
VENV_PYI=".venv-build/bin/pyinstaller"
RUN_SCRIPT="packaging/linux/build_run.sh"
DIST_ROOT="dist"
BUILD_ROOT="build"

if [ ! -x "$VENV_PYI" ]; then
  echo "ERROR: PyInstaller not found at $VENV_PYI"
  echo "See MINIMUM REQUIREMENTS at the top of this script."
  exit 1
fi
if [ ! -f "$RUN_SCRIPT" ]; then
  echo "ERROR: .run build script not found at $RUN_SCRIPT"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not on PATH (Node.js 20+ required)"
  exit 1
fi
if ! command -v makeself >/dev/null 2>&1; then
  echo "ERROR: makeself not on PATH (install via 'sudo apt install makeself')"
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

echo "=== 6/6 .run installer (v$BUILD_VERSION) ==="
bash "$RUN_SCRIPT" "$BUILD_VERSION"

OUTPUT="${DIST_ROOT}/sethlans-${BUILD_VERSION}-linux-x64.run"
if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: expected installer not produced at $OUTPUT"
  exit 1
fi

# --- Cleanup ---
# Installer is produced; the PyInstaller intermediate trees are no
# longer needed. Selective removal (matches tools/build_macos_installer.sh
# and tools/build_windows_installer.sh): wipe build/ and only the four
# PyInstaller bundle dirs under dist/, preserving any dist/sethlans-*
# deliverables so sibling-platform builds (macOS DMG, Windows exe if
# someone moves it here) survive a Linux build in a shared workspace.
echo "=== Cleanup ==="
rm -rf "${BUILD_ROOT:?}"
for component in launcher manager worker tray_helper; do
  rm -rf "${DIST_ROOT:?}/$component"
done
# build_run.sh wipes its staging dir on success; defensive removal here
# covers a partial/failed run.
rm -rf "${DIST_ROOT:?}/run-staging"
echo "Removed $BUILD_ROOT and $DIST_ROOT/{launcher,manager,worker,tray_helper}"

ls -lh "$OUTPUT"
echo ""
echo "Installer ready: $OUTPUT"
