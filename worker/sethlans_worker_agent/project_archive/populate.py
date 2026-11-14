# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cache hit, or copy + pin check + extract + publish (Unreal foundation
FR-PA-5, FR-PA-8, FR-PA-9, FR-PA-10, FR-PA-11).

``obtain_entry(cache, pin, fetch, abort, max_uncompressed)``:

* **Hit** (no share access): ``cache.acquire(pin)`` takes a refcount on a
  ``ready`` entry, then, outside the index lock, the entry is verified
  (FR-PA-9): ``.complete.json`` parses, its **full** SHA-256 equals the
  pin (the ``key16`` prefix is never enough), its digest equals the
  in-memory ``record_digest`` the cache keeps (Sec S1: the on-disk record
  alone is forgeable) and a stat walk of ``tree/`` matches it exactly. A
  failed verification quarantines the entry, releases the refcount and
  continues as a miss.
* **Miss**: under the populate lock for the pin (cancellable wait), the
  hit is re-checked, then ``fetch(abort)`` copies the archive (stage-A
  budget, copy thread, pin check: ``share_copy.copy_archive``). The local
  copy is pre-checked and pre-scanned, the stage-B budget
  (``declared_uncompressed``; also within ``CACHE_MAX``) is reserved, the
  archive is extracted into ``p/<key16>.tmp-<uuid>/tree/`` and published.
  The tmp dir and the local zip are removed on every path.

``CACHE_DIR``'s owner-only ACL (and no reparse point) is verified before
every acquire and before every populate (FR-WC-4).
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from sethlans_worker_agent.project_archive import eviction, fs_safe
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.cache import ArchiveCache
from sethlans_worker_agent.project_archive.entry_record import (
    CompleteRecord, RecordError, read_record, record_digest, stat_mismatches, write_record,
)
from sethlans_worker_agent.project_archive.layout import TREE_NAME
from sethlans_worker_agent.project_archive.publish import publish
from sethlans_worker_agent.project_archive.safe_extract import OpenedArchive
from sethlans_worker_agent.project_archive.share_copy import CopiedArchive

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcquiredEntry:
    """A pristine entry this job holds one refcount on (``cache.release`` it)."""
    sha256: str
    entry_dir: Path
    record: CompleteRecord
    hit: bool
    copy_s: float = 0.0
    extract_s: float = 0.0

    @property
    def tree(self) -> Path:
        return self.entry_dir / TREE_NAME


def verify_entry(entry_dir: Path, pin: str, expected_digest: str) -> CompleteRecord:
    """FR-PA-9 plus the in-memory anchor (Sec S1); raises ``RecordError``/``OSError``."""
    fs_safe.check_owned_dir(entry_dir.parent)
    fs_safe.check_owned_dir(entry_dir)
    record = read_record(entry_dir)
    if record.sha256 != pin:
        raise RecordError("full SHA-256 differs from the pin")
    if not expected_digest or record_digest(record) != expected_digest:
        raise RecordError(".complete.json differs from the record this worker published")
    bad = stat_mismatches(entry_dir, record)
    if bad:
        raise RecordError(f"{len(bad)} files differ from .complete.json")
    return record


def try_hit(cache: ArchiveCache, pin: str) -> Optional[AcquiredEntry]:
    cache.layout.verify_root()
    if not cache.acquire(pin):
        return None
    entry_dir = cache.entry_dir(pin)
    try:
        record = verify_entry(entry_dir, pin, cache.record_digest(pin))
    except (RecordError, OSError) as exc:
        cache.quarantine(pin, f"verification failed: {exc}")
        cache.release(pin)
        return None
    except BaseException:
        cache.release(pin)
        raise
    return AcquiredEntry(sha256=pin, entry_dir=entry_dir, record=record, hit=True)


def _extract_and_publish(cache: ArchiveCache, pin: str, copied: CopiedArchive, abort: Abort,
                         max_uncompressed: int) -> AcquiredEntry:
    cache.layout.check_child('p')  # owner and no reparse point before writing under it
    tmp = cache.layout.new_tmp_entry_dir(pin)
    started = time.monotonic()
    try:
        fs_safe.ensure_owned_dir(tmp)
        tree = tmp / TREE_NAME
        tree.mkdir()
        with OpenedArchive(copied.path, max_uncompressed) as archive:
            declared = archive.scan.total_uncompressed
            # Held through the publish, so CACHE_MAX counts these bytes until pristine_total does.
            with eviction.reserve(cache, declared, populate_bytes=declared) as reservation:
                result = archive.extract(tree, abort, reservation.consume)
                record = CompleteRecord(sha256=pin, source=copied.source, files=result.files,
                                        dirs=result.dirs, total_bytes=result.total_bytes)
                write_record(tmp, record)
                publish(cache, tmp, record)
    except BaseException:
        fs_safe.remove_tree(tmp)
        raise
    # On a shared (reused) entry the job's anchor is still the FRESH record (Sec S1a).
    return AcquiredEntry(sha256=pin, entry_dir=cache.entry_dir(pin), record=record, hit=False,
                         extract_s=time.monotonic() - started)


def _populate(cache: ArchiveCache, pin: str, fetch: Callable[[Abort], CopiedArchive], abort: Abort,
              max_uncompressed: int) -> AcquiredEntry:
    cache.layout.verify_root()
    started = time.monotonic()
    copied = fetch(abort)
    copy_s = time.monotonic() - started
    try:
        entry = _extract_and_publish(cache, pin, copied, abort, max_uncompressed)
    finally:
        fs_safe.remove_tree(copied.path)
    logger.info("Unreal archive %s cached (copy %.1f s, extract %.1f s, %d files).",
                pin[:16], copy_s, entry.extract_s, len(entry.record.files))
    return AcquiredEntry(sha256=entry.sha256, entry_dir=entry.entry_dir, record=entry.record, hit=False,
                         copy_s=copy_s, extract_s=entry.extract_s)


def obtain_entry(cache: ArchiveCache, pin: str, fetch: Callable[[Abort], CopiedArchive], abort: Abort,
                 max_uncompressed: int) -> AcquiredEntry:
    """Return the verified pristine entry for ``pin``, populating it on a miss."""
    hit = try_hit(cache, pin)
    if hit is not None:
        return hit
    with cache.populate_locks.hold(pin, abort):
        hit = try_hit(cache, pin)
        if hit is not None:
            return hit
        return _populate(cache, pin, fetch, abort, max_uncompressed)
