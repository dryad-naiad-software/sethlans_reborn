# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Owner-only folders and whole-file atomic replace for ``sethlans.ini``.

Write sequence: a new temp file in the target's own folder, owner-only
from birth (``shared.owner_only_acl.create_owner_only_file``: on Windows
``CreateFileW`` with the owner-only descriptor and no sharing, so no other
handle can exist while the secrets are written; on POSIX ``O_EXCL`` with
mode 0600), every byte written, ``fsync``, closed, checked with
``verify_owner_only``; only then ``os.replace`` over the target, followed
by an ``fsync`` of the folder on POSIX. A failed check, write or replace
removes the temp file and leaves the old target untouched.

Reads (:func:`read_limited`) retry briefly on Windows while a writer's
``os.replace`` holds the file, as writes retry while a reader holds it.
:func:`read_owned_file` also refuses links, FIFOs and files of other users.

The temp file is the only copy of the new content written anywhere other
than the target itself. Callers hold the settings lock
(``shared.file_lock``) around the read-modify-write.
"""
import errno
import logging
import os
import secrets
import stat
import threading
import time
from pathlib import Path

from shared import owner_only_acl

logger = logging.getLogger(__name__)

# Folders already warned about as not owner-only, so the warning is logged
# once per folder per process rather than on every write.
_warned_loose_dirs: set = set()
_warned_lock = threading.Lock()  # leaf lock; guards _warned_loose_dirs

_IS_WINDOWS = os.name == 'nt'
_REPLACE_ATTEMPTS = 20
_REPLACE_RETRY_SECONDS = 0.05


class SettingsPermissionError(PermissionError):
    """A settings file or folder could not be made owner-only."""


def ensure_owner_only_dir(folder) -> Path:
    """Create ``folder`` owner-only from birth when it is missing.

    Missing parents are created normally. An existing folder is never
    taken over: it must be owned by this process's user (else
    ``SettingsPermissionError``), and a warning is logged (once per
    folder per process) when it is not owner-only. Returns the folder as
    a ``Path``.
    """
    folder = Path(folder)
    if folder.is_dir():
        if not owner_only_acl.is_owned_by_current_user(folder):
            raise SettingsPermissionError(f"Settings folder {folder} is not owned by the current user")
        if not owner_only_acl.verify_owner_only(folder):
            _warn_loose_dir_once(folder)
        return folder
    folder.parent.mkdir(parents=True, exist_ok=True)
    owner_only_acl.create_owner_only_dir(folder)  # False: another process won the race
    if not folder.is_dir():
        raise NotADirectoryError(f"Settings folder is not a directory: {folder}")
    return folder


def _warn_loose_dir_once(folder: Path) -> None:
    key = os.path.normcase(os.path.abspath(folder))
    with _warned_lock:
        if key in _warned_loose_dirs:
            return
        _warned_loose_dirs.add(key)
    logger.warning("Settings folder %s is not owner-only; other accounts may list it", folder)


def make_owner_only(path) -> None:
    """Restrict ``path`` to its owner and check it; fail closed."""
    owner_only_acl.apply_owner_only(path)
    if not owner_only_acl.verify_owner_only(path):
        raise SettingsPermissionError(f"Could not make {path} owner-only")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _fsync_dir(folder: Path) -> None:
    if os.name == 'nt':
        return  # NTFS makes the rename durable with the file; no directory handle needed
    dir_fd = os.open(os.fspath(folder), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _discard(temp: Path) -> None:
    try:
        os.unlink(temp)
    except OSError:
        pass


def replace_with_retry(temp, path) -> None:
    """``os.replace``, retried briefly on Windows while a reader has *path* open.

    Python opens files without ``FILE_SHARE_DELETE``, so on Windows a
    replace fails with ``PermissionError`` while any process is reading
    the target. Reads are one short ``read`` call, so a few retries clear
    it; anything longer is raised. Also used by the worker's ``config_store``.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if not _IS_WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)


def read_limited(path, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes of ``path``.

    On Windows a ``PermissionError`` while a writer replaces the file is
    retried like :func:`replace_with_retry`; a folder at ``path`` fails at once
    (``IsADirectoryError``). ``FileNotFoundError`` is raised to the caller.
    """
    path = Path(path)
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            with open(path, "rb") as handle:
                return handle.read(limit + 1)
        except PermissionError:
            if path.is_dir():
                raise IsADirectoryError(f"Is a directory: {path}") from None
            if not _IS_WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


# A link is never followed (POSIX O_NOFOLLOW; Windows has no such flag, so
# the lstat check below covers it) and a FIFO never blocks the open.
_OWNED_READ_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_BINARY", 0))


def _open_no_follow(path: Path) -> int:
    if stat.S_ISLNK(os.lstat(path).st_mode):
        raise OSError(errno.ELOOP, "Is a symbolic link", str(path))
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            return os.open(path, _OWNED_READ_FLAGS)
        except PermissionError:
            if path.is_dir():
                raise IsADirectoryError(f"Is a directory: {path}") from None
            if not _IS_WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


def read_owned_file(path, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes of a regular file this user owns.

    For small files other programs must not be able to plant (the tray's
    ``setup/launcher_state.json`` and ``setup/setup_token``): a symbolic
    link is refused, not followed; the open does not block on a FIFO; the
    opened file must be a regular file (``fstat``) owned by this user
    (``owner_only_acl.is_owned_by_current_user``). Any of these failing
    raises ``OSError``; ``FileNotFoundError`` is raised as it is. Windows
    retries a writer's replace like :func:`read_limited`.
    """
    path = Path(path)
    fd = _open_no_follow(path)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        if not owner_only_acl.is_owned_by_current_user(path):
            raise OSError(f"{path.name} is not owned by the current user")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def atomic_replace(path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, owner-only (see module doc).

    The folder must exist. Raises ``OSError`` (``SettingsPermissionError``
    when the owner-only check fails); the old file is then unchanged.
    """
    path = Path(path)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = owner_only_acl.create_owner_only_file(temp)
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        make_owner_only(temp)
        replace_with_retry(temp, path)
    except BaseException:
        _discard(temp)
        raise
    _fsync_dir(path.parent)
