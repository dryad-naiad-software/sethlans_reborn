#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Uninstall script for Sethlans on Linux.
# Placed in the install directory (e.g., /opt/sethlans/uninstall.sh)
# by install.sh.
#
# Usage: sudo /opt/sethlans/uninstall.sh [--remove-data]

set -euo pipefail

REMOVE_DATA=0

for arg in "$@"; do
    case "$arg" in
        --remove-data)
            REMOVE_DATA=1
            ;;
        --help|-h)
            echo "Usage: uninstall.sh [--remove-data]"
            echo ""
            echo "Options:"
            echo "  --remove-data    Also remove user data (databases, configs, assets)"
            echo ""
            echo "Without --remove-data, only the application files are removed."
            echo "User data in ~/.local/share/sethlans/ is preserved."
            exit 0
            ;;
    esac
done

# Determine install directory (where this script lives)
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
SYMLINK_PATH="/usr/local/bin/sethlans"
DESKTOP_FILE="/usr/share/applications/sethlans.desktop"
ICON_FILE="/usr/share/icons/hicolor/512x512/apps/sethlans.png"

# Check root privileges
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] Uninstall requires root privileges." >&2
    echo "Run with: sudo $0 $*" >&2
    exit 1
fi

echo "--- Uninstalling Sethlans from ${INSTALL_DIR} ---"

# Stop running Sethlans processes
for proc in run_manager run_worker run_tray_helper run_launcher; do
    pkill -f "${proc}" 2>/dev/null || true
done

# Remove system symlink
if [ -L "${SYMLINK_PATH}" ]; then
    rm -f "${SYMLINK_PATH}"
    echo "Removed symlink: ${SYMLINK_PATH}"
fi

# Remove desktop entry
if [ -f "${DESKTOP_FILE}" ]; then
    rm -f "${DESKTOP_FILE}"
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database /usr/share/applications/ 2>/dev/null || true
    fi
    echo "Removed desktop entry."
fi

# Remove icon
if [ -f "${ICON_FILE}" ]; then
    rm -f "${ICON_FILE}"
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
    fi
fi

# Remove install directory
rm -rf "${INSTALL_DIR}"
echo "Removed install directory: ${INSTALL_DIR}"

# Optionally remove user data
if [ "${REMOVE_DATA}" -eq 1 ]; then
    echo "[WARNING] Removing ALL user data (databases, configs, assets)..."
    # Remove data for all users who have it
    for user_home in /home/*; do
        DATA_DIR="${user_home}/.local/share/sethlans"
        if [ -d "${DATA_DIR}" ]; then
            rm -rf "${DATA_DIR}"
            echo "Removed data: ${DATA_DIR}"
        fi
    done
    # Also check root's data dir
    if [ -d "/root/.local/share/sethlans" ]; then
        rm -rf "/root/.local/share/sethlans"
    fi
else
    echo "User data preserved. Use --remove-data to also remove data."
fi

echo "--- Sethlans uninstalled successfully ---"
