# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Deterministic zip writer (Unreal foundation FR-PK-6, FR-PK-7, FR-PK-8,
FR-PK-13).

Layout: ``<Name>/`` and an explicit entry for every directory, the files
under ``<Name>/``, then exactly one root-level member,
``sethlans-manifest.json``. Project members are sorted by archive path
(Python ``str`` order, a directory's name without its trailing ``/``, so
a directory sorts before its contents). The manifest is written **last**:
it carries each file's SHA-256, computed in the same read pass that feeds
the zip writer.

Every member has ``date_time=(1980, 1, 1, 0, 0, 0)``, ``create_system=3``
and fixed permissions; files are ``ZIP_DEFLATED`` at level 6, directories
``ZIP_STORED``; names are UTF-8 (``zipfile`` sets flag bit 11). The zip
comment is ``sethlans-manifest-sha256:<content_sha256>``.

``write_archive`` also reports which files compress more than the
worker's ratio limits allow (FR-PA-6 rule 7 and the total ratio); the
caller re-runs it with those files ``ZIP_STORED`` (``api.package``).
"""
import hashlib
import threading
import zipfile
from dataclasses import dataclass
from typing import Callable, FrozenSet, List, Optional, Sequence, Tuple

from . import manifest as manifest_mod
from . import rules, source_io
from .errors import PackagingCancelled
from .model import ScanResult, SourceFile

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
UNIX = 3
FILE_ATTR = 0o100644 << 16
DIR_ATTR = (0o040755 << 16) | 0x10
COMPRESS_LEVEL = 6
COMMENT_PREFIX = b'sethlans-manifest-sha256:'

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class Member:
    name: str                     # archive name; directories end with '/'
    source: Optional[SourceFile]  # None for a directory


@dataclass(frozen=True)
class WriteResult:
    manifest: manifest_mod.Manifest
    too_compressible: FrozenSet[str]  # archive names whose own ratio exceeds TOTAL_RATIO_LIMIT
    violates: bool                    # the archive breaks a worker ratio limit


def plan_members(result: ScanResult) -> List[Member]:
    """The project members in archive order (the manifest is not included)."""
    prefix = result.project_name
    members = [Member(prefix + '/', None)]
    members += [Member(f"{prefix}/{rel}/", None) for rel in result.dirs]
    members += [Member(f"{prefix}/{item.relpath}", item) for item in result.sources]
    members.sort(key=lambda member: member.name.rstrip('/'))
    return members


def _dir_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.create_system = UNIX
    info.external_attr = DIR_ATTR
    info.compress_type = zipfile.ZIP_STORED
    info.file_size = info.compress_size = info.CRC = 0
    return info


def file_info(name: str, size: int, stored: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.create_system = UNIX
    info.external_attr = FILE_ATTR
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.compress_level = None if stored else COMPRESS_LEVEL
    info.file_size = size
    return info


class _Pass:
    """One write of the archive; tracks progress, cancel and ratios."""

    def __init__(self, zf: zipfile.ZipFile, root: str, total: int, stored: FrozenSet[str],
                 progress: Optional[Progress], cancel: Optional[threading.Event]):
        self.zf, self.root, self.total, self.stored = zf, root, total, stored
        self.progress, self.cancel = progress, cancel
        self.done = 0
        self.sizes: List[Tuple[str, int, int]] = []  # (name, file_size, compress_size)

    def check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise PackagingCancelled()

    def write_file(self, member: Member) -> manifest_mod.ManifestFile:
        src = member.source
        info = file_info(member.name, src.size, member.name in self.stored)
        digest = hashlib.sha256()
        with self.zf.open(info, 'w', force_zip64=src.size >= rules.ZIP64_FORCE_BYTES) as out:
            def feed(chunk: bytes) -> None:
                self.check_cancel()
                digest.update(chunk)
                out.write(chunk)
                self.done += len(chunk)
                if self.progress is not None:
                    self.progress(self.done, self.total)
            source_io.stream_file(self.root, src.relpath, (src.ino, src.dev, src.size, src.mtime_ns), feed)
        self.sizes.append((info.filename, info.file_size, info.compress_size))
        return manifest_mod.ManifestFile(path=member.name, size=src.size, sha256=digest.hexdigest())

    def write_bytes(self, name: str, data: bytes) -> None:
        info = file_info(name, len(data), name in self.stored)
        self.zf.writestr(info, data)
        self.sizes.append((info.filename, info.file_size, info.compress_size))

    def ratios(self) -> Tuple[FrozenSet[str], bool]:
        """The files to store on a second pass, and whether this pass breaks a limit.

        A file breaking the per-entry limit is stored. When the total ratio
        is broken too, so is every file whose own ratio exceeds the total
        limit: with each file at or below it, the total cannot exceed it.
        """
        entry = frozenset(name for name, size, csize in self.sizes if rules.ratio_exceeds(
            size, csize, rules.ENTRY_RATIO_LIMIT, rules.ENTRY_RATIO_MIN_BYTES))
        total = rules.ratio_exceeds(sum(s[1] for s in self.sizes), sum(s[2] for s in self.sizes),
                                    rules.TOTAL_RATIO_LIMIT)
        heavy = frozenset(name for name, size, csize in self.sizes
                          if rules.ratio_exceeds(size, csize, rules.TOTAL_RATIO_LIMIT)) if total else frozenset()
        return entry | heavy, bool(entry) or total


def write_archive(handle, result: ScanResult, warnings: Sequence[str], stored: FrozenSet[str] = frozenset(),
                  progress: Optional[Progress] = None,
                  cancel: Optional[threading.Event] = None) -> WriteResult:
    """Write the archive for ``result`` to the binary file ``handle``.

    Args:
        warnings: The manifest's ``warnings``.
        stored: Archive names to write ``ZIP_STORED`` instead of deflated.

    Raises:
        SourceChangedError: a source file changed or was swapped.
        PackagingCancelled: ``cancel`` was set.
    """
    with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True,
                         compresslevel=COMPRESS_LEVEL) as zf:
        writer = _Pass(zf, result.root, result.total_bytes, stored, progress, cancel)
        files = []
        for member in plan_members(result):
            writer.check_cancel()
            if member.source is None:
                zf.mkdir(_dir_info(member.name))
            else:
                files.append(writer.write_file(member))
        manifest = manifest_mod.build_manifest(result.project_name, files, result.detected_features, warnings)
        writer.write_bytes(rules.MANIFEST_NAME, manifest.to_json_bytes())
        zf.comment = COMMENT_PREFIX + manifest.content_sha256.encode('ascii')
    heavy, violates = writer.ratios()
    return WriteResult(manifest=manifest, too_compressible=heavy, violates=violates)
