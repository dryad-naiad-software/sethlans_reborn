# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Packager -> worker round trip (Unreal foundation FR-PK-9, FR-PK-14,
FR-T-6 "Round trip", AC-12): ``package()`` output passes the worker's
EOCD pre-check, its central-directory pre-scan, ``safe_extract`` and
working-copy materialization unchanged.
"""
import hashlib
import sys
import zipfile

import pytest

from sethlans_worker_agent.project_archive import (
    eviction, populate, safe_extract, working_copy, zip_precheck, zip_scan,
)
from sethlans_worker_agent.project_archive.abort import Abort
from shared.project_packager import MANIFEST_NAME, PackagerError, package, scan
from tests.integration.worker.project_archive import _helpers as worker_h

from . import _helpers as h

GIB = 1024 ** 3


def _package(tmp_path, project):
    out_dir = tmp_path / 'out'
    out_dir.mkdir(exist_ok=True)
    return package(project, out_dir / 'demo.zip')


def _assert_extracts(tmp_path, result):
    checked = zip_precheck.precheck(result.out_path)
    with zipfile.ZipFile(result.out_path) as zf:
        assert checked.entries == len(zf.infolist())
        zip_scan.scan(zf, 10 * GIB)
    dest = tmp_path / 'extract'
    dest.mkdir()
    extracted = safe_extract.extract_zip(result.out_path, dest, 10 * GIB)
    by_path = {record.relpath: record for record in extracted.files}
    for item in result.manifest.files:
        assert by_path[item.path].sha256 == item.sha256 and by_path[item.path].size == item.size
    assert set(by_path) == {item.path for item in result.manifest.files} | {MANIFEST_NAME}
    return dest


def test_package_output_passes_the_worker_extraction_rules(tmp_path):
    project = h.make_project(tmp_path)
    result = _package(tmp_path, project)
    dest = _assert_extracts(tmp_path, result)
    for rel, data in h.INCLUDED.items():
        assert dest.joinpath(h.NAME, *rel.split('/')).read_bytes() == data
    assert dest.joinpath(h.NAME, 'Content', 'Empty').is_dir()


def test_compressible_content_still_passes_the_worker_ratio_limits(tmp_path):
    extra = {'Content/Zero.uasset': bytes(5 * 1024 * 1024), 'Content/Small.uasset': bytes(900_000)}
    result = _package(tmp_path, h.make_project(tmp_path, extra=extra))
    _assert_extracts(tmp_path, result)


@pytest.mark.parametrize('name', ['Big~1.uasset', 'Hero.uasset.'] if sys.platform != 'win32' else ['Big~1.uasset'])
def test_a_name_the_worker_would_reject_is_refused_before_writing(tmp_path, name):
    project = h.make_project(tmp_path, extra={f'Content/{name}': b'x'})
    with pytest.raises(PackagerError, match='cannot be packaged for Windows workers') as info:
        scan(project)
    assert info.value.paths == (f'Content/{name}',)


def _case_sensitive(directory) -> bool:
    probe = directory / 'case-probe'
    probe.write_bytes(b'')
    try:
        return not (directory / 'CASE-PROBE').exists()
    finally:
        probe.unlink()


@pytest.mark.skipif(sys.platform == 'win32', reason="needs a case-sensitive filesystem")
def test_a_case_collision_is_refused(tmp_path):
    # macOS APFS is case-insensitive by default: the second file would just
    # overwrite the first, so there is no collision to detect.
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive filesystem")
    project = h.make_project(tmp_path, extra={'Content/hero.uasset': b'x', 'Content/HERO.uasset': b'y'})
    with pytest.raises(PackagerError, match='letter case'):
        scan(project)


def test_package_output_materializes_a_working_copy(tmp_path):
    result = _package(tmp_path, h.make_project(tmp_path))
    pin = result.archive_sha256
    cache = worker_h.make_cache(tmp_path)
    fetch = worker_h.local_fetch(cache, result.out_path, pin)
    acquired = populate.obtain_entry(cache, pin, fetch, Abort(), 10 * GIB)
    try:
        copy = working_copy.materialize(
            cache.layout, acquired.tree, result.manifest.uproject, None, sha256=acquired.sha256,
            overlay_sha256='', reserve=lambda n: eviction.reserve(cache, n))
        assert copy.uproject.name == f'{h.NAME}.uproject' and copy.files == len(h.INCLUDED)
        for rel, data in h.INCLUDED.items():
            on_disk = copy.project_dir.joinpath(*rel.split('/')).read_bytes()
            assert hashlib.sha256(on_disk).digest() == hashlib.sha256(data).digest()
    finally:
        cache.release(pin)


def test_a_total_ratio_above_100_from_small_files_is_rewritten_and_extracts(tmp_path):
    """Every file is under 1 MiB (no per-entry violation) but the total ratio exceeds 100."""
    root = tmp_path / 'src' / 'Demo'
    files = {'Demo.uproject': b'{"FileVersion": 3}', 'Config/DefaultEngine.ini': b'[x]\n'}
    files.update({f'Content/Zero{i:02}.uasset': bytes(900_000) for i in range(40)})
    h.write_tree(root, files)
    result = _package(tmp_path, root)
    with zipfile.ZipFile(result.out_path) as zf:
        infos = [zf.getinfo(f'Demo/Content/Zero{i:02}.uasset') for i in range(40)]
        assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
        assert all(info.file_size < 1024 * 1024 for info in infos)
    _assert_extracts(tmp_path, result)
