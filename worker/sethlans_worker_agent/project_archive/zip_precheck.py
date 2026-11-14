# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End Of Central Directory pre-check (Unreal foundation FR-PA-6 step 0).

Runs on the **local** copy with plain ``open('rb')``/``seek``, before any
``zipfile.ZipFile`` is constructed, so a hostile header cannot make
``ZipFile()`` allocate millions of ``ZipInfo`` objects. It locates the
EOCD record exactly as ``zipfile`` does (the last 22 bytes, else the last
signature in the final 64 KiB) and the Zip64 locator and record when
present (``zipfile`` reads the record immediately before the locator).

Rejected (``UnsafeArchiveError``):

* a declared entry count above ``MAX_ENTRIES`` (200,000);
* a declared central-directory size above ``MAX_CD_BYTES`` (64 MiB);
* inconsistent records: a multi-disk archive, a central directory that
  ends beyond the EOCD (offset + size beyond the file), a Zip64 locator
  without its record.

``ZipFile`` parses entries until it has consumed the declared directory
size, whatever the declared count says. So the pre-check also **walks**
the central directory headers (streaming, constant memory) and rejects
more than ``MAX_ENTRIES`` headers, or a header count or byte total that
differs from the EOCD. That makes the bound real.
"""
import os
import struct
from dataclasses import dataclass

from sethlans_worker_agent.project_archive.errors import UnsafeArchiveError

MAX_ENTRIES = 200_000
MAX_CD_BYTES = 64 * 1024 * 1024

_EOCD = struct.Struct('<4s4H2LH')
_EOCD_SIG = b'PK\x05\x06'
_LOC64 = struct.Struct('<4sLQL')
_LOC64_SIG = b'PK\x06\x07'
_EOCD64 = struct.Struct('<4sQ2H2L4Q')
_EOCD64_SIG = b'PK\x06\x06'
_CDH = struct.Struct('<4s4B4HL2L5H2L')
_CDH_SIG = b'PK\x01\x02'
_MAX_COMMENT = 0xFFFF


@dataclass(frozen=True)
class PrecheckResult:
    entries: int
    cd_size: int
    cd_offset: int  # where the directory starts in the file (``zipfile``'s start_dir)
    zip64: bool


def _find_eocd(handle, size: int):
    if size < _EOCD.size:
        raise UnsafeArchiveError("file is too small to be a zip archive")
    handle.seek(size - _EOCD.size)
    data = handle.read(_EOCD.size)
    if data[:4] == _EOCD_SIG and data[-2:] == b'\x00\x00':
        return size - _EOCD.size, _EOCD.unpack(data)
    tail_len = min(size, _EOCD.size + _MAX_COMMENT)
    handle.seek(size - tail_len)
    tail = handle.read(tail_len)
    start = tail.rfind(_EOCD_SIG)
    if start < 0 or start + _EOCD.size > len(tail):
        raise UnsafeArchiveError("no End Of Central Directory record")
    return size - tail_len + start, _EOCD.unpack(tail[start:start + _EOCD.size])


def _read_record64(handle, pos: int) -> bytes:
    handle.seek(pos)
    data = handle.read(_EOCD64.size)
    if len(data) != _EOCD64.size:
        raise UnsafeArchiveError("truncated Zip64 End Of Central Directory record")
    return data


def _read_zip64(handle, eocd_pos: int):
    """``(location, entries, cd_size, cd_offset)`` from the Zip64 record, or None.

    Mirrors CPython's ``zipfile._EndRecData64`` so the values checked here
    are the values ``ZipFile`` will use.
    """
    offset = eocd_pos - _LOC64.size
    if offset < 0:
        return None
    handle.seek(offset)
    loc = _LOC64.unpack(handle.read(_LOC64.size))
    if loc[0] != _LOC64_SIG:
        return None
    _sig, disk_no, reloff, disks = loc
    if disk_no != 0 or disks > 1:
        raise UnsafeArchiveError("multi-disk Zip64 archives are not supported")
    offset -= _EOCD64.size
    if offset < 0 or reloff > offset:
        raise UnsafeArchiveError("inconsistent Zip64 End Of Central Directory locator")
    extra = offset - reloff
    data = _read_record64(handle, reloff)
    if data[:4] != _EOCD64_SIG and reloff != offset:
        extra, data = 0, _read_record64(handle, offset)
    if data[:4] != _EOCD64_SIG:
        raise UnsafeArchiveError("Zip64 locator without its record")
    rec = _EOCD64.unpack(data)
    if rec[9] + rec[8] != reloff or rec[1] + 12 != _EOCD64.size + extra:
        raise UnsafeArchiveError("inconsistent Zip64 End Of Central Directory record")
    if rec[4] != 0 or rec[5] != 0 or rec[6] != rec[7]:
        raise UnsafeArchiveError("multi-disk Zip64 archives are not supported")
    return offset - extra, rec[7], rec[8], rec[9]


def _check_declared(entries: int, cd_size: int, cd_offset: int, location: int) -> int:
    if entries > MAX_ENTRIES:
        raise UnsafeArchiveError(f"declares {entries} entries (limit {MAX_ENTRIES})")
    if cd_size > MAX_CD_BYTES:
        raise UnsafeArchiveError(f"declares a {cd_size}-byte central directory (limit {MAX_CD_BYTES})")
    concat = location - cd_size - cd_offset  # bytes prepended to the archive, as zipfile computes
    if concat < 0:
        raise UnsafeArchiveError("inconsistent End Of Central Directory (directory beyond the record)")
    return cd_offset + concat


def _walk_headers(handle, start: int, cd_size: int) -> int:
    """Count central-directory headers without building ``ZipInfo`` objects."""
    handle.seek(start)
    consumed = count = 0
    while consumed < cd_size:
        header = handle.read(_CDH.size)
        if len(header) != _CDH.size or header[:4] != _CDH_SIG:
            raise UnsafeArchiveError("corrupt central directory header")
        fields = _CDH.unpack(header)
        variable = fields[12] + fields[13] + fields[14]  # name, extra, comment lengths
        if len(handle.read(variable)) != variable:
            raise UnsafeArchiveError("truncated central directory")
        consumed += _CDH.size + variable
        count += 1
        if count > MAX_ENTRIES:
            raise UnsafeArchiveError(f"central directory has more than {MAX_ENTRIES} entries")
    if consumed != cd_size:
        raise UnsafeArchiveError("central directory size does not match its headers")
    return count


def precheck(path) -> PrecheckResult:
    """Validate the EOCD records of the local zip at ``path`` (FR-PA-6 step 0)."""
    size = os.path.getsize(path)
    with open(path, 'rb') as handle:
        eocd_pos, eocd = _find_eocd(handle, size)
        _sig, disk_no, disk_cd, entries_disk, entries, cd_size, cd_offset, _comment = eocd
        if disk_no != 0 or disk_cd != 0 or entries_disk != entries:
            raise UnsafeArchiveError("multi-disk archives are not supported")
        location, zip64 = eocd_pos, False
        z64 = _read_zip64(handle, eocd_pos)
        if z64 is not None:
            location, entries, cd_size, cd_offset = z64
            zip64 = True
        start = _check_declared(entries, cd_size, cd_offset, location)
        count = _walk_headers(handle, start, cd_size)
        if count != entries:
            raise UnsafeArchiveError(f"central directory has {count} entries but declares {entries}")
    return PrecheckResult(entries=entries, cd_size=cd_size, cd_offset=start, zip64=zip64)
