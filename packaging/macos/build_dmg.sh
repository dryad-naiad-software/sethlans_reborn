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

# Create Applications symlink for drag-to-install
ln -sf /Applications "${STAGING_DIR}/Applications"

# Remove any existing DMG
rm -f "${DMG_PATH}"

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
