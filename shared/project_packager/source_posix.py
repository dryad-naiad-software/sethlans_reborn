# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
POSIX descriptor walks for the packager's containment checks (Unreal
foundation FR-PK-5, FR-PK-10). Imported off Windows only.

Every path below the project root is opened one component at a time,
relative to its parent's descriptor (``dir_fd``) and with ``O_NOFOLLOW``:
a directory or file swapped for a symlink fails the open (``ELOOP`` or
``ENOTDIR``) instead of being followed, so nothing outside the root can be
reached however the tree changes. Component names come from directory
listings, never ``..``.
"""
import os
from typing import Sequence

_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | _CLOEXEC
_FILE_FLAGS = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | _CLOEXEC | getattr(os, 'O_NONBLOCK', 0)
               | getattr(os, 'O_NOCTTY', 0))
_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


def open_dir(root: str, parts: Sequence[str]) -> int:
    """A descriptor of ``root/<parts...>``, each component opened without following links."""
    # The root is a resolved path (no link in its last component); a root later
    # swapped for a symlink fails here too.
    fd = os.open(root, _DIR_FLAGS | _NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, _DIR_FLAGS | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_file(root: str, parts: Sequence[str]) -> int:
    """A read-only descriptor of the file ``root/<parts...>``, no link followed on the way."""
    parent = open_dir(root, parts[:-1])
    try:
        return os.open(parts[-1], _FILE_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
