# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``.complete.json`` of a pristine entry and the cheap verification on use
(Unreal foundation FR-PA-1, FR-PA-7, FR-PA-9).

The record holds the full SHA-256 of the archive, its source
``(unc, size, mtime_ns)``, the per-file index ``(relpath, size,
mtime_ns, sha256, attrs)``, the directory list and the total bytes. It
lives on disk under ``CACHE_DIR`` and is parsed strictly: a malformed or
oversized file, a relpath that breaks the FR-PA-6 rules, or a bad hash
makes it invalid (``RecordError``).

``stat_mismatches`` is FR-PA-9's stat walk of ``tree/``: the same relpath
sets (files and directories), sizes and mtimes, with no reparse point.

The on-disk record is writable by the worker's own account, so it is never
the integrity anchor by itself (Sec S1): the cache keeps
``record_digest(record)`` of the record it published (or loaded at
startup) in memory, and every later use compares the on-disk record's
digest with it.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from sethlans_worker_agent.project_archive import fs_safe, zip_rules
from sethlans_worker_agent.project_archive.layout import COMPLETE_NAME, TREE_NAME
from sethlans_worker_agent.project_archive.safe_extract import FileRecord

SCHEMA = 1
MAX_RECORD_BYTES = 64 * 1024 * 1024
_SHA_RE = re.compile(r'^[0-9a-f]{64}\Z')


class RecordError(ValueError):
    """``.complete.json`` is missing, malformed or does not describe the tree."""


@dataclass(frozen=True)
class SourceInfo:
    unc: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class CompleteRecord:
    sha256: str
    source: SourceInfo
    files: Tuple[FileRecord, ...]
    dirs: Tuple[str, ...]
    total_bytes: int

    def by_relpath(self) -> Dict[str, FileRecord]:
        return {record.relpath: record for record in self.files}


def canonical_bytes(record: CompleteRecord) -> bytes:
    """The one serialization of a record (written to disk and digested)."""
    payload = {
        'schema': SCHEMA, 'sha256': record.sha256,
        'source': {'unc': record.source.unc, 'size': record.source.size, 'mtime_ns': record.source.mtime_ns},
        'total_bytes': record.total_bytes,
        'dirs': list(record.dirs),
        'files': [[f.relpath, f.size, f.mtime_ns, f.sha256, f.attrs] for f in record.files],
    }
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def record_digest(record: CompleteRecord) -> str:
    """SHA-256 of ``canonical_bytes``: the in-memory anchor kept on the cache entry (Sec S1)."""
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def content_tuples(record: CompleteRecord):
    """``(relpath, size, sha256, attrs)`` per file: what an extraction must reproduce."""
    return sorted((f.relpath, f.size, f.sha256, f.attrs) for f in record.files)


def write_record(entry_dir, record: CompleteRecord) -> None:
    """Write ``entry_dir/.complete.json`` (exclusive create)."""
    path = os.path.join(os.fspath(entry_dir), COMPLETE_NAME)
    with open(fs_safe.long_path(path), 'xb') as handle:
        handle.write(canonical_bytes(record))


def _int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecordError(f"{name} must be a non-negative int")
    return value


def _relpath(value) -> str:
    if not isinstance(value, str):
        raise RecordError("relpath must be a string")
    try:
        normalized = zip_rules.normalize_relpath(value)
    except ValueError as exc:
        raise RecordError(f"invalid relpath: {exc}") from exc
    if normalized != value:
        raise RecordError("relpath is not normalized")
    return value


def _file(item) -> FileRecord:
    if not isinstance(item, list) or len(item) != 5:
        raise RecordError("file record must be a 5-item list")
    relpath, size, mtime_ns, sha, attrs = item
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise RecordError("file sha256 is invalid")
    return FileRecord(_relpath(relpath), _int(size, 'size'), _int(mtime_ns, 'mtime_ns'), sha,
                      _int(attrs, 'attrs'))


def parse_record(data: dict) -> CompleteRecord:
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise RecordError("unknown schema")
    sha = data.get('sha256')
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise RecordError("sha256 is invalid")
    source = data.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('unc'), str):
        raise RecordError("source is invalid")
    files = data.get('files')
    dirs = data.get('dirs')
    if not isinstance(files, list) or not isinstance(dirs, list):
        raise RecordError("files and dirs must be lists")
    parsed = tuple(_file(item) for item in files)
    if len({f.relpath for f in parsed}) != len(parsed):
        raise RecordError("duplicate relpath")
    total = _int(data.get('total_bytes'), 'total_bytes')
    if total != sum(f.size for f in parsed):
        raise RecordError("total_bytes does not match the files")
    return CompleteRecord(sha256=sha, source=SourceInfo(source['unc'], _int(source.get('size'), 'source.size'),
                                                        _int(source.get('mtime_ns'), 'source.mtime_ns')),
                          files=parsed, dirs=tuple(_relpath(d) for d in dirs), total_bytes=total)


def read_record(entry_dir) -> CompleteRecord:
    """Parse ``entry_dir/.complete.json``; raises ``RecordError``."""
    path = os.path.join(os.fspath(entry_dir), COMPLETE_NAME)
    try:
        st = fs_safe.lstat(path)
        if fs_safe.is_reparse(st) or not os.path.isfile(fs_safe.long_path(path)):
            raise RecordError(".complete.json is not a regular file")
        if st.st_size > MAX_RECORD_BYTES:
            raise RecordError(".complete.json is too large")
        with open(fs_safe.long_path(path), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError(f".complete.json unreadable ({type(exc).__name__})") from exc
    return parse_record(data)


def tree_dir(entry_dir) -> str:
    return os.path.join(os.fspath(entry_dir), TREE_NAME)


def walk_entry_tree(entry_dir) -> fs_safe.TreeListing:
    """Walk ``<entry_dir>/tree`` after checking neither directory is a link (FR-PA-21)."""
    for path in (os.fspath(entry_dir), tree_dir(entry_dir)):
        if fs_safe.is_reparse(fs_safe.lstat(path)):
            raise fs_safe.ReparsePointError(f"{path} is a symlink or junction")
    return fs_safe.walk_tree(tree_dir(entry_dir))


def stat_mismatches(entry_dir, record: CompleteRecord) -> List[str]:
    """FR-PA-9 stat walk: relpaths that differ from the record (``['<walk error>']`` on a link)."""
    try:
        listing = walk_entry_tree(entry_dir)
    except OSError as exc:
        return [f'<walk: {exc}>']
    expected = record.by_relpath()
    bad = sorted(set(listing.files) ^ set(expected))
    for relpath, st in listing.files.items():
        want = expected.get(relpath)
        if want is not None and (st.st_size != want.size or st.st_mtime_ns != want.mtime_ns):
            bad.append(relpath)
    bad.extend(sorted(set(listing.dirs) ^ set(_all_dirs(record))))
    return bad


def _all_dirs(record: CompleteRecord) -> List[str]:
    """Directory entries plus every parent of a file (extraction creates both)."""
    found = set(record.dirs)
    for relpath in [f.relpath for f in record.files] + list(record.dirs):
        parts = relpath.split('/')
        found.update('/'.join(parts[:depth]) for depth in range(1, len(parts)))
    return sorted(found)
