# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Safe extraction of the **local** archive copy (Unreal foundation FR-PA-6,
FR-PA-7). The untrusted zip is never parsed over SMB.

``open_archive`` runs the EOCD pre-check (step 0), constructs the
``ZipFile`` and pre-scans every entry (step 1). The caller then reserves
the stage-B disk budget from ``scan.total_uncompressed`` (step 2) and
calls ``extract``, which streams each file through ``zf.open`` in 1 MiB
chunks:

* into ``<target-dir>/.sx-<uuid>.part``, renamed to its target afterwards
  (a uuid temp name never collides with a real ``x.part`` entry);
* counting bytes: more than the declared ``file_size``, or a different
  count at EOF, is a lying header; the local header must also agree with
  the central directory (method, CRC and sizes), so two readers cannot
  see two different archives;
* hashing each file (SHA-256) and recording ``(relpath, size, mtime_ns
  after the rename, sha256, attrs)`` for ``.complete.json``;
* asserting containment with ``commonpath`` on resolved paths;
* ignoring the zip's permissions and attributes (files inherit the
  owner-only DACL of ``CACHE_DIR``);
* checking cancel, shutdown and the deadline on every chunk.

CRC and decompression errors surface as ``UnsafeArchiveError``.
"""
import hashlib
import os
import struct
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe, zip_precheck, zip_scan
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.errors import UnsafeArchiveError

CHUNK = 1024 * 1024
_LOCAL = struct.Struct('<4s2B4HL2L2H')
_LOCAL_SIG = b'PK\x03\x04'
_ZIP64_MARK = 0xFFFFFFFF


@dataclass(frozen=True)
class FileRecord:
    relpath: str
    size: int
    mtime_ns: int
    sha256: str
    attrs: int


@dataclass(frozen=True)
class ExtractResult:
    files: Tuple[FileRecord, ...]
    dirs: Tuple[str, ...]
    total_bytes: int


def _check_local_header(raw, info: zipfile.ZipInfo) -> None:
    raw.seek(info.header_offset)
    data = raw.read(_LOCAL.size)
    if len(data) != _LOCAL.size or data[:4] != _LOCAL_SIG:
        raise UnsafeArchiveError("missing local file header", info.filename)
    fields = _LOCAL.unpack(data)
    flags, method, crc, csize, size = fields[3], fields[4], fields[7], fields[8], fields[9]
    if method != info.compress_type:
        raise UnsafeArchiveError("local header disagrees with the central directory (method)", info.filename)
    if flags & 0x08:  # sizes and CRC follow in a data descriptor
        return
    if crc != info.CRC:
        raise UnsafeArchiveError("local header disagrees with the central directory (CRC)", info.filename)
    for local, central in ((csize, info.compress_size), (size, info.file_size)):
        if local != _ZIP64_MARK and local != central:
            raise UnsafeArchiveError("local header disagrees with the central directory (size)", info.filename)


class OpenedArchive:
    """A pre-checked, pre-scanned local zip; use as a context manager."""

    def __init__(self, zip_path, max_uncompressed: int):
        self.path = os.fspath(zip_path)
        zip_precheck.precheck(self.path)
        try:
            self._zf = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
            raise UnsafeArchiveError(f"unreadable zip ({exc})") from exc
        try:
            self.scan = zip_scan.scan(self._zf, max_uncompressed)
        except BaseException:
            self._zf.close()
            raise

    def __enter__(self) -> 'OpenedArchive':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._zf.close()

    def extract(self, dest, abort: Abort = NEVER,
                on_bytes: Optional[Callable[[int], None]] = None) -> ExtractResult:
        """Extract into the (empty) directory ``dest``; see the module docstring."""
        dest = os.fspath(dest)
        root_real = os.path.normcase(os.path.realpath(dest))
        parents: Dict[str, str] = {}
        records: List[FileRecord] = []
        dirs: List[str] = []
        with open(self.path, 'rb') as raw:
            for entry in self.scan.entries:
                abort.check()
                target = os.path.join(dest, *entry.relpath.split('/'))
                if entry.is_dir:
                    fs_safe_makedirs(target)
                    dirs.append(entry.relpath)
                    continue
                _check_local_header(raw, entry.info)
                parent = os.path.dirname(target)
                fs_safe_makedirs(parent)
                _assert_inside(root_real, parent, target, parents)
                records.append(self._extract_file(entry, parent, target, abort, on_bytes))
        return ExtractResult(files=tuple(records), dirs=tuple(sorted(dirs)),
                             total_bytes=sum(r.size for r in records))

    def _extract_file(self, entry, parent: str, target: str, abort: Abort, on_bytes) -> FileRecord:
        info = entry.info
        temp = os.path.join(parent, f".sx-{uuid.uuid4().hex}.part")
        digest, count = hashlib.sha256(), 0
        try:
            with self._zf.open(info) as src, open(fs_safe.long_path(temp), 'xb') as out:
                while True:
                    abort.check()
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > info.file_size:
                        raise UnsafeArchiveError("entry is larger than its declared size", info.filename)
                    digest.update(chunk)
                    out.write(chunk)
                    if on_bytes is not None:
                        on_bytes(len(chunk))
            if count != info.file_size:
                raise UnsafeArchiveError("entry is smaller than its declared size", info.filename)
            os.replace(fs_safe.long_path(temp), fs_safe.long_path(target))
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            _discard(temp)
            raise UnsafeArchiveError(f"corrupt entry data ({exc})", info.filename) from exc
        except BaseException:
            _discard(temp)
            raise
        st = fs_safe.lstat(target)
        return FileRecord(relpath=entry.relpath, size=count, mtime_ns=st.st_mtime_ns,
                          sha256=digest.hexdigest(), attrs=fs_safe.file_attrs(st))


def fs_safe_makedirs(path: str) -> None:
    os.makedirs(fs_safe.long_path(path), exist_ok=True)


def _assert_inside(root_real: str, parent: str, target: str, cache: Dict[str, str]) -> None:
    """Defense in depth: the resolved target lies under the resolved root (FR-PA-7)."""
    parent_real = cache.get(parent)
    if parent_real is None:
        parent_real = cache[parent] = os.path.normcase(os.path.realpath(parent))
    target_real = os.path.join(parent_real, os.path.normcase(os.path.basename(target)))
    if os.path.commonpath([root_real, target_real]) != root_real:
        raise UnsafeArchiveError("entry resolves outside the extraction directory", target)


def _discard(path: str) -> None:
    try:
        os.unlink(fs_safe.long_path(path))
    except OSError:
        pass


def extract_zip(zip_path, dest_tmp, max_uncompressed: int, abort: Abort = NEVER, *,
                reserve: Optional[Callable[[int], None]] = None,
                on_bytes: Optional[Callable[[int], None]] = None) -> ExtractResult:
    """Pre-check, pre-scan, ``reserve(total)`` (stage B), then extract into ``dest_tmp``."""
    with OpenedArchive(zip_path, max_uncompressed) as archive:
        if reserve is not None:
            reserve(archive.scan.total_uncompressed)
        return archive.extract(dest_tmp, abort, on_bytes)
