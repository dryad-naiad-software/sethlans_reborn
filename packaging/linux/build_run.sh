#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Build a Linux .run self-extracting installer using makeself.
#
# Usage: packaging/linux/build_run.sh <version>
# Example: packaging/linux/build_run.sh 0.1.0
#
# Expects PyInstaller output at dist/{manager,worker,tray_helper,launcher}/
# Produces: dist/sethlans-<version>-linux-x64.run

set -euo pipefail

VERSION="${1:?Usage: build_run.sh <version>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
STAGING_DIR="${DIST_DIR}/run-staging"
RUN_NAME="sethlans-${VERSION}-linux-x64"
RUN_PATH="${DIST_DIR}/${RUN_NAME}.run"

echo "--- Building Linux .run installer: ${RUN_NAME} ---"

# Validate makeself is available
if ! command -v makeself &>/dev/null; then
    echo "[ERROR] makeself is not installed." >&2
    echo "Install with: sudo apt-get install makeself" >&2
    exit 1
fi

# Validate component bundles exist
for component in manager worker launcher; do
    if [ ! -d "${DIST_DIR}/${component}" ]; then
        echo "[ERROR] ${component} bundle not found at ${DIST_DIR}/${component}" >&2
        exit 1
    fi
done

# Clean and create staging area
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}/bin"

# Copy component bundles
for component in manager worker tray_helper launcher; do
    if [ -d "${DIST_DIR}/${component}" ]; then
        cp -R "${DIST_DIR}/${component}" "${STAGING_DIR}/bin/${component}"
    fi
done

# Create main executable symlink
ln -sf "bin/launcher/run_launcher" "${STAGING_DIR}/sethlans"

# Copy license
if [ -f "${PROJECT_ROOT}/LICENSE.txt" ]; then
    cp "${PROJECT_ROOT}/LICENSE.txt" "${STAGING_DIR}/LICENSE.txt"
fi

# Write version.json
cat > "${STAGING_DIR}/version.json" <<EOF
{"version": "${VERSION}", "platform": "linux-x64"}
EOF

# Copy install/uninstall scripts
cp "${SCRIPT_DIR}/install.sh" "${STAGING_DIR}/install.sh"
cp "${SCRIPT_DIR}/uninstall.sh" "${STAGING_DIR}/uninstall.sh"
cp "${SCRIPT_DIR}/sethlans.desktop" "${STAGING_DIR}/sethlans.desktop"
chmod +x "${STAGING_DIR}/install.sh"
chmod +x "${STAGING_DIR}/uninstall.sh"

# Copy icon if available
if [ -f "${SCRIPT_DIR}/sethlans.png" ]; then
    cp "${SCRIPT_DIR}/sethlans.png" "${STAGING_DIR}/sethlans.png"
fi

# Remove existing .run
rm -f "${RUN_PATH}"

# Build the self-extracting archive
echo "--- Creating makeself archive ---"
makeself \
    --gzip \
    --nox11 \
    "${STAGING_DIR}" \
    "${RUN_PATH}" \
    "Sethlans ${VERSION} Installer" \
    ./install.sh

echo "--- .run installer created at ${RUN_PATH} ---"
echo "--- Size: $(du -h "${RUN_PATH}" | cut -f1) ---"

# Clean up staging
rm -rf "${STAGING_DIR}"
