# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup recovery, step 3: reconcile ``index.json`` with ``p/`` (Unreal
foundation FR-PA-18 step 3, Sec N2; review fix Sec S1d).

* ``index.json`` is read only if ``lstat`` shows a regular file (no
  reparse point) owned by the token user; any problem means an empty index.
* An indexed key is kept only if ``p/<key16>/`` is a real directory owned
  by the token user whose ``.complete.json`` parses and names the same
  full SHA-256; otherwise the key is dropped. The record parsed here
  becomes the entry's in-memory ``record_digest`` anchor.
* **No adoption.** An unindexed ``p/<key16>/`` goes to ``trash/`` like
  every other unindexed directory: whoever can forge the tree can forge a
  self-consistent record, so re-hashing against it proves nothing. The
  cost is one re-copy after a rare crash between publish and the index
  write.

Residual (phase 1, documented for the spec): across a restart the anchor
is rebuilt from disk, so a same-user rewrite of tree + record + index
while the worker is stopped is not detectable; only the phase-2 separate
render account (D6) closes it.
"""
import json
import logging
import os
import re
from typing import Dict, List

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.project_archive.cache import INDEX_SCHEMA, Entry
from sethlans_worker_agent.project_archive.entry_record import RecordError, read_record, record_digest
from sethlans_worker_agent.project_archive.layout import CacheLayout

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r'^[0-9a-f]{64}\Z')
MAX_INDEX_BYTES = 16 * 1024 * 1024


def _meta_ok(sha, meta) -> bool:
    if not isinstance(sha, str) or not _SHA_RE.match(sha) or not isinstance(meta, dict):
        return False
    last_used = meta.get('last_used')
    return isinstance(last_used, (int, float)) and not isinstance(last_used, bool) \
        and isinstance(meta.get('source_unc', ''), str)


def load_index(layout: CacheLayout) -> Dict[str, dict]:
    """The persisted index, or ``{}`` when it is missing, foreign or malformed."""
    path = layout.index
    try:
        fs_safe.check_owned_file(path)
        if fs_safe.lstat(path).st_size > MAX_INDEX_BYTES:
            raise ValueError("index.json is too large")
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring the Unreal cache index: %s", exc)
        return {}
    if not isinstance(data, dict) or data.get('schema') != INDEX_SCHEMA or not isinstance(data.get('entries'), dict):
        logger.warning("Ignoring the Unreal cache index: unknown format.")
        return {}
    return {sha: meta for sha, meta in data['entries'].items() if _meta_ok(sha, meta)}


def _indexed_entry(layout: CacheLayout, sha: str, meta: dict):
    entry_dir = layout.entry_dir(sha)
    try:
        fs_safe.check_owned_dir(entry_dir)
        record = read_record(entry_dir)
    except (OSError, RecordError) as exc:
        logger.warning("Dropping Unreal cache index key %s: %s", sha[:16], exc)
        return None
    if record.sha256 != sha:
        logger.warning("Dropping Unreal cache index key %s: .complete.json names another archive.", sha[:16])
        return None
    return Entry(sha, record.total_bytes, float(meta['last_used']), meta.get('source_unc', ''),
                 record_digest=record_digest(record))


def reconcile(layout: CacheLayout) -> List[Entry]:
    """Step 3: the entries to load into the cache; everything else goes to ``trash/``."""
    entries: Dict[str, Entry] = {}
    for sha, meta in load_index(layout).items():
        entry = _indexed_entry(layout, sha, meta)
        if entry is not None and entry.sha256[:16] not in {e.sha256[:16] for e in entries.values()}:
            entries[sha] = entry
    kept = {sha[:16] for sha in entries}
    with os.scandir(layout.p) as listing:
        names = sorted(item.name for item in listing)
    for name in names:
        if name in kept:
            continue
        logger.info("Discarding unindexed Unreal cache directory p/%s.", name)
        path = layout.p / name
        moved = fs_safe.move_aside(path, layout.trash)
        if moved is not None:
            fs_safe.remove_tree(moved)
        else:
            logger.warning("Could not move p/%s to trash; it is retried on the next start.", name)
    return list(entries.values())
