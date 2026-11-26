# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One fetch of one cached Blender build, run only by the runner thread (BS-MGR-3a, BS-MGR-6, BS-MGR-7).

1. **Claim** (:func:`claim_next`): a due row (QUEUED, or FAILED with its
   retry time come) moves to FETCHING through one conditional ``update()``;
   only a claim that changed one row fetches.
2. **Disk floor** (BS-MGR-7): less than 2 GiB free under the root fails the
   row with no request.
3. **Fetch**: ``find_build`` (release list, cached in
   ``<root>/release_cache.json``) and ``download_build`` into ``<root>/.part/``
   with the runner's stop event, then ``os.replace`` into
   ``<root>/<version>/``. A regular file already there with the entry's
   SHA-256 is kept without a download. No session is passed: ``download_build`` makes a
   plain one, so no credentials can follow a redirect (BS-DL-7).
4. **Result**: READY with ``sha256``, ``size`` and ``source``; or FAILED
   with the error text (500 characters at most) and ``next_try_at`` = now +
   30 min. Both are conditional on the row still being FETCHING. When the
   success write changes nothing (the row was deleted or changed meanwhile),
   the placed file is deleted again.

No transaction is open during the download; every write is one autocommit
``update()``. Every exception of one fetch is caught here and fails the row.
"""

import hashlib
import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Optional

from django.utils import timezone

from shared.blender_sources import BlenderSourcesError, DownloadStoppedError, download_build, find_build

from ...constants import BlenderBuildStatus
from ...models import BlenderBuild
from ...models.blender_builds import ERROR_MAX_LENGTH, SOURCE_MAX_LENGTH
from . import paths, wanted

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=30)
MIN_FREE_BYTES = 2 * 1024 ** 3
GIB = 1024 ** 3
HASH_CHUNK_BYTES = 1024 * 1024


def claim_next() -> Optional[BlenderBuild]:
    """Claim the oldest due row (QUEUED or FAILED-due -> FETCHING); None when nothing was claimed."""
    now = timezone.now()
    for row in BlenderBuild.objects.filter(wanted.due_q(now)).order_by('updated_at', 'pk')[:20]:
        rows = BlenderBuild.objects.filter(pk=row.pk, status=row.status)
        if row.status == BlenderBuildStatus.FAILED:
            rows = rows.filter(wanted.due_q(now))
        if rows.update(status=BlenderBuildStatus.FETCHING, error='', updated_at=now) == 1:
            row.status = BlenderBuildStatus.FETCHING
            return row
    return None


def _fail(row: BlenderBuild, message: str, log: bool = True) -> None:
    now = timezone.now()
    changed = BlenderBuild.objects.filter(pk=row.pk, status=BlenderBuildStatus.FETCHING).update(
        status=BlenderBuildStatus.FAILED, error=message[:ERROR_MAX_LENGTH],
        next_try_at=now + RETRY_AFTER, updated_at=now)
    if changed:
        if log:
            logger.error("Blender cache: fetching %s failed: %s", row.file_name, message)
    else:
        logger.info("Blender cache: %s changed during its fetch; failure not recorded.", row.file_name)


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # the sweep retries
        logger.warning("Blender cache: could not remove %s: %s", path, exc)


def _free_bytes_error(root: Path) -> Optional[str]:
    """The BS-MGR-7 message when less than 2 GiB is free under ``root``, else None."""
    free = shutil.disk_usage(root).free
    if free >= MIN_FREE_BYTES:
        return None
    return f"not enough disk space: {free / GIB:.1f} GiB free, {MIN_FREE_BYTES // GIB} GiB needed"


def _matches(path: Path, sha256: str, stop) -> bool:
    """True when ``path`` is a regular file (a link does not count) whose SHA-256 is ``sha256``."""
    if paths.regular_size(path) is None:
        return False
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b''):
                if stop is not None and stop.is_set():
                    raise DownloadStoppedError("stopped while checking the placed build")
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == sha256.lower()


def _place(row: BlenderBuild, root: Path, stop):
    """Look up, download and move the build into ``<root>/<version>/``; returns (final path, entry).

    A file already in place with the entry's SHA-256 (a crash between
    ``os.replace`` and the success write, or a replace onto a file being
    streamed on Windows) is kept as it is: no download, no replace.
    """
    entry = find_build(row.version, row.platform, paths.release_cache_file(root))
    if entry.file != row.file_name:
        raise ValueError(f"the release list names {entry.file!r}, expected {row.file_name}")
    (root / row.version).mkdir(parents=True, exist_ok=True)
    final = paths.build_path(root, row.version, row.file_name)
    if final is None:
        raise ValueError(f"refusing to place {row.file_name} outside {root}")
    if _matches(final, entry.sha256, stop):
        logger.info("Blender cache: %s is already in place; not downloaded again.", row.file_name)
        return final, entry
    downloaded = download_build(entry, paths.part_dir(root), stop=stop)
    os.replace(downloaded, final)
    return final, entry


def _succeed(row: BlenderBuild, final: Path, entry) -> None:
    size = final.stat().st_size
    now = timezone.now()
    changed = BlenderBuild.objects.filter(pk=row.pk, status=BlenderBuildStatus.FETCHING).update(
        status=BlenderBuildStatus.READY, sha256=entry.sha256.lower(), size=size,
        source=(entry.source or '')[:SOURCE_MAX_LENGTH], error='', next_try_at=None, updated_at=now)
    if changed:
        logger.info("Blender cache: %s is ready (%d bytes).", row.file_name, size)
        return
    _remove(final)
    logger.info("Blender cache: %s is no longer wanted or changed during its fetch; file removed.",
                row.file_name)


def fetch(row: BlenderBuild, root: Path, stop) -> None:
    """Fetch one claimed (FETCHING) row; never raises."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        problem = _free_bytes_error(root)
        if problem is not None:
            _fail(row, problem)
            return
        final, entry = _place(row, root, stop)
        _succeed(row, final, entry)
    except DownloadStoppedError:
        if wanted.requeue(row.pk, BlenderBuildStatus.FETCHING):
            logger.info("Blender cache: fetching %s stopped; queued again.", row.file_name)
    except BlenderSourcesError as exc:  # its text names every source and reason (BS-LOG-2)
        _fail_quietly(row, str(exc), log=True)
    except Exception as exc:  # noqa: BLE001 -- one failed fetch must not end the runner
        logger.exception("Blender cache: fetching %s raised.", row.file_name)
        _fail_quietly(row, f"{type(exc).__name__}: {exc}", log=False)


def _fail_quietly(row: BlenderBuild, message: str, log: bool) -> None:
    try:
        _fail(row, message, log=log)
    except Exception:  # noqa: BLE001 -- the start step re-queues a row left FETCHING
        logger.exception("Blender cache: recording the failure of %s failed.", row.file_name)
