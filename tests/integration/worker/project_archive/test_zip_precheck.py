# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
EOCD pre-check (Unreal foundation FR-PA-6 step 0, FR-T-6, AC-10).
"""
import tracemalloc
import zipfile

import pytest

from sethlans_worker_agent.project_archive import safe_extract, zip_precheck
from sethlans_worker_agent.project_archive.errors import UnsafeArchiveError

from ._helpers import make_zip, patch_bytes, write_eocd, write_zip64_eocd


def test_valid_archive_passes(tmp_path):
    path = tmp_path / 'ok.zip'
    make_zip(path, dirs=('Demo/', 'Demo/Content/'))
    result = zip_precheck.precheck(path)
    with zipfile.ZipFile(path) as zf:
        assert result.entries == len(zf.infolist())
        assert result.cd_offset == zf.start_dir
    assert result.zip64 is False


def test_zip64_archive_passes(tmp_path):
    path = tmp_path / 'z64.zip'
    with zipfile.ZipFile(path, 'w') as zf:
        with zf.open('Demo/a.bin', 'w', force_zip64=True) as handle:
            handle.write(b'x' * 10)
    assert zip_precheck.precheck(path).entries == 1


def test_ten_million_entries_rejected_before_zipfile(tmp_path, monkeypatch):
    path = tmp_path / 'bomb.zip'
    write_zip64_eocd(path, entries=10_000_000)

    def no_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed")

    monkeypatch.setattr(safe_extract.zipfile, 'ZipFile', no_zipfile)
    tracemalloc.start()
    try:
        with pytest.raises(UnsafeArchiveError, match='10000000 entries'):
            safe_extract.OpenedArchive(path, max_uncompressed=1 << 40)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024


def test_hundred_mib_central_directory_rejected(tmp_path):
    path = tmp_path / 'cd.zip'
    write_eocd(path, entries=1, cd_size=100 * 1024 * 1024)
    with pytest.raises(UnsafeArchiveError, match='central directory'):
        zip_precheck.precheck(path)


def test_directory_beyond_record_rejected(tmp_path):
    path = tmp_path / 'bad.zip'
    write_eocd(path, entries=1, cd_size=46, cd_offset=1000)
    with pytest.raises(UnsafeArchiveError, match='inconsistent'):
        zip_precheck.precheck(path)


def test_more_headers_than_declared_rejected(tmp_path):
    path = tmp_path / 'count.zip'
    make_zip(path, files={'Demo/a.txt': b'a', 'Demo/b.txt': b'b'})
    data = path.read_bytes()
    eocd = data.rfind(b'PK\x05\x06')
    patch_bytes(path, eocd + 8, (1).to_bytes(2, 'little') * 2)  # declare 1 entry, 2 present
    with pytest.raises(UnsafeArchiveError, match='declares 1'):
        zip_precheck.precheck(path)


def test_not_a_zip_rejected(tmp_path):
    path = tmp_path / 'junk.zip'
    path.write_bytes(b'not a zip at all' * 4)
    with pytest.raises(UnsafeArchiveError):
        zip_precheck.precheck(path)
