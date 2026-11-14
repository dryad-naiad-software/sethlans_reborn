# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Overlay staging and its pin (Unreal foundation FR-PA-2, FR-PA-12, D3).

``stage_overlay(source, dest_dir, pin, abort, ...)``:

* walks the share directory with ``os.scandir`` without following links;
  a reparse point or non-regular file is refused, and so is a relpath that
  breaks FR-PA-6 rules 1-2; at most 10,000 files;
* reserves the stat'ed byte total (FR-PA-11), then copies each file
  (read-only ``'rb'`` on the share) into ``o/<job_id>-<att8>/``, counting
  the bytes **as they are copied** against the 4 GiB cap, so a file that
  grows is caught;
* computes the digest over the **staged** copy with the same function the
  admin used (``shared.unreal.overlay_digest.compute_overlay_digest``) and
  fails with the FR-PA-12 message unless it equals ``overlay_sha256``.

The walk and the copy both run on abandonable daemon workers
(``stream_worker``, Conc C2), so a blocked SMB call never holds the job
thread: cancel, shutdown, the deadline and a stall of
``COPY_STALL_SECONDS`` (``ShareStallError``) return at once.

An ``OSError`` on the share side is environmental
(``ShareUnavailableError``); everything else is a content failure. An
overlay only adds or replaces files relative to the project folder; it
never deletes (that is ``working_copy``'s rule).
"""
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from sethlans_worker_agent.project_archive import fs_safe, stream_worker, zip_rules
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.errors import (
    OverlayPinMismatchError, ShareUnavailableError, UnsafeOverlayError,
)
from shared.unreal.unc import validate_unc_path

MAX_FILES = 10_000
MAX_BYTES = 4 * 1024 ** 3
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StagedOverlay:
    path: Path
    digest: str
    files: Tuple[str, ...]  # '/'-separated relpaths, relative to the project folder
    total_bytes: int


def default_share_validator(path) -> str:
    return validate_unc_path(str(path)).canonical


def default_digest(root: Path) -> str:
    from shared.unreal.overlay_digest import compute_overlay_digest  # chunk 2 (FR-MU-5)
    return compute_overlay_digest(Path(root)).digest


def _share_lstat(path: str) -> os.stat_result:
    try:
        return os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ShareUnavailableError(f"the overlay on the share cannot be read: {exc}") from exc


def _visit(base: str, rel: str, found: Dict[str, int], pending: List[str], max_files: int) -> None:
    try:
        zip_rules.normalize_relpath(rel)
    except ValueError as exc:
        raise UnsafeOverlayError(f"Overlay file name is not allowed ({exc}).") from exc
    st = _share_lstat(os.path.join(base, rel.rsplit('/', 1)[-1]))
    if fs_safe.is_reparse(st):
        raise UnsafeOverlayError(f"Overlay contains a symlink or junction ({rel}).")
    if stat.S_ISDIR(st.st_mode):
        pending.append(rel)
    elif stat.S_ISREG(st.st_mode):
        found[rel] = st.st_size
        if len(found) > max_files:
            raise UnsafeOverlayError(f"Overlay has more than {max_files} files.")
    else:
        raise UnsafeOverlayError(f"Overlay contains a special file ({rel}).")


def _walk_target(root: str, max_files: int):
    """Worker: check the root and list ``relpath -> size``; refuses links and special files."""
    def target(job: stream_worker.StreamJob) -> Dict[str, int]:
        root_st = _share_lstat(root)
        if fs_safe.is_reparse(root_st) or not stat.S_ISDIR(root_st.st_mode):
            raise UnsafeOverlayError("The overlay path on the share is not a plain directory.")
        found: Dict[str, int] = {}
        pending = ['']
        while pending and not job.is_abandoned():
            rel_dir = pending.pop()
            base = os.path.join(root, *rel_dir.split('/')) if rel_dir else root
            try:
                with os.scandir(base) as entries:
                    names = sorted(entry.name for entry in entries)
            except OSError as exc:
                raise ShareUnavailableError(f"the overlay on the share cannot be listed: {exc}") from exc
            for name in names:
                _visit(base, f'{rel_dir}/{name}' if rel_dir else name, found, pending, max_files)
                job.progress(len(found))
        return found
    return target


def _read(handle) -> bytes:
    try:
        return handle.read(CHUNK)
    except OSError as exc:
        raise ShareUnavailableError(f"reading the overlay from the share failed: {exc}") from exc


def _copy_one(job, src: str, dst: Path, copied: int, max_bytes: int, on_bytes) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = open(src, 'rb')
    except OSError as exc:
        raise ShareUnavailableError(f"reading the overlay from the share failed: {exc}") from exc
    with handle, open(fs_safe.long_path(dst), 'xb') as out:
        while not job.is_abandoned():
            data = _read(handle)
            if not data or job.is_abandoned():
                break
            copied += len(data)
            if copied > max_bytes:
                raise UnsafeOverlayError(f"Overlay is larger than {max_bytes} bytes.")
            out.write(data)
            on_bytes(len(data))
            job.progress(copied)
    return copied


def _copy_target(root: str, dest: Path, files, max_bytes: int, reservation):
    """Worker: copy every overlay file, counting bytes as they are copied (4 GiB cap)."""
    def target(job: stream_worker.StreamJob) -> int:
        copied = 0
        for rel in sorted(files):
            if job.is_abandoned():
                break
            copied = _copy_one(job, os.path.join(root, *rel.split('/')), dest.joinpath(*rel.split('/')),
                               copied, max_bytes, reservation.consume)
        return copied
    return target


def stage_overlay(source, dest_dir, pin: str, abort: Abort = NEVER, *, reserve: Callable[[int], object],
                  share_validator: Callable = default_share_validator,
                  digest_fn: Callable[[Path], str] = default_digest,
                  max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES,
                  stall_seconds: float = 120.0, poll_seconds: float = 1.0) -> StagedOverlay:
    """Copy the share overlay into ``dest_dir`` (must not exist) and check its pin.

    The share walk and the copy run on abandonable daemon workers
    (``stream_worker``, Conc C2); an abandoned copy removes ``dest_dir``
    itself (it is per claim attempt, ``o/<job_id>-<att8>``).
    """
    try:
        root = share_validator(source)
    except ValueError as exc:
        raise UnsafeOverlayError(f"Invalid overlay path: {exc}") from exc
    files = stream_worker.run(_walk_target(root, max_files), what='listing the overlay', abort=abort,
                              stall_seconds=stall_seconds, cleanup=lambda: None, poll_seconds=poll_seconds)
    declared = sum(files.values())
    if declared > max_bytes:
        raise UnsafeOverlayError(f"Overlay is larger than {max_bytes} bytes.")
    dest = Path(dest_dir)
    dest.mkdir()
    with reserve(declared) as reservation:
        copied = stream_worker.run(_copy_target(root, dest, files, max_bytes, reservation),
                                   what='copying the overlay', abort=abort, stall_seconds=stall_seconds,
                                   cleanup=lambda: fs_safe.remove_tree(dest), poll_seconds=poll_seconds)
    digest = digest_fn(dest)
    if digest != pin:
        raise OverlayPinMismatchError(pin, digest)
    return StagedOverlay(path=dest, digest=digest, files=tuple(sorted(files)), total_bytes=copied)
