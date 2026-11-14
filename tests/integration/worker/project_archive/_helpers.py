# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the project-archive real-filesystem tests (FR-T-6): synthetic
zips, a cache under ``tmp_path`` and local stand-ins for the share.
"""
import hashlib
import json
import os
import stat
import struct
import zipfile
from collections import namedtuple
from pathlib import Path

from sethlans_worker_agent.project_archive import eviction, share_copy
from sethlans_worker_agent.project_archive.cache import ArchiveCache, CacheLimits
from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.project_archive.limits import PipelineLimits
from shared import owner_only_acl
from shared.unreal.unc import validate_unc_dir

GIB = 1024 ** 3
SHARE_ROOT = validate_unc_dir('\\\\nas.lan\\sethlans')
DiskUsage = namedtuple('DiskUsage', 'total used free')

PROJECT_FILES = {
    'Demo/Demo.uproject': b'{"FileVersion": 3}',
    'Demo/Config/DefaultEngine.ini': b'[/Script/Engine.RendererSettings]\nr.X=1\n',
    'Demo/Content/Maps/Main.umap': b'M' * 4096,
    'Demo/Content/Chars/Hero.uasset': b'H' * 2048,
}


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def make_zip(path, files=None, dirs=(), method=zipfile.ZIP_DEFLATED):
    """A zip with ``dirs`` (trailing ``/``) and ``files`` ({name: bytes}); returns its SHA-256."""
    files = PROJECT_FILES if files is None else files
    with zipfile.ZipFile(path, 'w', compression=method) as zf:
        for name in dirs:
            zf.writestr(zipfile.ZipInfo(name if name.endswith('/') else name + '/'), b'')
        for name, data in files.items():
            zf.writestr(name, data)
    return sha256_file(path)


def make_zip_infos(path, infos):
    """A zip from ``(ZipInfo, bytes)`` pairs (for crafted attributes)."""
    with zipfile.ZipFile(path, 'w') as zf:
        for info, data in infos:
            zf.writestr(info, data)
    return sha256_file(path)


def info(name, *, create_system=0, mode=0, method=zipfile.ZIP_DEFLATED):
    zinfo = zipfile.ZipInfo(name)
    zinfo.create_system = create_system
    zinfo.external_attr = (mode & 0xFFFF) << 16
    zinfo.compress_type = method
    return zinfo


def symlink_info(create_system=3):
    return info('Demo/link', create_system=create_system, mode=stat.S_IFLNK | 0o777)


def patch_bytes(path, offset, data):
    with open(path, 'r+b') as handle:
        handle.seek(offset)
        handle.write(data)


def local_header_offset(path, name):
    with zipfile.ZipFile(path) as zf:
        return zf.getinfo(name).header_offset


def central_header_offset(path, name):
    with zipfile.ZipFile(path) as zf:
        pos = zf.start_dir
    data = Path(path).read_bytes()
    while data[pos:pos + 4] == b'PK\x01\x02':
        name_len, extra_len, comment_len = struct.unpack('<3H', data[pos + 28:pos + 34])
        if data[pos + 46:pos + 46 + name_len].decode('utf-8') == name:
            return pos
        pos += 46 + name_len + extra_len + comment_len
    raise KeyError(name)


def set_flag_bits(path, name, bits):
    central, local = central_header_offset(path, name), local_header_offset(path, name)
    patch_bytes(path, central + 8, struct.pack('<H', bits))
    patch_bytes(path, local + 6, struct.pack('<H', bits))


def replace_name_bytes(path, old: bytes, new: bytes):
    """Same-length rename in every header (e.g. to plant a NUL)."""
    assert len(old) == len(new)
    data = Path(path).read_bytes()
    Path(path).write_bytes(data.replace(old, new))


def write_zip64_eocd(path, entries):
    """Only a Zip64 EOCD record + locator + EOCD (empty directory, ``entries`` declared)."""
    record = struct.pack('<4sQ2H2L4Q', b'PK\x06\x06', 44, 45, 45, 0, 0, entries, entries, 0, 0)
    locator = struct.pack('<4sLQL', b'PK\x06\x07', 0, 0, 1)
    eocd = struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    with open(path, 'wb') as handle:
        handle.write(record + locator + eocd)


def write_eocd(path, entries, cd_size, cd_offset=0, prefix=b''):
    eocd = struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, entries, entries, cd_size, cd_offset, 0)
    with open(path, 'wb') as handle:
        handle.write(prefix + eocd)


def new_cache_root(tmp_path) -> Path:
    root = Path(tmp_path) / 'uecache'
    owner_only_acl.create_owner_only_dir(root)
    return root


def big_disk(free=500 * GIB):
    return lambda _path: DiskUsage(free * 2, free, free)


def make_cache(tmp_path, *, cache_max=100 * GIB, min_free=1 * GIB, disk_usage=None) -> ArchiveCache:
    layout = CacheLayout(new_cache_root(tmp_path))
    layout.ensure_children()
    return ArchiveCache(layout, CacheLimits(cache_max, min_free), disk_usage=disk_usage or big_disk())


def limits(**overrides) -> PipelineLimits:
    values = dict(max_archive_bytes=10 * GIB, max_uncompressed_bytes=10 * GIB, cache_max_bytes=100 * GIB,
                  min_free_bytes=GIB, copy_stall_seconds=5.0, workcopy_mode='hardlink')
    values.update(overrides)
    return PipelineLimits(**values)


def local_fetch(cache, zip_path, pin, *, counter=None, **kwargs):
    """A ``fetch`` for ``populate.obtain_entry`` that copies a local file as the share."""
    def fetch(abort):
        if counter is not None:
            counter.append(zip_path)
        return share_copy.copy_archive(
            str(zip_path), cache.layout.downloads, pin, abort, max_bytes=kwargs.get('max_bytes', 10 * GIB),
            stall_seconds=kwargs.get('stall_seconds', 5.0), reserve=lambda n: eviction.reserve(cache, n),
            share_validator=lambda p: str(p), poll_seconds=0.05)
    return fetch


def spec_digest(root) -> str:
    """The FR-MU-5 overlay digest formula, for tests that run before chunk 2 lands."""
    rows = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            rows.append([rel, os.path.getsize(full), sha256_file(full)])
    rows.sort(key=lambda row: row[0])
    payload = json.dumps(rows, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


class AlwaysReachable:
    def __init__(self):
        self.calls = []

    def check(self, share_root, relpath, kind, **kwargs):
        self.calls.append((relpath, kind))
        return True


def local_resolver(share_dir):
    """Map share relpaths onto a local directory standing in for the share."""
    return lambda root, relpath, kind: str(Path(share_dir).joinpath(*relpath.split('/')))
