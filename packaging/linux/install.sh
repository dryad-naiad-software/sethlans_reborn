#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Linux install script embedded in the makeself .run archive.
#
# Interactive: sudo ./sethlans-<version>-linux-x64.run
# Unattended:  sudo SETHLANS_ENROLLMENT_KEY=<key> \
#              ./sethlans-<version>-linux-x64.run \
#              --unattended --topology=worker \
#              --manager-host=https://studio:8080 \
#              --enrollment-key-file=<path> --user=<username>

set -euo pipefail

# --- Defaults ---
PREFIX="/opt/sethlans"
SYMLINK_PATH="/usr/local/bin/sethlans"
DESKTOP_DIR="/usr/share/applications"
UNATTENDED=0
TOPOLOGY=""
MANAGER_HOST=""
ENROLLMENT_KEY_FILE=""
TARGET_USER="${SUDO_USER:-$(whoami)}"
AUTOSTART=0

# --- Denylist for --prefix (prefix-match, not exact-match) ---
DENIED_PREFIXES="/ /etc /bin /sbin /usr/bin /usr/sbin /boot /dev /proc /sys"

# --- Parse arguments ---
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix=*)
            PREFIX="${1#*=}"
            ;;
        --unattended)
            UNATTENDED=1
            ;;
        --topology=*)
            TOPOLOGY="${1#*=}"
            ;;
        --manager-host=*)
            MANAGER_HOST="${1#*=}"
            ;;
        --enrollment-key-file=*)
            ENROLLMENT_KEY_FILE="${1#*=}"
            ;;
        --user=*)
            TARGET_USER="${1#*=}"
            ;;
        --autostart=*)
            AUTOSTART="${1#*=}"
            ;;
        --help|-h)
            echo "Usage: install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --prefix=<path>               Install location (default: /opt/sethlans)"
            echo "  --unattended                   Non-interactive install"
            echo "  --topology=<type>              manager|manager_worker|worker"
            echo "  --manager-host=<url>           Manager URL for worker topology"
            echo "  --enrollment-key-file=<path>   Path to file containing enrollment key"
            echo "  --user=<username>              User to run enrollment as"
            echo "  --autostart=0|1                Enable autostart (default: 0)"
            echo ""
            echo "Enrollment key can also be passed via SETHLANS_ENROLLMENT_KEY env var."
            exit 0
            ;;
        *)
            echo "[WARNING] Unknown option: $1" >&2
            ;;
    esac
    shift
done

# --- Validate --prefix against denylist (prefix-match) ---
# Normalize prefix (remove trailing slash for comparison)
NORM_PREFIX="${PREFIX%/}"
for denied in $DENIED_PREFIXES; do
    case "$NORM_PREFIX" in
        "$denied"|"$denied"/*)
            echo "[ERROR] Cannot install to ${PREFIX} — protected system path." >&2
            exit 1
            ;;
    esac
done

# --- Check root privileges ---
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This installer requires root privileges." >&2
    echo "Run with: sudo $0 $*" >&2
    exit 1
fi

echo "--- Installing Sethlans to ${PREFIX} ---"

# --- Create install directory and copy files ---
mkdir -p "${PREFIX}"
# Force world-traversable perms on PREFIX. Under makeself, root's umask can
# leave mkdir at 0700, which prevents non-root processes (GNOME Shell,
# gtk-launch) from traversing /opt/sethlans to validate the .desktop file's
# Exec= path, causing the app to silently disappear from the app grid.
chmod 0755 "${PREFIX}"
cp -R bin/ "${PREFIX}/bin/"
cp -f LICENSE.txt "${PREFIX}/LICENSE.txt" 2>/dev/null || true
cp -f version.json "${PREFIX}/version.json" 2>/dev/null || true

# Create main executable symlink within install dir
ln -sf "bin/launcher/run_launcher" "${PREFIX}/sethlans"

# Make executables runnable
find "${PREFIX}/bin" -type f -name "run_*" -exec chmod +x {} \;

# Defensive: ensure the entire bin/ subtree is readable+traversable by all
# users so non-root processes can resolve /opt/sethlans/sethlans ->
# bin/launcher/run_launcher. Uses capital X so dirs and already-executable
# files get +x, but plain data files don't.
chmod -R go+rX "${PREFIX}/bin"

# --- Create system-wide symlink ---
mkdir -p "$(dirname "${SYMLINK_PATH}")"
ln -sf "${PREFIX}/sethlans" "${SYMLINK_PATH}"
echo "Created symlink: ${SYMLINK_PATH} -> ${PREFIX}/sethlans"

# --- Install .desktop file ---
if [ -f sethlans.desktop ]; then
    sed "s|@PREFIX@|${PREFIX}|g" sethlans.desktop \
        > "${DESKTOP_DIR}/sethlans.desktop"
    chmod 644 "${DESKTOP_DIR}/sethlans.desktop"
    # Update desktop database if available
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
    fi
    echo "Installed desktop entry."
fi

# --- Install icon (source PNG is 512x512) ---
if [ -f sethlans.png ]; then
    ICON_DIR="/usr/share/icons/hicolor/512x512/apps"
    mkdir -p "${ICON_DIR}"
    cp sethlans.png "${ICON_DIR}/sethlans.png"
    # Force world-readable perms on the icon. Under makeself, root's umask
    # leaves the staged PNG at 0600, and `cp` preserves source perms — so
    # GNOME Shell (running as the user) can't read the icon and the app
    # entry falls back to the generic missing-icon placeholder.
    chmod 644 "${ICON_DIR}/sethlans.png"
    # Update icon cache if available
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
    fi
fi

# --- Install uninstall script ---
cp uninstall.sh "${PREFIX}/uninstall.sh"
chmod +x "${PREFIX}/uninstall.sh"

# --- Unattended configuration ---
if [ "${UNATTENDED}" -eq 1 ]; then
    echo "--- Performing unattended configuration ---"

    # Validate TARGET_USER against passwd (no shell injection via eval)
    if ! id "${TARGET_USER}" &>/dev/null; then
        echo "[ERROR] User '${TARGET_USER}' does not exist." >&2
        exit 1
    fi
    TARGET_HOME=$(getent passwd "${TARGET_USER}" | cut -d: -f6)
    if [ -z "${TARGET_HOME}" ]; then
        echo "[ERROR] Could not resolve home directory for user: ${TARGET_USER}" >&2
        exit 1
    fi
    XDG_DATA="${TARGET_HOME}/.local/share/sethlans"

    # Create data directory owned by target user
    su -c "mkdir -p '${XDG_DATA}'" "${TARGET_USER}"

    # Write topology.json (validate against allowlist to prevent injection)
    if [ -n "${TOPOLOGY}" ]; then
        case "${TOPOLOGY}" in
            manager|manager_worker|worker) ;;
            *)
                echo "[ERROR] Invalid topology: ${TOPOLOGY}" >&2
                echo "Allowed: manager, manager_worker, worker" >&2
                exit 1
                ;;
        esac
        printf '{"topology": "%s"}\n' "${TOPOLOGY}" | \
            su -c "cat > '${XDG_DATA}/topology.json'" "${TARGET_USER}"
        echo "Wrote topology: ${TOPOLOGY}"
    fi

    # Write setup_complete sentinel
    su -c "touch '${XDG_DATA}/.setup_complete'" "${TARGET_USER}"

    # Perform enrollment if key is available
    ENROLLMENT_KEY="${SETHLANS_ENROLLMENT_KEY:-}"

    if [ -n "${ENROLLMENT_KEY_FILE}" ] && [ -f "${ENROLLMENT_KEY_FILE}" ]; then
        ENROLLMENT_KEY="$(cat "${ENROLLMENT_KEY_FILE}")"
    fi

    if [ -n "${ENROLLMENT_KEY}" ] && [ -n "${MANAGER_HOST}" ]; then
        echo "Performing worker enrollment as ${TARGET_USER}..."
        # Pass secrets via inherited env var, never shell interpolation
        SETHLANS_ENROLLMENT_KEY="${ENROLLMENT_KEY}" \
        SETHLANS_WORKER_MANAGER_URL="${MANAGER_HOST}" \
            su -m -c "'${PREFIX}/bin/worker/run_worker' --enroll-and-exit" \
            "${TARGET_USER}" || {
            echo "[WARNING] Enrollment failed. Worker may need manual enrollment." >&2
        }
    fi
fi

echo "--- Sethlans installed successfully to ${PREFIX} ---"
echo "Run 'sethlans' to start the application."
