# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sweep of the Blender cache root, run only by the runner thread between fetches (BS-MGR-8).

Every file under the root that no READY or FETCHING row names is deleted.
Kept: ``<version>/<file_name>`` of READY and FETCHING rows, the
``.part/<file_name>`` and ``.part/<file_name>.part`` of FETCHING rows, and the
release-list cache at the top (``release_cache.json*`` and the hidden
``.release_cache.json.*`` temp files of its atomic write).

The walk never follows a link: a symbolic link or a junction is removed
itself, never what it points to, and nothing below it is visited. Empty
folders below the root are removed. A delete that fails (on Windows, a file
still being served) is logged and tried again at the next sweep.
"""

import logging
import os
from pathlib import Path
from typing import Set

from ...constants import BlenderBuildStatus
from ...models import BlenderBuild
from . import paths

logger = logging.getLogger(__name__)


def kept_paths() -> Set[str]:
    """Root-relative POSIX paths the sweep keeps."""
    keep = set()
    rows = BlenderBuild.objects.filter(
        status__in=(BlenderBuildStatus.READY, BlenderBuildStatus.FETCHING),
    ).values_list('version', 'file_name', 'status')
    for version, file_name, status in rows:
        keep.add(f"{version}/{file_name}")
        if status == BlenderBuildStatus.FETCHING:
            keep.add(f"{paths.PART_DIR}/{file_name}")
            keep.add(f"{paths.PART_DIR}/{file_name}.part")
    return keep


def _is_release_cache(relative: str) -> bool:
    name = paths.RELEASE_CACHE_NAME
    return '/' not in relative and (relative.startswith(name) or relative.startswith(f".{name}."))


def _is_link(entry: os.DirEntry) -> bool:
    return entry.is_symlink() or entry.is_junction()


def _remove_link(path: str) -> None:
    """Remove a link itself (a directory link on Windows needs ``rmdir``, which never follows it)."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def _delete(entry: os.DirEntry, relative: str) -> int:
    try:
        if _is_link(entry):
            _remove_link(entry.path)
        else:
            os.unlink(entry.path)
    except OSError as exc:
        logger.warning("Blender cache sweep: could not remove %s: %s", relative, exc)
        return 0
    logger.info("Blender cache sweep: removed %s.", relative)
    return 1


def _sweep_dir(folder: str, prefix: str, keep: Set[str]) -> int:
    removed = 0
    with os.scandir(folder) as entries:
        children = list(entries)
    for entry in children:
        relative = f"{prefix}{entry.name}"
        if _is_link(entry):
            removed += _delete(entry, relative)
        elif entry.is_dir(follow_symlinks=False):
            removed += _sweep_dir(entry.path, f"{relative}/", keep)
            _remove_if_empty(entry.path, relative)
        elif relative not in keep and not _is_release_cache(relative):
            removed += _delete(entry, relative)
    return removed


def _remove_if_empty(path: str, relative: str) -> None:
    try:
        with os.scandir(path) as entries:
            empty = next(entries, None) is None
        if empty:
            os.rmdir(path)
    except OSError as exc:
        logger.debug("Blender cache sweep: left the folder %s: %s", relative, exc)


def sweep(root: Path) -> int:
    """Delete every file under ``root`` no READY or FETCHING row names; returns how many were removed."""
    if not os.path.isdir(root):
        return 0
    return _sweep_dir(str(root), '', kept_paths())
