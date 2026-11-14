# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Atomic publish of an extracted entry (Unreal foundation FR-PA-8; review
fixes Sec S1, Conc C1, C3, C9).

Extraction targets ``p/<key16>.tmp-<uuid>/tree/`` and writes
``.complete.json`` there. Under the index lock:

* a key that is still being disposed (``cache.removing``) is refused with
  ``CacheKeyCollisionError``;
* an existing entry **in use** (refcount > 0) is reused only if it is the
  same archive, ``ready``, its on-disk record still has the digest the
  cache recorded, and that record's ``(relpath, size, sha256, attrs)``
  tuples equal the fresh extraction's (checked, with a stat walk, before
  the lock is taken). Anything else in use is a ``CacheKeyCollisionError``
  and the directory is never reused;
* otherwise (no entry, or refcount 0) the existing ``p/<key16>/`` is
  **always replaced** by the fresh extraction: it is renamed to
  ``trash/`` (claiming a pending move for the key, if any), logged when it
  belonged to another archive, and the tmp dir is ``os.replace``'d into
  place. An unindexed directory is never trusted, whatever its
  ``.complete.json`` says (a same-user forger writes both).

The entry is registered with the digest of the **fresh** record. Trashed
directories are deleted after the lock is released; a delete failure can
never leak the caller's refcount.
"""
import logging
import os
from typing import List, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.project_archive.cache import READY, ArchiveCache
from sethlans_worker_agent.project_archive.entry_record import (
    CompleteRecord, RecordError, content_tuples, read_record, record_digest, stat_mismatches,
)
from sethlans_worker_agent.project_archive.errors import ArchiveError, CacheKeyCollisionError
from sethlans_worker_agent.project_archive.layout import key16

logger = logging.getLogger(__name__)


def _existing_matches(entry_dir, record: CompleteRecord) -> Optional[str]:
    """Digest of the on-disk record when it describes exactly the fresh extraction, else None."""
    try:
        fs_safe.check_owned_dir(entry_dir)
        disk = read_record(entry_dir)
    except (OSError, RecordError):
        return None
    if disk.sha256 != record.sha256 or content_tuples(disk) != content_tuples(record):
        return None
    if stat_mismatches(entry_dir, disk):
        return None
    return record_digest(disk)


def _move_to_trash_locked(cache: ArchiveCache, path):
    moved = fs_safe.move_aside(path, cache.layout.trash)
    if moved is None:
        raise ArchiveError(f"Cannot replace Unreal cache entry {os.path.basename(os.fspath(path))}.")
    return moved


def publish(cache: ArchiveCache, tmp_dir, record: CompleteRecord) -> bool:
    """Publish ``tmp_dir`` as the entry for ``record.sha256``, acquired once.

    Returns True when ``tmp_dir`` became the entry, False when the identical
    entry already in use was shared (``tmp_dir`` was discarded).
    """
    final = cache.entry_dir(record.sha256)
    disk_digest = _existing_matches(final, record) if fs_safe.lexists(final) else None
    to_delete: List[Tuple[object, int]] = []
    try:
        with cache.lock:
            return _publish_locked(cache, tmp_dir, record, final, disk_digest, to_delete)
    finally:
        for path, size in to_delete:
            try:
                cache.delete_trash(path, size)
            except Exception:  # noqa: BLE001 - Conc C3: the refcount is already taken
                logger.exception("Could not delete %s after publish.", path)


def _publish_locked(cache, tmp_dir, record, final, disk_digest, to_delete) -> bool:
    sha, key = record.sha256, key16(record.sha256)
    if key in cache.removing:
        raise CacheKeyCollisionError(key)
    holder = cache.entry_for_key_locked(key)
    if holder is not None and holder.refcount > 0:
        if (holder.sha256 == sha and holder.state == READY and disk_digest is not None
                and disk_digest == holder.record_digest):
            to_delete.append((_move_to_trash_locked(cache, tmp_dir), 0))
            holder.refcount += 1
            return False
        raise CacheKeyCollisionError(key)
    size = 0
    if holder is not None:
        if holder.sha256 != sha:
            logger.error("cache key collision or invalid entry at p/%s; replaced", key)
        _path, size, _key = cache.begin_eviction_locked([holder])[0]
        cache.removing.discard(key)  # moved aside right here, under the lock
    size += cache.trash.take_move_locked(key)
    if fs_safe.lexists(final):
        try:
            to_delete.append((_move_to_trash_locked(cache, final), size))
        except ArchiveError:
            cache.trash.queue_move_locked(final, size, key)  # its bytes stay accounted
            raise
    else:
        cache.ledger.pending_trash = max(cache.ledger.pending_trash - size, 0)
    os.replace(fs_safe.long_path(tmp_dir), fs_safe.long_path(final))
    cache.register_locked(sha, record.total_bytes, record.source.unc, acquired=True,
                          digest=record_digest(record))
    return True
