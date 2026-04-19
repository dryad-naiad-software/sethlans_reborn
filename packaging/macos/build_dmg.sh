#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Build a macOS .dmg installer from the PyInstaller .app bundle output.
#
# Usage: packaging/macos/build_dmg.sh <version>
# Example: packaging/macos/build_dmg.sh 0.1.0
#
# Expects PyInstaller output at dist/Sethlans.app (and dist/SethlansHelper.app).
# Produces: dist/sethlans-<version>-macos-arm64.dmg

set -euo pipefail

VERSION="${1:?Usage: build_dmg.sh <version>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
APP_NAME="Sethlans.app"
DMG_NAME="sethlans-${VERSION}-macos-arm64"
DMG_PATH="${DIST_DIR}/${DMG_NAME}.dmg"
STAGING_DIR="${DIST_DIR}/dmg-staging"

echo "--- Building macOS DMG: ${DMG_NAME} ---"

# Validate that the .app bundle exists
if [ ! -d "${DIST_DIR}/${APP_NAME}" ]; then
    echo "[ERROR] ${APP_NAME} not found at ${DIST_DIR}/${APP_NAME}" >&2
    echo "Run PyInstaller with launcher.spec first." >&2
    exit 1
fi

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

for component in manager worker tray_helper; do
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
if [ -d "${RESOURCES}/bin/tray_helper/SethlansHelper.app" ]; then
    codesign --force --deep --sign - \
        "${RESOURCES}/bin/tray_helper/SethlansHelper.app"
fi

# Apply Info.plist from template
PLIST_TEMPLATE="${SCRIPT_DIR}/Info.plist.template"
if [ -f "${PLIST_TEMPLATE}" ]; then
    sed "s/\${VERSION}/${VERSION}/g" "${PLIST_TEMPLATE}" \
        > "${STAGING_DIR}/${APP_NAME}/Contents/Info.plist"
fi

# Copy application icon
if [ -f "${SCRIPT_DIR}/sethlans.icns" ]; then
    cp "${SCRIPT_DIR}/sethlans.icns" "${RESOURCES}/sethlans.icns"
fi

# Write version.json
cat > "${RESOURCES}/version.json" <<EOF
{"version": "${VERSION}", "platform": "macos-arm64"}
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
codesign --force --deep --sign - "${STAGING_DIR}/${APP_NAME}"

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
    -volname "Sethlans ${VERSION}" \
    -srcfolder "${STAGING_DIR}" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "${DMG_PATH}"

# Clean up staging
rm -rf "${STAGING_DIR}"

echo "--- DMG created at ${DMG_PATH} ---"
echo "--- Size: $(du -h "${DMG_PATH}" | cut -f1) ---"
