# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Platform-independent handling of Windows path strings.

Unreal rendering is Windows-only, so the paths the worker builds into UE
command lines and environments, and the image paths it compares, are
Windows paths. A drive-letter path (``D:\\uecache``, ``C:/UE/...``) is
therefore joined, normalized and case-folded with ``ntpath`` on **every**
platform, so the results (and the unit tests' golden values) are identical
on the Linux and macOS CI runners. Any other path (a POSIX ``tmp_path`` in a
test, the fallback runner off Windows) uses ``os.path``.

Real filesystem calls (``os.stat``, ``lstat``, ...) never go through here.
"""
import ntpath
import os
import re

_WIN_DRIVE = re.compile(r'^[A-Za-z]:[\\/]', re.ASCII)


def is_windows_style(path) -> bool:
    """True for a drive-letter path such as ``D:\\uecache`` or ``C:/UE``."""
    return bool(_WIN_DRIVE.match(str(path)))


def module_for(path):
    """``ntpath`` for a drive-letter path, else ``os.path``."""
    return ntpath if is_windows_style(path) else os.path


def join(base, *parts) -> str:
    text = str(base)
    return module_for(text).join(text, *parts)


def normpath(path) -> str:
    text = str(path)
    return module_for(text).normpath(text)


def normcase(path) -> str:
    """Case-fold (and, for a Windows path, unify separators) with the path's own flavour."""
    text = str(path)
    return module_for(text).normcase(text)
