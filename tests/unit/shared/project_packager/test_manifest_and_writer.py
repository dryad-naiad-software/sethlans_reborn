# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manifest canonical form, member order and fixed member attributes
(Unreal foundation FR-PK-6, FR-PK-7, FR-PK-8).
"""
import hashlib
import zipfile

from shared.project_packager import manifest, writer
from shared.project_packager.model import ScanResult, SourceFile


def _files():
    return [manifest.ManifestFile('D/Content/b', 2, 'b' * 64), manifest.ManifestFile('D/Config/a', 1, 'a' * 64)]


def test_canonical_json_is_sorted_and_compact():
    assert manifest.canonical_json({'b': 1, 'a': [{'z': 'é', 'y': 2}]}) == '{"a":[{"y":2,"z":"é"}],"b":1}'.encode()


def test_content_hash_ignores_input_order():
    files = _files()
    expected = hashlib.sha256(manifest.canonical_json(
        [{'path': 'D/Config/a', 'sha256': 'a' * 64, 'size': 1},
         {'path': 'D/Content/b', 'sha256': 'b' * 64, 'size': 2}])).hexdigest()
    assert manifest.content_sha256(files) == manifest.content_sha256(files[::-1]) == expected


def test_manifest_round_trips_and_derives_counts():
    built = manifest.build_manifest('D', _files(), {'source': False, 'cpp_modules': False, 'plugins': False}, ['w'])
    assert [item.path for item in built.files] == ['D/Config/a', 'D/Content/b']
    assert built.uproject == 'D/D.uproject' and built.file_count == 2 and built.total_bytes == 3
    data = built.to_json_bytes()
    assert list(manifest.json.loads(data)) == sorted(manifest.json.loads(data))
    assert manifest.parse_manifest(data) == built


def _source(rel):
    return SourceFile(relpath=rel, size=1, ino=1, dev=1, mtime_ns=1)


def test_members_sort_by_path_with_directories_before_their_contents():
    rels = ['Content/A-b.uasset', 'Content/A/x.uasset', 'D.uproject', 'Config/x.ini', 'Content/a.uasset']
    result = ScanResult(project_name='D', files=[(r, 1) for r in rels], total_bytes=5, excluded_top_level=[],
                        detected_features={}, warnings=[], dirs=['Config', 'Content', 'Content/A'],
                        sources=tuple(_source(r) for r in sorted(rels)))
    names = [member.name for member in writer.plan_members(result)]
    assert names == ['D/', 'D/Config/', 'D/Config/x.ini', 'D/Content/', 'D/Content/A/', 'D/Content/A-b.uasset',
                     'D/Content/A/x.uasset', 'D/Content/a.uasset', 'D/D.uproject']
    for index, name in enumerate(names):
        parent = name.rstrip('/').rsplit('/', 1)[0] + '/'
        if parent != name and '/' in name.rstrip('/'):
            assert names.index(parent) < index


def test_file_info_attributes():
    deflated = writer.file_info('D/x', 10, stored=False)
    assert (deflated.date_time, deflated.create_system, deflated.external_attr) == (
        (1980, 1, 1, 0, 0, 0), 3, 0o100644 << 16)
    assert deflated.compress_type == zipfile.ZIP_DEFLATED and deflated.compress_level == 6
    stored = writer.file_info('D/x', 10, stored=True)
    assert stored.compress_type == zipfile.ZIP_STORED and stored.file_size == 10
