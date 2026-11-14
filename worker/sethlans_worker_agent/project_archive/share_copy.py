# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Copy of the pinned archive from the share (Unreal foundation FR-PA-2,
FR-PA-4, FR-PA-5).

``copy_archive(unc, dest_dir, pin, ...)``:

* **Read-only.** The share file is opened only with ``'rb'`` (and
  ``os.stat``); nothing writes, renames, deletes or touches a share path.
* **Pre-copy.** ``os.stat(follow_symlinks=False)`` first: a reparse point
  or non-regular file is refused, ``st_size > MAX_ARCHIVE_GB`` raises
  ``ArchiveTooLargeError`` before any byte is read, then the stage-A
  budget (``reserve(st_size)``) is taken.
* **Stream.** A daemon **copy thread** (``stream_worker``) streams 8 MiB
  chunks into ``downloads/<uuid>.zip.part`` while hashing; each written
  chunk is consumed from the reservation (Conc R2-L4). More bytes than the
  pre-copy ``st_size`` abort with "archive grew during copy".
* **Waiting.** Cancel, shutdown, the deadline and a stall of
  ``COPY_STALL_SECONDS`` (``ShareStallError``) abandon the copy at once;
  the copy thread deletes its ``.part`` when its blocked read returns.
* **Completion.** The source is re-stat'ed: a size or ``st_mtime_ns``
  change raises ``ArchiveChangedError``; the local size must equal the
  source size; the SHA-256 must equal the pin (``ArchivePinMismatchError``).
  On any error the ``.part`` is deleted.

Tests copy from a local path through the keyword-only ``share_validator``;
production callers pass none (the default re-validates the UNC path).
"""
import hashlib
import logging
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sethlans_worker_agent.project_archive import fs_safe, stream_worker
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.entry_record import SourceInfo
from sethlans_worker_agent.project_archive.errors import (
    ArchiveChangedError, ArchiveError, ArchivePinMismatchError, ArchiveTooLargeError, ShareUnavailableError,
)
from shared.unreal.unc import validate_unc_path

logger = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024


@dataclass(frozen=True)
class CopiedArchive:
    path: Path
    sha256: str
    size: int
    source: SourceInfo


def default_share_validator(path) -> str:
    """Production validator: the resolved path must pass FR-MU-6 again."""
    return validate_unc_path(str(path)).canonical


def _open_rb(path):
    return open(path, 'rb')


def _remove(path: Path) -> None:
    try:
        os.unlink(fs_safe.long_path(path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete partial archive copy %s: %s", path, exc)


def _read(src, chunk: int) -> bytes:
    try:
        return src.read(chunk)
    except OSError as exc:
        raise ShareUnavailableError(f"reading the archive from the share failed: {exc}") from exc


def _copy_target(source: str, part: Path, size: int, chunk: int, opener, reservation):
    """The copy thread's work: returns the SHA-256 of the copied bytes."""
    def target(job: stream_worker.StreamJob) -> str:
        digest, total = hashlib.sha256(), 0
        try:
            src = opener(source)
        except OSError as exc:
            raise ShareUnavailableError(f"opening the archive on the share failed: {exc}") from exc
        try:
            with src, open(fs_safe.long_path(part), 'xb') as out:
                while not job.is_abandoned():
                    data = _read(src, chunk)
                    if job.is_abandoned() or not data:
                        break
                    if total + len(data) > size:
                        raise ArchiveChangedError("archive grew during copy")
                    out.write(data)
                    digest.update(data)
                    total += len(data)
                    reservation.consume(len(data))
                    job.progress(total)
        except OSError as exc:  # share-side errors are already ShareUnavailableError
            raise ArchiveError(f"writing the local archive copy failed: {exc}") from exc
        return digest.hexdigest()
    return target


def _stat_source(path: str) -> os.stat_result:
    try:
        return os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ShareUnavailableError(f"the archive on the share cannot be read: {exc}") from exc


def _pre_copy(path: str, max_bytes: int) -> os.stat_result:
    st = _stat_source(path)
    if fs_safe.is_reparse(st):
        raise ArchiveError("The archive path on the share is a symlink or junction.")
    if not stat.S_ISREG(st.st_mode):
        raise ArchiveError("The archive path on the share is not a regular file.")
    if st.st_size > max_bytes:
        raise ArchiveTooLargeError(
            f"Archive is {st.st_size} bytes, above the {max_bytes}-byte limit (SETHLANS_UNREAL_MAX_ARCHIVE_GB).")
    return st


def _finish(part: Path, sha256: str, path: str, before: os.stat_result, pin: str) -> None:
    after = _stat_source(path)
    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise ArchiveChangedError("archive changed during copy")
    local = fs_safe.lstat(part).st_size
    if local != before.st_size:
        raise ArchiveChangedError(f"archive copy has {local} bytes, the share file {before.st_size}")
    if sha256 != pin:
        raise ArchivePinMismatchError(pin, sha256)


def copy_archive(unc, dest_dir, pin: str, abort: Abort = NEVER, *, max_bytes: int,
                 stall_seconds: float, reserve: Callable[[int], object],
                 share_validator: Callable = default_share_validator,
                 opener: Callable = _open_rb, clock: Callable[[], float] = time.monotonic,
                 poll_seconds: float = 1.0, chunk: int = CHUNK) -> CopiedArchive:
    """Copy and pin-check the share archive ``unc`` into ``dest_dir``; see the module docstring."""
    try:
        path = share_validator(unc)
    except ValueError as exc:
        raise ArchiveError(f"Invalid archive path: {exc}") from exc
    before = _pre_copy(path, max_bytes)
    reservation = reserve(before.st_size)
    part = Path(dest_dir) / f"{uuid.uuid4().hex}.zip.part"
    try:
        # The worker (or the abandon) removes the part on every failure inside run().
        sha256 = stream_worker.run(
            _copy_target(path, part, before.st_size, chunk, opener, reservation), what='copying the archive',
            abort=abort, stall_seconds=stall_seconds, cleanup=lambda: _remove(part),
            poll_seconds=poll_seconds, clock=clock)
        try:
            _finish(part, sha256, path, before, pin)
        except BaseException:
            _remove(part)
            raise
    finally:
        reservation.release()
    return CopiedArchive(path=part, sha256=sha256, size=before.st_size,
                         source=SourceInfo(unc=str(unc), size=before.st_size, mtime_ns=before.st_mtime_ns))
