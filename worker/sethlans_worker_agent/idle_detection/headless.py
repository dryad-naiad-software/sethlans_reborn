# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Headless and Docker environment detection (FR-3).

Determines whether the host is a dedicated render node (no interactive
desktop) so the worker can skip idle detection and treat the machine as
always-available.
"""
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_docker() -> bool:
    """Detect Docker container via /.dockerenv or cgroup inspection."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup_path = Path("/proc/1/cgroup")
        if cgroup_path.exists():
            text = cgroup_path.read_text(encoding="utf-8", errors="replace")
            if "docker" in text:
                return True
    except OSError:
        pass
    return False


def _is_headless_linux() -> bool:
    """Linux: headless when no DISPLAY and no WAYLAND_DISPLAY but /dev/dri exists."""
    has_display = bool(os.environ.get("DISPLAY"))
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_dri = Path("/dev/dri").exists()
    return not has_display and not has_wayland and has_dri


def _is_headless_windows() -> bool:
    """Windows: headless when no explorer.exe is running.

    On Windows Server without an interactive desktop session,
    explorer.exe is absent.
    """
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == "explorer.exe":
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return True
    except Exception:
        return False


def _is_headless_macos() -> bool:
    """macOS: headless when WindowServer is not running."""
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] == "WindowServer":
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return True
    except Exception:
        return False


def is_headless() -> bool:
    """Detect if the current host is a headless render node.

    Returns True if:
    - Docker: /.dockerenv exists or 'docker' in /proc/1/cgroup
    - Linux: no DISPLAY and no WAYLAND_DISPLAY, but /dev/dri exists
    - Windows: no explorer.exe process
    - macOS: no WindowServer process

    Docker detection is checked first on all platforms.
    """
    if _is_docker():
        logger.info("Docker container detected -- headless mode.")
        return True

    if sys.platform.startswith("linux"):
        result = _is_headless_linux()
    elif sys.platform == "win32":
        result = _is_headless_windows()
    elif sys.platform == "darwin":
        result = _is_headless_macos()
    else:
        result = False

    if result:
        logger.info(
            "Headless host detected -- idle detection disabled, "
            "worker is always-available."
        )
    return result
