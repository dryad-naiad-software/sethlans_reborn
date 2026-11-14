# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Packager limits (Unreal foundation FR-PK-13): the file and byte caps, and
the worker's entry and central-directory limits, which ``scan()`` warns
about and ``package()`` enforces.
"""
import pytest

from shared.project_packager import PackagerError, PackagerOptions, package, rules, scan

from . import _helpers as h

ENTRIES = len(h.INCLUDED) + 6 + 1  # files + directories (with <Name>/) + manifest


@pytest.fixture()
def project(tmp_path):
    return h.make_project(tmp_path)


@pytest.fixture()
def out(tmp_path):
    directory = tmp_path / 'out'
    directory.mkdir()
    return directory / 'demo.zip'


def test_entry_count_counts_directories_and_the_manifest(project):
    assert scan(project).archive_entry_count == ENTRIES


def test_too_many_entries_warn_on_scan_and_fail_on_package(project, out, monkeypatch):
    monkeypatch.setattr(rules, 'WORKER_MAX_ENTRIES', ENTRIES - 1)
    result = scan(project)
    assert any('workers accept at most' in warning for warning in result.warnings)
    with pytest.raises(PackagerError, match='workers accept at most'):
        package(project, out)
    assert not out.exists() and h.partials(out.parent) == []


def test_exactly_the_worker_entry_limit_is_accepted(project, out, monkeypatch):
    monkeypatch.setattr(rules, 'WORKER_MAX_ENTRIES', ENTRIES)
    assert scan(project).warnings == []
    package(project, out)


def test_a_central_directory_over_the_worker_limit_fails_on_package(project, out, monkeypatch):
    monkeypatch.setattr(rules, 'WORKER_MAX_CD_BYTES', 100)
    assert any('64 MiB' in warning for warning in scan(project).warnings)
    with pytest.raises(PackagerError, match='file list'):
        package(project, out)


def test_max_total_bytes(project):
    total = sum(len(data) for data in h.INCLUDED.values())
    assert scan(project, PackagerOptions(max_total_bytes=total)).total_bytes == total
    with pytest.raises(PackagerError, match='exceed'):
        scan(project, PackagerOptions(max_total_bytes=total - 1))


def test_max_files(project, monkeypatch):
    monkeypatch.setattr(rules, 'MAX_FILES', len(h.INCLUDED) - 1)
    with pytest.raises(PackagerError, match='more than'):
        scan(project)


def test_max_depth(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'MAX_DEPTH', 2)
    root = tmp_path / 'src' / 'Demo'
    h.write_tree(root, {'Demo.uproject': b'{}', 'Content/a/b/x.uasset': b'x'})
    scan(root)
    h.write_tree(root, {'Content/a/b/c/x.uasset': b'x'})
    with pytest.raises(PackagerError, match='nested deeper'):
        scan(root)


def test_name_errors_list_at_most_50_paths(tmp_path):
    bad = {f'Content/n~{i}.uasset': b'x' for i in range(55)}
    project = h.make_project(tmp_path, extra=bad)
    with pytest.raises(PackagerError, match=r'\(and 5 more\)') as info:
        scan(project)
    assert len(info.value.paths) == 50 and set(info.value.paths) <= set(bad)
