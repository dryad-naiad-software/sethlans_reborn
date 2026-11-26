# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one table of Blender platform ids (BS-SRC-6).

Each id is the platform part of an official build name,
``blender-<version>-<platform>.<ext>``, with the one archive kind the farm
installs from: ``.zip`` on Windows (never ``.msi`` or ``.msix``), ``.tar.xz``
on Linux and ``.dmg`` on macOS.
"""
import platform
import re
from typing import Optional

# Platform id -> archive extension (without the leading dot).
PLATFORM_EXTENSIONS = {
    "windows-x64": "zip",
    "windows-arm64": "zip",
    "linux-x64": "tar.xz",
    "linux-arm64": "tar.xz",
    "macos-x64": "dmg",
    "macos-arm64": "dmg",
}

# (platform.system(), platform.machine().lower()) -> platform id.
_MACHINES = {
    ("Windows", "amd64"): "windows-x64",
    ("Windows", "arm64"): "windows-arm64",
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Darwin", "x86_64"): "macos-x64",
    ("Darwin", "arm64"): "macos-arm64",
}

VERSION_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")  # use fullmatch
SERIES_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}")  # use fullmatch


def current_platform_id(system: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    """The platform id of this machine (or of ``system``/``machine``), else None."""
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    return _MACHINES.get((system, machine.lower()))


def is_platform_id(value: object) -> bool:
    """True when ``value`` is one of the six platform ids."""
    return isinstance(value, str) and value in PLATFORM_EXTENSIONS


def is_version(value: object) -> bool:
    """True for a full ``X.Y.Z`` version string (BS-REL-7)."""
    return isinstance(value, str) and VERSION_RE.fullmatch(value) is not None


def series_of(version: str) -> str:
    """``"4.5"`` for ``"4.5.8"``."""
    return ".".join(version.split(".")[:2])


def version_key(version: str) -> tuple:
    """Integer tuple for ordering versions: ``(4, 5, 10) > (4, 5, 9)``."""
    return tuple(int(part) for part in version.split("."))


def build_file_name(version: str, platform_id: str) -> str:
    """``blender-<version>-<platform>.<ext>``; raises ``ValueError`` for an unknown platform or bad version."""
    if not is_version(version):
        raise ValueError(f"not a Blender version: {version!r}")
    if not is_platform_id(platform_id):
        raise ValueError(f"not a Blender platform id: {platform_id!r}")
    return f"blender-{version}-{platform_id}.{PLATFORM_EXTENSIONS[platform_id]}"
