# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Platform-specific utility functions for tool management.

Builds the path of the Blender executable inside an install folder for the
current OS. The platform id itself (``windows-x64`` and so on) comes from the
one table in ``shared.blender_sources.platforms`` (BS-SRC-6).
"""
import platform
from pathlib import Path


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
