# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Central-directory pre-scan (Unreal foundation FR-PA-6 step 1).

Every entry is checked before a single byte is extracted; any violation
rejects the **whole** archive with ``UnsafeArchiveError(reason, entry)``:

1-2. name rules (``zip_rules.normalize_relpath``), applied to the raw
     ``orig_filename`` (``ZipInfo.filename`` is cut at a NUL and has its
     backslashes rewritten on Windows, which would hide rule breaks);
3.   ``S_IFLNK`` in ``external_attr >> 16`` whatever ``create_system``, or a
     Unix entry whose type is not 0, a regular file or a directory;
4.   the encryption flag;
5.   a compression method other than STORED or DEFLATED;
6.   a case-insensitive duplicate, or a name that is both a file and a
     directory prefix of another entry;
7.   ``file_size / max(compress_size, 1) > 200`` with ``file_size >= 1 MiB``.

The archive is also rejected for more than 200,000 entries, a declared
uncompressed total above ``max_uncompressed``, or a total ratio above 100.
A directory entry that declares data is rejected too.
"""
import stat
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from sethlans_worker_agent.project_archive import zip_rules
from sethlans_worker_agent.project_archive.errors import UnsafeArchiveError
from sethlans_worker_agent.project_archive.zip_precheck import MAX_ENTRIES

ENTRY_RATIO_LIMIT = 200
ENTRY_RATIO_MIN_BYTES = 1024 * 1024
TOTAL_RATIO_LIMIT = 100
ALLOWED_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
_UNIX = 3


@dataclass(frozen=True)
class ScannedEntry:
    info: zipfile.ZipInfo
    relpath: str
    is_dir: bool


@dataclass(frozen=True)
class ScanResult:
    entries: Tuple[ScannedEntry, ...]
    total_uncompressed: int
    total_compressed: int

    @property
    def file_relpaths(self) -> Tuple[str, ...]:
        return tuple(entry.relpath for entry in self.entries if not entry.is_dir)


def _raw_name(info: zipfile.ZipInfo) -> str:
    return getattr(info, 'orig_filename', None) or info.filename


def _check_mode(info: zipfile.ZipInfo, name: str) -> None:
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind == stat.S_IFLNK:
        raise UnsafeArchiveError("symlink entry", name)
    if info.create_system == _UNIX and kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise UnsafeArchiveError("special file entry", name)


def _check_entry(info: zipfile.ZipInfo) -> ScannedEntry:
    name = _raw_name(info)
    is_dir = name.replace('\\', '/').endswith('/')
    try:
        relpath = zip_rules.normalize_relpath(name, is_dir=is_dir)
    except ValueError as exc:
        raise UnsafeArchiveError(str(exc), name) from exc
    _check_mode(info, name)
    if info.flag_bits & 0x1:
        raise UnsafeArchiveError("encrypted entry", name)
    if info.compress_type not in ALLOWED_METHODS:
        raise UnsafeArchiveError(f"compression method {info.compress_type} is not STORED or DEFLATED", name)
    if is_dir and info.file_size:
        raise UnsafeArchiveError("directory entry with data", name)
    if (info.file_size >= ENTRY_RATIO_MIN_BYTES
            and info.file_size / max(info.compress_size, 1) > ENTRY_RATIO_LIMIT):
        raise UnsafeArchiveError(f"compression ratio above {ENTRY_RATIO_LIMIT}", name)
    return ScannedEntry(info=info, relpath=relpath, is_dir=is_dir)


def _check_names(entries: List[ScannedEntry]) -> None:
    seen: Dict[Tuple[str, ...], ScannedEntry] = {}
    for entry in entries:
        key = zip_rules.fold_key(entry.relpath)
        if key in seen:
            raise UnsafeArchiveError("duplicate name (case-insensitive)", entry.relpath)
        seen[key] = entry
    prefixes: Set[Tuple[str, ...]] = set()
    for key in seen:
        prefixes.update(key[:depth] for depth in range(1, len(key)))
    for key, entry in seen.items():
        if not entry.is_dir and key in prefixes:
            raise UnsafeArchiveError("name is both a file and a directory", entry.relpath)


def scan(zf: zipfile.ZipFile, max_uncompressed: int) -> ScanResult:
    """Pre-scan every entry of ``zf``; raises ``UnsafeArchiveError``."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise UnsafeArchiveError(f"more than {MAX_ENTRIES} entries")
    entries = [_check_entry(info) for info in infos]
    _check_names(entries)
    total = sum(entry.info.file_size for entry in entries)
    compressed = sum(entry.info.compress_size for entry in entries)
    if total > max_uncompressed:
        raise UnsafeArchiveError(f"declared uncompressed size {total} exceeds the limit {max_uncompressed}")
    if total / max(compressed, 1) > TOTAL_RATIO_LIMIT:
        raise UnsafeArchiveError(f"total compression ratio above {TOTAL_RATIO_LIMIT}")
    return ScanResult(entries=tuple(entries), total_uncompressed=total, total_compressed=compressed)
