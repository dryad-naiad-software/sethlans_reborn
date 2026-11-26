# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Where the manager keeps its Blender builds, and read-only checks of them (BS-MGR-2, BS-MGR-12).

Layout under the root (``settings.BLENDER_CACHE_ROOT``, by default
``get_data_dir('manager') / 'blender'``):

- ``<root>/<version>/<file_name>``: a READY build;
- ``<root>/.part/``: downloads in progress (``shared.blender_sources``);
- ``<root>/release_cache.json`` (with its ``.lock`` and atomic-write temp
  files): the release-list cache (BS-REL-5).

Nothing here writes: only the runner thread changes files under the root
(BS-MGR-6). The paths are built from the row's validated ``version`` and the
``file_name`` the manager built itself, and are checked to stay under the
root.
"""

import os
import stat
from pathlib import Path
from typing import Optional

from django.conf import settings

from shared.frozen_paths import get_data_dir

PART_DIR = '.part'
RELEASE_CACHE_NAME = 'release_cache.json'


class CacheRootError(Exception):
    """The configured cache root is unusable (inside ``MEDIA_ROOT``)."""


def cache_root() -> Path:
    """The cache root now in force."""
    configured = getattr(settings, 'BLENDER_CACHE_ROOT', None)
    return Path(configured) if configured else get_data_dir('manager') / 'blender'


def check_root(root: Path) -> None:
    """Refuse a root inside ``MEDIA_ROOT``, which ``/media/`` serves to anyone (BS-MGR-2)."""
    media = getattr(settings, 'MEDIA_ROOT', None)
    if not media:
        return
    real_root, real_media = Path(os.path.realpath(root)), Path(os.path.realpath(media))
    if real_root == real_media or real_root.is_relative_to(real_media) or real_media.is_relative_to(real_root):
        raise CacheRootError(f"the Blender cache {root} overlaps MEDIA_ROOT {media}")


def release_cache_file(root: Path) -> Path:
    return root / RELEASE_CACHE_NAME


def part_dir(root: Path) -> Path:
    return root / PART_DIR


def build_path(root: Path, version: str, file_name: str) -> Optional[Path]:
    """``<root>/<version>/<file_name>``, or None when it would leave the root (BS-MGR-12)."""
    path = root / version / file_name
    real_root = Path(os.path.realpath(root))
    # No link and no ``..`` anywhere below the root: the resolved path must be
    # exactly the one built from the two names.
    if Path(os.path.realpath(path)) != real_root / version / file_name:
        return None
    return path


def regular_size(path: Path) -> Optional[int]:
    """The size of ``path`` when it is a regular file (a link counts as missing), else None."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info.st_size if stat.S_ISREG(info.st_mode) else None


def ready_file(row, root: Optional[Path] = None) -> Optional[Path]:
    """The file of a READY ``row`` when present with the recorded size, else None (BS-MGR-9)."""
    root = cache_root() if root is None else root
    path = build_path(root, row.version, row.file_name)
    if path is None or row.size is None:
        return None
    return path if regular_size(path) == row.size else None
