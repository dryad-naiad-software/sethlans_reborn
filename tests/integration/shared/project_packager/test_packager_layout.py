# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Packager allowlist, layout, manifest and determinism (Unreal foundation
FR-PK-1..3, FR-PK-6..8, FR-PK-12, FR-T-6 "Packager", AC-12).
"""
import hashlib
import json
import zipfile

from shared.project_packager import MANIFEST_NAME, package, scan
from shared.project_packager.manifest import canonical_json, parse_manifest

from . import _helpers as h


def _package(tmp_path, project, name='out.zip', **kwargs):
    out_dir = tmp_path / 'out'
    out_dir.mkdir(exist_ok=True)
    return package(project, out_dir / name, **kwargs)


def test_scan_reports_the_allowlist_and_the_excluded_root_entries(tmp_path):
    project = h.make_project(tmp_path)
    result = scan(project)
    assert result.project_name == h.NAME
    assert [rel for rel, _size in result.files] == sorted(h.INCLUDED)
    assert dict(result.files) == {rel: len(data) for rel, data in h.INCLUDED.items()}
    assert result.total_bytes == sum(len(data) for data in h.INCLUDED.values())
    assert result.excluded_top_level == h.EXCLUDED_TOP_LEVEL
    assert result.dirs == ['Config', 'Content', 'Content/Chars', 'Content/Empty', 'Content/Maps']
    assert result.detected_features == {'source': False, 'plugins': False, 'cpp_modules': False}
    assert result.warnings == []
    assert result.file_count == len(h.INCLUDED) and result.dir_count == 6


def test_archive_contains_exactly_the_allowlist_in_the_poc_layout(tmp_path):
    project = h.make_project(tmp_path)
    result = _package(tmp_path, project)
    with zipfile.ZipFile(result.out_path) as zf:
        names = [info.filename for info in zf.infolist()]
        assert names == h.expected_names()
        for rel, data in h.INCLUDED.items():
            assert zf.read(f'{h.NAME}/{rel}') == data
    assert not any('/.' in name or name.startswith('.') for name in names)


def test_member_attributes_are_fixed(tmp_path):
    result = _package(tmp_path, h.make_project(tmp_path))
    with zipfile.ZipFile(result.out_path) as zf:
        for info in zf.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            if info.is_dir():
                assert info.external_attr == (0o040755 << 16) | 0x10
                assert info.compress_type == zipfile.ZIP_STORED and info.file_size == 0
            else:
                assert info.external_attr == 0o100644 << 16
                assert info.compress_type == zipfile.ZIP_DEFLATED
        manifest = parse_manifest(zf.read(MANIFEST_NAME))
        assert zf.comment == f'sethlans-manifest-sha256:{manifest.content_sha256}'.encode('ascii')


def test_manifest_lists_every_file_with_its_hash(tmp_path):
    result = _package(tmp_path, h.make_project(tmp_path))
    with zipfile.ZipFile(result.out_path) as zf:
        raw = zf.read(MANIFEST_NAME)
    doc = json.loads(raw)
    assert raw == canonical_json(doc)  # canonical: sorted keys, no whitespace
    assert doc['schema'] == 1 and doc['packager_version'] == '1'
    assert doc['project_name'] == h.NAME and doc['uproject'] == f'{h.NAME}/{h.NAME}.uproject'
    expected = sorted(
        ({'path': f'{h.NAME}/{rel}', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
         for rel, data in h.INCLUDED.items()), key=lambda item: item['path'])
    assert doc['files'] == expected
    assert doc['file_count'] == len(expected) and doc['total_bytes'] == sum(i['size'] for i in expected)
    assert doc['content_sha256'] == hashlib.sha256(canonical_json(expected)).hexdigest()
    assert result.manifest.to_dict() == doc


def test_two_runs_are_byte_identical_and_the_source_is_untouched(tmp_path):
    project = h.make_project(tmp_path)
    before = h.snapshot(project)
    first = _package(tmp_path, project, 'a.zip')
    second = _package(tmp_path, project, 'b.zip')
    assert first.out_path.read_bytes() == second.out_path.read_bytes()
    assert first.archive_sha256 == second.archive_sha256 == h.sha256_file(first.out_path)
    assert first.archive_size == first.out_path.stat().st_size
    assert first.manifest == second.manifest
    assert h.snapshot(project) == before
    assert h.partials(tmp_path / 'out') == []


def test_content_hash_does_not_depend_on_the_project_location(tmp_path):
    first = _package(tmp_path / 'one', h.make_project(tmp_path / 'one'))
    second = _package(tmp_path / 'two', h.make_project(tmp_path / 'two'))
    assert first.archive_sha256 == second.archive_sha256
    assert first.manifest.content_sha256 == second.manifest.content_sha256


def test_highly_compressible_files_are_stored_so_the_worker_accepts_them(tmp_path):
    zeros = bytes(3 * 1024 * 1024)
    project = h.make_project(tmp_path, extra={'Content/Zero.uasset': zeros})
    first = _package(tmp_path, project, 'a.zip')
    second = _package(tmp_path, project, 'b.zip')
    assert first.out_path.read_bytes() == second.out_path.read_bytes()
    with zipfile.ZipFile(first.out_path) as zf:
        info = zf.getinfo(f'{h.NAME}/Content/Zero.uasset')
        assert info.compress_type == zipfile.ZIP_STORED and zf.read(info) == zeros
        assert zf.getinfo(f'{h.NAME}/Content/Maps/Main.umap').compress_type == zipfile.ZIP_DEFLATED


def test_progress_reaches_the_total(tmp_path):
    calls = []
    result = _package(tmp_path, h.make_project(tmp_path), progress=lambda done, total: calls.append((done, total)))
    total = sum(len(data) for data in h.INCLUDED.values())
    assert calls and calls[-1] == (total, total)
    assert [done for done, _ in calls] == sorted(done for done, _ in calls)
    assert result.manifest.total_bytes == total


def test_a_differently_spelled_root_directory_is_included_with_a_warning(tmp_path):
    root = tmp_path / 'src' / 'Demo'
    h.write_tree(root, {'Demo.uproject': b'{}', 'content/A.uasset': b'a', 'Config/DefaultEngine.ini': b''})
    result = scan(root)
    assert ('content/A.uasset', 1) in result.files and result.excluded_top_level == []
    assert result.warnings == ['content/ is spelled differently from the usual Content/']


def test_a_missing_content_directory_is_a_warning(tmp_path):
    root = tmp_path / 'src' / 'Demo'
    h.write_tree(root, {'Demo.uproject': b'{}', 'Config/DefaultEngine.ini': b''})
    assert scan(root).warnings == ['the project has no Content/ directory']
