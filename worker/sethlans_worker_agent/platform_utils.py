# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Platform-specific utility functions for tool management.

Provides standalone functions for determining the current platform identifier
and constructing the correct Blender executable path for the current OS.
"""
import platform
from pathlib import Path


def get_platform_identifier():
    """
    Determines the platform identifier string (e.g., 'windows-x64').

    This is used to match the worker's OS and architecture with the correct
    Blender download file.

    Returns:
        str or None: The platform identifier string, or None if the platform
                     is not supported.
    """
    system = platform.system().lower()
    arch = platform.machine().lower()

    if system == "windows":
        return "windows-x64" if "64" in arch else "windows-x86"
    elif system == "linux":
        if arch == "x86_64":
            return "linux-x64"
        elif arch == "aarch64":
            return "linux-arm64"
    elif system == "darwin":  # macOS
        return "macos-arm64" if "arm" in arch or "aarch64" in arch else "macos-x64"
    return None


def get_executable_path_for_blender(base_dir, install_dir_name):
    """
    Constructs the full path to the Blender executable within an install folder.

    This handles the different file paths for the Blender executable on
    Windows, Linux, and macOS.

    Args:
        base_dir (Path): The base directory containing Blender installations.
        install_dir_name (str): The name of the installation directory
            (e.g., 'blender-4.1.1-windows-x64').

    Returns:
        Path: The full path to the Blender executable.
    """
    base_path = Path(base_dir) / install_dir_name
    if platform.system() == "Windows":
        return base_path / "blender.exe"
    elif platform.system() == "Darwin":  # macOS
        # The .app is a directory, so we need to point inside it
        return base_path / "Blender.app" / "Contents" / "MacOS" / "Blender"
    else:  # Linux
        return base_path / "blender"
