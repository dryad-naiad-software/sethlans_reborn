# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reparse-safe filesystem helpers for ``CACHE_DIR`` (Unreal foundation
FR-PA-21).

* Every walk uses ``os.scandir`` and never follows a link; the file stats
  come from ``os.lstat`` on each path (on Windows that opens the file, so
  sizes and times are exact even for a hardlink changed through another
  name, where the directory entry may be stale).
* ``remove_tree`` is a custom delete: a symlink or junction is removed
  **as a link** (``unlink``/``rmdir`` on the link itself) and never
  descended into. ``shutil.rmtree`` is not used under ``CACHE_DIR``.
* Nothing here calls ``chmod`` or changes attributes: a hardlinked file
  shares attributes with its pristine twin (FR-PA-17). A delete that
  fails (e.g. a read-only file) is logged and left for recovery.
* On Windows every path is passed with the ``\\\\?\\`` long-path prefix.
"""
import logging
import os
import stat
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from shared import owner_only_acl

logger = logging.getLogger(__name__)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_LONG_PREFIX = '\\\\?\\'


class ReparsePointError(OSError):
    """A symlink, junction or other reparse point was found where none may be."""


class SpecialFileError(OSError):
    """An entry is neither a regular file nor a directory."""


@dataclass
class TreeListing:
    """Result of ``walk_tree``: ``/``-separated relpaths."""
    files: Dict[str, os.stat_result] = field(default_factory=dict)
    dirs: List[str] = field(default_factory=list)


def is_reparse(st: os.stat_result) -> bool:
    """True for a symlink, junction or any Windows reparse point (from ``lstat``)."""
    attrs = getattr(st, 'st_file_attributes', 0) or 0
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def file_attrs(st: os.stat_result) -> int:
    """``st_file_attributes`` on Windows, ``0`` elsewhere (FR-PA-7)."""
    return int(getattr(st, 'st_file_attributes', 0) or 0) if sys.platform == 'win32' else 0


def long_path(path) -> str:
    """Absolute path, with the ``\\\\?\\`` prefix on Windows (FR-PA-7)."""
    text = os.fspath(path)
    if sys.platform != 'win32' or text.startswith(_LONG_PREFIX):
        return text
    return _LONG_PREFIX + os.path.abspath(text)


def lstat(path) -> os.stat_result:
    return os.lstat(long_path(path))


def lexists(path) -> bool:
    return os.path.lexists(long_path(path))


def walk_tree(root) -> TreeListing:
    """List ``root`` without following links. Raises on any reparse point or special file."""
    listing = TreeListing()
    pending = ['']
    while pending:
        rel_dir = pending.pop()
        base = os.path.join(os.fspath(root), *rel_dir.split('/')) if rel_dir else os.fspath(root)
        with os.scandir(long_path(base)) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            rel = f'{rel_dir}/{name}' if rel_dir else name
            st = lstat(os.path.join(base, name))
            if is_reparse(st):
                raise ReparsePointError(f"reparse point at {rel}")
            if stat.S_ISDIR(st.st_mode):
                listing.dirs.append(rel)
                pending.append(rel)
            elif stat.S_ISREG(st.st_mode):
                listing.files[rel] = st
            else:
                raise SpecialFileError(f"special file at {rel}")
    listing.dirs.sort()
    return listing


def _unlink(path: str) -> bool:
    """Remove a file or a link (never its target)."""
    target = long_path(path)
    try:
        os.unlink(target)
        return True
    except FileNotFoundError:
        return True
    except (IsADirectoryError, PermissionError) as first:
        try:  # a directory symlink or junction on Windows
            os.rmdir(target)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            logger.warning("Could not delete %s: %s", path, first)
            return False
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)
        return False


def _rmdir(path: str) -> bool:
    try:
        os.rmdir(long_path(path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete directory %s: %s", path, exc)
        return False
    return True


def _remove_children(current: str, stack: list) -> bool:
    """Unlink files and links in ``current``; push real subdirectories."""
    try:
        with os.scandir(long_path(current)) as entries:
            names = [entry.name for entry in entries]
    except OSError as exc:
        logger.warning("Could not list %s for deletion: %s", current, exc)
        return False
    ok = True
    for name in names:
        child = os.path.join(current, name)
        try:
            child_st = lstat(child)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not inspect %s for deletion: %s", child, exc)
            ok = False
            continue
        if is_reparse(child_st) or not stat.S_ISDIR(child_st.st_mode):
            ok = _unlink(child) and ok
        else:
            stack.append((child, False))
    return ok


def remove_tree(path) -> bool:
    """Delete ``path`` without following links. True when it is gone; never raises.

    **Phase-2 blocker (TOCTOU).** Each directory is ``lstat``-ed and then
    listed with ``scandir`` by path; a process running as the same account
    can swap the directory for a junction between the two calls, and the
    listing (and the deletes under it) would then follow the junction.
    Phase 1 accepts this: such a process already has full rights over the
    same files (D6). Phase 2 (separate render account, OQ-10) must delete
    by handle (``FILE_FLAG_OPEN_REPARSE_POINT`` + ``FILE_DISPOSITION_INFO``)
    or ensure the render account cannot write under the tree being deleted.
    """
    path = os.fspath(path)
    try:
        st = lstat(path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("Could not inspect %s for deletion: %s", path, exc)
        return False
    if is_reparse(st) or not stat.S_ISDIR(st.st_mode):
        return _unlink(path)
    ok = True
    stack = [(path, False)]
    while stack:
        current, children_done = stack.pop()
        if children_done:
            ok = _rmdir(current) and ok
            continue
        stack.append((current, True))
        ok = _remove_children(current, stack) and ok
    return ok and not os.path.lexists(long_path(path))


def move_aside(path, trash_dir) -> Optional[Path]:
    """Rename ``path`` into ``trash_dir`` under a unique name; None if it fails."""
    path = Path(path)
    target = Path(trash_dir) / f"{path.name}.{uuid.uuid4().hex[:12]}"
    try:
        os.rename(long_path(path), long_path(target))
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not move %s to trash: %s", path, exc)
        return None
    return target


def check_owned_dir(path) -> None:
    """Raise ``OSError`` unless ``path`` is a real directory owned by the token user."""
    st = lstat(path)
    if is_reparse(st):
        raise ReparsePointError(f"{path} is a symlink or junction")
    if not stat.S_ISDIR(st.st_mode):
        raise SpecialFileError(f"{path} is not a directory")
    if not owner_only_acl.is_owned_by_current_user(path):
        raise PermissionError(f"{path} is not owned by this worker's account")


def check_owned_file(path) -> None:
    """Raise ``OSError`` unless ``path`` is a regular file owned by the token user."""
    st = lstat(path)
    if is_reparse(st) or not stat.S_ISREG(st.st_mode):
        raise SpecialFileError(f"{path} is not a regular file")
    if not owner_only_acl.is_owned_by_current_user(path):
        raise PermissionError(f"{path} is not owned by this worker's account")


def open_output_file(path, output_dir):
    """FR-PA-21 / Sec S4: open the render output for upload, or raise ``OSError``.

    ``output_dir`` must be a real directory owned by the token user; the
    file must be a regular, non-reparse file with exactly one link
    (``st_nlink == 1``: no hard link into the pristine cache or elsewhere)
    whose resolved path lies inside ``output_dir``. The file is then
    opened and ``fstat``-ed, and ``(st_dev, st_ino)`` and the link count
    must match the checked ``lstat``, so a swap between check and open is
    caught. Returns the open binary handle; the caller uploads from it and
    closes it.
    """
    check_owned_dir(output_dir)
    st = lstat(path)
    if is_reparse(st) or not stat.S_ISREG(st.st_mode):
        raise SpecialFileError(f"{path} is not a regular file")
    if st.st_nlink != 1:
        raise SpecialFileError(f"{path} has {st.st_nlink} hard links")
    root = os.path.normcase(os.path.realpath(output_dir))
    resolved = os.path.normcase(os.path.realpath(path))
    if os.path.commonpath([root, resolved]) != root:
        raise ReparsePointError(f"{path} resolves outside {output_dir}")
    handle = open(long_path(path), 'rb')
    try:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino) or opened.st_nlink != 1:
            raise SpecialFileError(f"{path} changed between the check and the open")
    except BaseException:
        handle.close()
        raise
    return handle


def check_output_file(path, output_dir) -> None:
    """``open_output_file`` without keeping the handle."""
    open_output_file(path, output_dir).close()


def ensure_owned_dir(path) -> bool:
    """Create ``path`` owner-only (exclusive), or check the existing one. True if created."""
    if owner_only_acl.create_owner_only_dir(os.fspath(path)):
        return True
    check_owned_dir(path)
    return False
