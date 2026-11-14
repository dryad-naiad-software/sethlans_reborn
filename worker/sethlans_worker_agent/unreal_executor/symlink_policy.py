# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows remote symlink evaluation policy (Unreal foundation FR-WD-7, Sec N5).

Unreal is refused when remote-to-remote or remote-to-local symlink
evaluation is enabled, because a link on the share could then redirect
the worker's reads. Read once at startup, **fail closed**:

* any non-zero integer means enabled; a value of another type (string,
  binary) also counts as enabled;
* only ``FileNotFoundError`` (key or value absent) means the Windows
  default, disabled; any other ``OSError`` (for example access denied)
  propagates, and the caller disables Unreal.

Two locations are read: the values ``fsutil behavior set SymlinkEvaluation``
writes (``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem``), and,
defensively, the same value names under a policies key
(``HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Filesystems\\NTFS``). theta
has no symlink entry under any ``Policies`` key (checked 2026-11-14), so
the second read is a guard, not a verified Group Policy location.
"""
import sys

KEYS = (
    r'SYSTEM\CurrentControlSet\Control\FileSystem',
    r'SOFTWARE\Policies\Microsoft\Windows\Filesystems\NTFS',
)
VALUES = ('SymlinkRemoteToRemoteEvaluation', 'SymlinkRemoteToLocalEvaluation')


def _is_windows() -> bool:
    return sys.platform == 'win32'


def _value_enables(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return True  # unexpected type: fail closed
    return value != 0


def _key_enables(winreg, path: str) -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except FileNotFoundError:
        return False
    with key:
        for name in VALUES:
            try:
                value, _kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if _value_enables(value):
                return True
    return False


def remote_symlink_evaluation_enabled() -> bool:
    """True when R2R or R2L evaluation is (or may be) enabled. Raises on unreadable keys."""
    if not _is_windows():
        return False
    import winreg
    return any(_key_enables(winreg, path) for path in KEYS)
