# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Post-job pristine integrity check (Unreal foundation FR-PA-16, Sec N8).

Run only after every UE process is confirmed gone (``survivors == []``)
and before the working copy is deleted, in both workcopy modes:

1. **Full re-hash** of the job's own entry against the record captured
   when the entry was acquired (not a re-read of ``.complete.json``, which
   the job's project code could have rewritten): every file's SHA-256,
   size and attributes, and the exact relpath set;
2. **Stat walk** (FR-PA-9) of every other ``ready`` entry, each held by a
   refcount while it is walked so it cannot be evicted underneath; its
   on-disk record must still have the digest the cache recorded (Sec S1).

A mismatch quarantines the entry. ``IntegrityReport.own_ok`` False means
the job must fail without uploading ("Pristine cache integrity check
failed for this job's project; output discarded."); failures of other
entries only add to the summary line.
"""
import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.cache import ArchiveCache
from sethlans_worker_agent.project_archive.entry_record import (
    CompleteRecord, RecordError, read_record, record_digest, stat_mismatches, tree_dir, walk_entry_tree,
)
from sethlans_worker_agent.project_archive.layout import key16

OWN_FAILURE_MESSAGE = "Pristine cache integrity check failed for this job's project; output discarded."
CHUNK = 8 * 1024 * 1024


@dataclass(frozen=True)
class IntegrityReport:
    own_ok: bool = True
    failures: Dict[str, int] = field(default_factory=dict)  # key16 -> mismatching files
    skipped: str = ''

    @property
    def summary(self) -> str:
        if self.skipped:
            return f"pristine_integrity=skipped({self.skipped})"
        if not self.failures:
            return "pristine_integrity=ok"
        detail = ', '.join(f"{key}: {count} files" for key, count in sorted(self.failures.items()))
        return f"pristine_integrity=FAILED ({detail})"


def skipped(reason: str) -> IntegrityReport:
    return IntegrityReport(skipped=reason)


def _sha256(path, abort: Abort) -> str:
    digest = hashlib.sha256()
    with open(fs_safe.long_path(path), 'rb') as handle:
        while True:
            abort.check()
            data = handle.read(CHUNK)
            if not data:
                return digest.hexdigest()
            digest.update(data)


def rehash_mismatches(entry_dir, record: CompleteRecord, abort: Abort = NEVER) -> List[str]:
    """Relpaths whose content, size or attributes differ (or that are missing/extra)."""
    tree = tree_dir(entry_dir)
    try:
        listing = walk_entry_tree(entry_dir)
    except OSError as exc:
        return [f'<walk: {exc}>']
    expected = record.by_relpath()
    bad = sorted(set(listing.files) ^ set(expected))
    for relpath, want in expected.items():
        st = listing.files.get(relpath)
        if st is None:
            continue
        if st.st_size != want.size or fs_safe.file_attrs(st) != want.attrs:
            bad.append(relpath)
            continue
        try:
            got = _sha256(os.path.join(tree, *relpath.split('/')), abort)
        except OSError:
            bad.append(relpath)
            continue
        if got != want.sha256:
            bad.append(relpath)
    return bad


def _walk_other(cache: ArchiveCache, sha: str) -> int:
    if not cache.acquire(sha, touch=False):  # held only so it cannot be evicted; LRU unchanged
        return 0
    try:
        try:
            record = read_record(cache.entry_dir(sha))
            anchored = record.sha256 == sha and record_digest(record) == cache.record_digest(sha)
            bad = len(stat_mismatches(cache.entry_dir(sha), record)) if anchored else 1
        except RecordError:
            bad = 1
        if bad:
            cache.quarantine(sha, f"post-job stat walk found {bad} mismatching files")
        return bad
    finally:
        cache.release(sha)


def check_after_job(cache: ArchiveCache, own_sha: str, own_record: CompleteRecord,
                    abort: Abort = NEVER) -> IntegrityReport:
    """FR-PA-16 steps 1 and 2; quarantines every mismatching entry."""
    failures: Dict[str, int] = {}
    own_bad = rehash_mismatches(cache.entry_dir(own_sha), own_record, abort)
    if own_bad:
        cache.quarantine(own_sha, f"post-job re-hash found {len(own_bad)} mismatching files")
        failures[key16(own_sha)] = len(own_bad)
    for sha in cache.ready_shas():
        if sha != own_sha:
            bad = _walk_other(cache, sha)
            if bad:
                failures[key16(sha)] = bad
    return IntegrityReport(own_ok=not own_bad, failures=failures)
