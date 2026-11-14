# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Read-only, link-refusing access to the artist's project directory
(Unreal foundation FR-PK-5, FR-PK-10).

Every path is given as the resolved project root plus a ``/``-separated
relpath, and containment is checked on the object that is actually read:

* **Listing** (``opened_dir``). On Windows the directory is opened as a
  handle (``source_win.open_dir``) that must not be a reparse point and
  whose final path must be the expected one; while it is held the
  directory and its ancestors cannot be renamed, so listing it and
  ``lstat``-ing its children by path cannot be redirected. On POSIX the
  directory is reached by a descriptor walk with ``O_NOFOLLOW``
  (``source_posix``) and listed and ``lstat``-ed through ``dir_fd``.
* **Reading** (``stream_file``) opens first, then requires the open file
  to be inside the project (Windows: its final path; POSIX: the
  ``O_NOFOLLOW`` walk) and ``os.fstat`` to match the scan's ``lstat``
  (``st_ino``, ``st_dev``, ``st_size``, ``st_mtime_ns``) with no reparse
  attribute. After the read the byte count and ``fstat`` are checked again.
  Any difference raises ``SourceChangedError``.
* ``lstat_checked`` refuses symlinks, junctions, cloud-file placeholders,
  any other reparse point, offline or recall-on-access files and special
  files.

Nothing here writes, renames or deletes under the project directory.
Files are opened with full sharing on Windows, so an editor that is saving
is not blocked (the change is detected instead).
"""
import errno
import os
import stat
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional

from .errors import PackagerError, SourceChangedError

if os.name == 'nt':  # pragma: no cover - platform split
    from . import source_win as _win
    _posix = None
else:  # pragma: no cover - platform split
    from . import source_posix as _posix
    _win = None

CHUNK = 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_UNSAFE_ATTRS = (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_OFFLINE
                 | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
_LINK_ERRNOS = (errno.ELOOP, errno.ENOTDIR)
OUTSIDE = "it is not inside the project directory"


def fs_path(path: str) -> str:
    """``path`` with the Windows ``\\\\?\\`` long-path prefix (absolute paths only)."""
    if os.name != 'nt' or path.startswith('\\\\?\\') or not os.path.isabs(path):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + path


def join(root_fs: str, relpath: str) -> str:
    return os.path.join(root_fs, *relpath.split('/')) if relpath else root_fs


def _parts(relpath: str) -> List[str]:
    return relpath.split('/') if relpath else []


def _unsafe_attrs(st) -> int:
    return getattr(st, 'st_file_attributes', 0) & _UNSAFE_ATTRS


def _lstat(path: str, dir_fd: Optional[int] = None) -> os.stat_result:
    return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)


def lstat_checked(path: str, relpath: str, dir_fd: Optional[int] = None) -> os.stat_result:
    """``lstat`` of a regular file or directory that is not a link or placeholder."""
    st = _lstat(path, dir_fd)
    if stat.S_ISLNK(st.st_mode):
        raise PackagerError(f"{relpath!r} is a symbolic link; links are never packaged", (relpath,))
    if _unsafe_attrs(st):
        raise PackagerError(
            f"{relpath!r} is a junction, reparse point or cloud placeholder; links are never packaged. "
            "If the project is in a OneDrive or other synced folder, move it out of that folder "
            "(files kept on the device are still cloud placeholders)", (relpath,))
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise PackagerError(f"{relpath!r} is not a regular file or directory", (relpath,))
    return st


def identity(st: os.stat_result) -> tuple:
    return st.st_ino, st.st_dev, st.st_size, st.st_mtime_ns


class DirView:
    """A verified, open directory: its child names and no-follow ``lstat`` of them."""

    def __init__(self, path: str, names: List[str], dir_fd: Optional[int]):
        self._path, self.names, self._dir_fd = path, names, dir_fd

    def _child(self, name: str) -> str:
        return name if self._dir_fd is not None else os.path.join(self._path, name)

    def lstat(self, name: str, relpath: str) -> os.stat_result:
        if self._dir_fd is None:
            return lstat_checked(self._child(name), relpath)
        return lstat_checked(self._child(name), relpath, dir_fd=self._dir_fd)

    def raw_lstat(self, name: str) -> os.stat_result:
        return _lstat(self._child(name), self._dir_fd)


def _replaced(path: str, label: str, exc: OSError, want_dir: bool,
              expected: Optional[tuple] = None) -> PackagerError:
    """Classify a failed open: a change (gone, a link on the way, a different type) or a read error."""
    if getattr(exc, 'errno', None) in _LINK_ERRNOS and os.name != 'nt':
        return SourceChangedError(label, "a link was found on its path")
    try:
        now = _lstat(path)
    except OSError:
        return SourceChangedError(label, "it disappeared")
    kind_ok = stat.S_ISDIR(now.st_mode) if want_dir else stat.S_ISREG(now.st_mode)
    changed = expected is not None and identity(now) != expected
    if stat.S_ISLNK(now.st_mode) or _unsafe_attrs(now) or not kind_ok or changed:
        return SourceChangedError(label, "it was replaced")
    return PackagerError(f"cannot read {label!r}: {exc.strerror or exc}", (label,))


def _check_win_dir(handle: int, path: str, label: str) -> None:
    try:
        attrs, final = _win.attributes(handle), _win.final_path(handle)
    except OSError as exc:
        raise PackagerError(f"cannot verify where {label!r} is: {exc.strerror or exc}", (label,)) from exc
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT or not attrs & _win.FILE_ATTRIBUTE_DIRECTORY:
        raise SourceChangedError(label, "it was replaced by a link or a file")
    if not _win.same_path(final, path):
        raise SourceChangedError(label, OUTSIDE)


@contextmanager
def opened_dir(root_fs: str, relpath: str) -> Iterator[DirView]:
    """List ``root_fs/relpath`` after verifying it is the real directory inside the project."""
    label, path = relpath or '.', join(root_fs, relpath)
    if os.name == 'nt':
        try:
            handle = _win.open_dir(path)
        except OSError as exc:
            raise _replaced(path, label, exc, want_dir=True) from exc
        try:
            _check_win_dir(handle, path, label)
            with os.scandir(path) as entries:
                names = sorted(entry.name for entry in entries)
            yield DirView(path, names, None)
        finally:
            _win.close(handle)
        return
    try:
        fd = _posix.open_dir(root_fs, _parts(relpath))
    except OSError as exc:
        raise _replaced(path, label, exc, want_dir=True) from exc
    try:
        yield DirView(path, sorted(os.listdir(fd)), fd)
    finally:
        os.close(fd)


def _open(root_fs: str, relpath: str) -> int:
    if os.name == 'nt':
        return _win.open_file(join(root_fs, relpath))
    return _posix.open_file(root_fs, _parts(relpath))


def _check_opened(fd: int, root_fs: str, relpath: str, expected: tuple) -> None:
    if os.name == 'nt':
        try:
            final = _win.fd_final_path(fd)
        except OSError as exc:
            raise PackagerError(f"cannot verify where {relpath!r} is: {exc.strerror or exc}", (relpath,)) from exc
        if not _win.same_path(final, join(root_fs, relpath)):
            raise SourceChangedError(relpath, OUTSIDE)
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or _unsafe_attrs(opened):
        raise SourceChangedError(relpath, "it is no longer a regular file")
    if identity(opened) != expected:
        raise SourceChangedError(relpath, "it is not the file that was scanned")


def stream_file(root_fs: str, relpath: str, expected: tuple, on_chunk: Callable[[bytes], None]) -> int:
    """Feed the verified file ``root_fs/relpath`` to ``on_chunk``; return its size.

    Args:
        expected: ``identity()`` of the scan's ``lstat``.

    Raises:
        SourceChangedError: the file is outside the project, was swapped
            before the open, or changed size or modification time during
            the read.
        PackagerError: it cannot be opened although it is unchanged.
    """
    try:
        fd = _open(root_fs, relpath)
    except OSError as exc:
        raise _replaced(join(root_fs, relpath), relpath, exc, want_dir=False, expected=expected) from exc
    count = 0
    try:
        _check_opened(fd, root_fs, relpath, expected)
        while chunk := os.read(fd, CHUNK):
            count += len(chunk)
            if count > expected[2]:
                raise SourceChangedError(relpath, "it grew while it was read")
            on_chunk(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if count != expected[2] or (after.st_size, after.st_mtime_ns) != (expected[2], expected[3]):
        raise SourceChangedError(relpath, "its size or modification time changed while it was read")
    return count
