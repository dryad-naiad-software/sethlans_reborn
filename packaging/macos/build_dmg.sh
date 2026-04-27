#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Build a macOS .dmg installer from the PyInstaller .app bundle output.
#
# Usage: packaging/macos/build_dmg.sh <build_version>
# Example: packaging/macos/build_dmg.sh 0.2.0.abc12
#
# <build_version> is the semver plus a 5-char git commit suffix
# (e.g. `0.2.0.abc12`). The script derives the strict-semver part for
# Apple's CFBundleShortVersionString (which only accepts X.Y.Z) and
# uses the full build_version for CFBundleVersion + filename + DMG name.
#
# Expects PyInstaller output at dist/Sethlans.app (and dist/SethlansHelper.app).
# Produces: dist/sethlans-<build_version>-macos-arm64.dmg

set -euo pipefail

BUILD_VERSION="${1:?Usage: build_dmg.sh <build_version>}"
SEMVER="$(echo "$BUILD_VERSION" | cut -d. -f1-3)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
APP_NAME="Sethlans.app"
DMG_NAME="sethlans-${BUILD_VERSION}-macos-arm64"
DMG_PATH="${DIST_DIR}/${DMG_NAME}.dmg"
STAGING_DIR="${DIST_DIR}/dmg-staging"

echo "--- Building macOS DMG: ${DMG_NAME} (semver ${SEMVER}) ---"

# Validate that the .app bundle exists
if [ ! -d "${DIST_DIR}/${APP_NAME}" ]; then
    echo "[ERROR] ${APP_NAME} not found at ${DIST_DIR}/${APP_NAME}" >&2
    echo "Run PyInstaller with launcher.spec first." >&2
    exit 1
fi

# Strictly validate required component bundles. tray_helper is intentionally
# omitted here — it's optional on macOS (the SethlansHelper.app handles the
# tray UI) and the copy loop below already warns if it's missing. wizard is
# required: a DMG without the setup wizard ships an unconfigurable app.
for component in manager worker wizard; do
    if [ ! -d "${DIST_DIR}/${component}" ]; then
        echo "[ERROR] ${component} bundle not found at ${DIST_DIR}/${component}" >&2
        exit 1
    fi
done

# Clean staging area
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"

# Copy the .app bundle to staging
cp -R "${DIST_DIR}/${APP_NAME}" "${STAGING_DIR}/${APP_NAME}"

# Align CFBundleExecutable with the actual PyInstaller binary name.
# launcher.spec names the exe `run_launcher` (shared with Windows),
# while Info.plist.template declares `CFBundleExecutable=sethlans`.
# Rename the binary in the staged bundle so macOS Launch Services can
# find the main executable. Must happen BEFORE the ad-hoc re-sign —
# codesign hashes file names, so renaming after signing breaks the seal.
MACOS_DIR="${STAGING_DIR}/${APP_NAME}/Contents/MacOS"
if [ -f "${MACOS_DIR}/run_launcher" ] && [ ! -f "${MACOS_DIR}/sethlans" ]; then
    mv "${MACOS_DIR}/run_launcher" "${MACOS_DIR}/sethlans"
fi

# Copy component bundles into the .app's Resources
RESOURCES="${STAGING_DIR}/${APP_NAME}/Contents/Resources"
mkdir -p "${RESOURCES}/bin"

for component in manager worker tray_helper wizard; do
    if [ -d "${DIST_DIR}/${component}" ]; then
        cp -R "${DIST_DIR}/${component}" "${RESOURCES}/bin/${component}"
    else
        echo "[WARNING] ${component} bundle not found at ${DIST_DIR}/${component}" >&2
    fi
done

# Copy the tray helper .app if it exists (macOS-specific)
if [ -d "${DIST_DIR}/SethlansHelper.app" ]; then
    cp -R "${DIST_DIR}/SethlansHelper.app" \
        "${RESOURCES}/bin/tray_helper/"
fi

# Defensively re-sign the nested helper bundle. The `cp -R` above may or
# may not preserve the ad-hoc signature cleanly across macOS versions,
# and the outer re-sign below needs a valid seal on every nested bundle.
#
# Inside-out signing for the helper: sign nested Mach-O binaries first
# (.dylib / .so under Contents/), then the executable, then the outer
# bundle. `--deep` is intentionally avoided here — Apple deprecated it
# in macOS 11 and notarytool will reject `--deep`-signed artifacts when
# the Developer ID + notarization workstream lands (#85).
if [ -d "${RESOURCES}/bin/tray_helper/SethlansHelper.app" ]; then
    HELPER_BUNDLE="${RESOURCES}/bin/tray_helper/SethlansHelper.app"
    # Sign nested Mach-O first (dylibs, shared objects)
    find "${HELPER_BUNDLE}" -type f \( -name "*.dylib" -o -name "*.so" \) \
        -exec codesign --force --sign - {} +
    # Sign the helper's main executable explicitly if present
    HELPER_EXE="${HELPER_BUNDLE}/Contents/MacOS/run_tray_helper"
    if [ -f "${HELPER_EXE}" ]; then
        codesign --force --sign - "${HELPER_EXE}"
    fi
    # Outer helper bundle last
    codesign --force --sign - "${HELPER_BUNDLE}"
fi

# Apply Info.plist from template. Two substitutions: BUILD_VERSION
# (semver+git hash, accepted by CFBundleVersion) and SEMVER
# (strict X.Y.Z, required by CFBundleShortVersionString).
PLIST_TEMPLATE="${SCRIPT_DIR}/Info.plist.template"
if [ -f "${PLIST_TEMPLATE}" ]; then
    sed -e "s/\${BUILD_VERSION}/${BUILD_VERSION}/g" \
        -e "s/\${SEMVER}/${SEMVER}/g" \
        "${PLIST_TEMPLATE}" \
        > "${STAGING_DIR}/${APP_NAME}/Contents/Info.plist"
fi

# Copy application icon
if [ -f "${SCRIPT_DIR}/sethlans.icns" ]; then
    cp "${SCRIPT_DIR}/sethlans.icns" "${RESOURCES}/sethlans.icns"
fi

# Write version.json
cat > "${RESOURCES}/version.json" <<EOF
{"version": "${BUILD_VERSION}", "semver": "${SEMVER}", "platform": "macos-arm64"}
EOF

# Strip any inherited com.apple.quarantine xattrs from bundle contents.
# PNGs under shared/tray/assets/ can be quarantined in the build
# workspace (from prior downloads) and PyInstaller carries that forward
# into COLLECT. A user drag-install from the DMG leaves those xattrs in
# place on inner files, which has tripped up some sandboxed readers.
# Must run BEFORE the re-sign (defensive — clears state the signature
# should not be sealing).
xattr -cr "${STAGING_DIR}/${APP_NAME}" || true

# Re-sign the bundle AFTER every mutation of Contents/ (Info.plist,
# sethlans.icns, version.json). PyInstaller's embedded ad-hoc signature
# was already invalidated by those writes; any re-sign applied before
# them would be invalidated again and the verify below would abort.
# Ad-hoc sign is enough to clear Gatekeeper's "damaged" gate; Developer
# ID signing + notarization are a separate workstream — see GitHub #85.
#
# Inside-out signing: nested Mach-O first, then component launchers,
# then nested .app bundles, then the outer bundle last. Apple
# deprecated `codesign --deep` starting macOS 11 — notarytool rejects
# `--deep`-signed bundles, so we avoid it here even for ad-hoc signing
# to keep the build forward-compatible with #85 (Developer ID +
# notarization). See:
# https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution

# 1. Sign every nested Mach-O binary (.dylib, .so) under the staged bundle.
find "${STAGING_DIR}/${APP_NAME}" -type f \( -name "*.dylib" -o -name "*.so" \) \
    -exec codesign --force --sign - {} +

# 2. Sign component executables explicitly (PyInstaller-frozen launchers).
for component in wizard manager worker launcher; do
    bin="${RESOURCES}/bin/${component}/run_${component}"
    if [ -f "$bin" ]; then
        codesign --force --sign - "$bin"
    fi
done

# 3. tray_helper bundled as a plain PyInstaller dir (not a .app) on
#    Linux/Windows but kept for parity if present here.
if [ -f "${RESOURCES}/bin/tray_helper/run_tray_helper" ]; then
    codesign --force --sign - "${RESOURCES}/bin/tray_helper/run_tray_helper"
fi

# 4. Outer bundle LAST. Signing the parent re-seals references to every
#    nested signature already applied above.
codesign --force --sign - "${STAGING_DIR}/${APP_NAME}"

# Create Applications symlink for drag-to-install
ln -sf /Applications "${STAGING_DIR}/Applications"

# Remove any existing DMG
rm -f "${DMG_PATH}"

# Fail the build loudly if the re-sign didn't take. Prevents shipping
# a broken DMG from CI or dev machines.
echo "--- Verifying bundle signature ---"
codesign --verify --deep --strict "${STAGING_DIR}/${APP_NAME}"

# Create the DMG using hdiutil
echo "--- Creating DMG with hdiutil ---"
hdiutil create \
    -volname "Sethlans ${SEMVER}" \
    -srcfolder "${STAGING_DIR}" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "${DMG_PATH}"

# Clean up staging
rm -rf "${STAGING_DIR}"

echo "--- DMG created at ${DMG_PATH} ---"
echo "--- Size: $(du -h "${DMG_PATH}" | cut -f1) ---"
