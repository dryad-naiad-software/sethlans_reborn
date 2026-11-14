# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Packager read-only and link safety, output rules and project detection
(Unreal foundation FR-PK-2, FR-PK-4, FR-PK-5, FR-PK-10, FR-PK-11,
FR-T-6 "Packager", AC-12).
"""
import os
import sys
import threading

import pytest

from shared.project_packager import (
    EXTENSION_DIRS, ExtensionRule, PackagerError, PackagerOptions, PackagingCancelled, SourceChangedError,
    UnsupportedProjectFeature, package, scan,
)
from shared.project_packager import scanner, source_io

from . import _helpers as h

WINDOWS = sys.platform == 'win32'
MAP = 'Content/Maps/Main.umap'


@pytest.fixture()
def project(tmp_path):
    return h.make_project(tmp_path)


@pytest.fixture()
def out(tmp_path):
    directory = tmp_path / 'out'
    directory.mkdir()
    return directory / 'demo.zip'


def _swap_after_scan(monkeypatch, swap):
    real = scanner.scan

    def scan_then_swap(*args, **kwargs):
        result = real(*args, **kwargs)
        swap()
        return result
    monkeypatch.setattr(scanner, 'scan', scan_then_swap)


def _junction(target, link):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def _symlink_or_skip(target, link, **kwargs):
    try:
        os.symlink(target, link, **kwargs)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here ({exc})")


def test_output_inside_the_source_is_refused(project):
    for target in (project / 'demo.zip', project / 'Saved' / 'demo.zip', project):
        with pytest.raises(PackagerError, match='outside the project directory'):
            package(project, target)
    assert not (project / 'demo.zip').exists() and not (project / 'Saved' / 'demo.zip').exists()


@pytest.mark.skipif(not WINDOWS, reason="case-insensitive paths are a Windows behavior")
def test_output_inside_the_source_through_a_different_case_is_refused(project):
    with pytest.raises(PackagerError, match='outside the project directory'):
        package(project, str(project).upper() + '\\SAVED\\demo.zip')


def test_existing_output_needs_overwrite(project, out):
    out.write_bytes(b'old')
    with pytest.raises(PackagerError, match='already exists'):
        package(project, out)
    assert out.read_bytes() == b'old'
    result = package(project, out, PackagerOptions(overwrite=True))
    assert h.sha256_file(out) == result.archive_sha256 and h.partials(out.parent) == []


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_junction_under_content_is_refused(tmp_path, project, out):
    (tmp_path / 'profile').mkdir()
    (tmp_path / 'profile' / 'secret.txt').write_bytes(b'secret')
    _junction(tmp_path / 'profile', project / 'Content' / 'Linked')
    with pytest.raises(PackagerError, match='junction') as info:
        package(project, out)
    assert info.value.paths == ('Content/Linked',)
    assert not out.exists() and h.partials(out.parent) == []


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_junction_as_the_content_root_is_refused(tmp_path, project):
    os.rename(project / 'Content', tmp_path / 'moved')
    _junction(tmp_path / 'moved', project / 'Content')
    with pytest.raises(PackagerError, match='junction'):
        scan(project)


def test_a_symlink_under_content_is_refused(tmp_path, project):
    (tmp_path / 'secret.txt').write_bytes(b'secret')
    _symlink_or_skip(tmp_path / 'secret.txt', project / 'Content' / 'secret.uasset')
    with pytest.raises(PackagerError, match='symbolic link'):
        scan(project)


def test_a_file_replaced_between_scan_and_open_is_refused(tmp_path, project, out, monkeypatch):
    target = project.joinpath(*MAP.split('/'))
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'X' * target.stat().st_size)

    def swap():
        os.replace(replacement, target)
    _swap_after_scan(monkeypatch, swap)
    with pytest.raises(SourceChangedError) as info:
        package(project, out)
    assert info.value.relpath == MAP
    assert not out.exists() and h.partials(out.parent) == []


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_file_swapped_for_a_junction_between_scan_and_open_is_refused(tmp_path, project, out, monkeypatch):
    target = project.joinpath(*MAP.split('/'))
    (tmp_path / 'profile').mkdir()

    def swap():
        target.unlink()
        _junction(tmp_path / 'profile', target)
    _swap_after_scan(monkeypatch, swap)
    with pytest.raises(SourceChangedError):
        package(project, out)
    assert not out.exists() and h.partials(out.parent) == []


@pytest.mark.skipif(WINDOWS, reason="POSIX symlink swap; Windows uses the junction case")
def test_a_file_swapped_for_a_symlink_between_scan_and_open_is_refused(tmp_path, project, out, monkeypatch):
    target = project.joinpath(*MAP.split('/'))
    other = tmp_path / 'other'
    other.write_bytes(target.read_bytes())

    def swap():
        target.unlink()
        os.symlink(other, target)
    _swap_after_scan(monkeypatch, swap)
    with pytest.raises(SourceChangedError):
        package(project, out)


def test_a_source_changed_mid_read_is_refused(project, out, monkeypatch):
    real = source_io.stream_file

    def touching(root_fs, relpath, expected, on_chunk):
        def feed(chunk):
            on_chunk(chunk)
            if relpath == MAP:
                os.utime(source_io.join(root_fs, relpath), ns=(expected[3] + 10**9, expected[3] + 10**9))
        return real(root_fs, relpath, expected, feed)
    monkeypatch.setattr(source_io, 'stream_file', touching)
    with pytest.raises(SourceChangedError, match='modification time'):
        package(project, out)
    assert not out.exists() and h.partials(out.parent) == []


def test_a_source_growing_mid_read_is_refused(project, out, monkeypatch):
    real = source_io.stream_file
    monkeypatch.setattr(source_io, 'CHUNK', 64 * 1024)

    def growing(root_fs, relpath, expected, on_chunk):
        def feed(chunk):
            on_chunk(chunk)
            if relpath == MAP:
                with open(source_io.join(root_fs, relpath), 'ab') as handle:
                    handle.write(b'more')
        return real(root_fs, relpath, expected, feed)
    monkeypatch.setattr(source_io, 'stream_file', growing)
    with pytest.raises(SourceChangedError):
        package(project, out)


def test_cancel_deletes_the_partial(project, out):
    cancel = threading.Event()
    with pytest.raises(PackagingCancelled):
        package(project, out, progress=lambda done, total: cancel.set(), cancel=cancel)
    assert not out.exists() and h.partials(out.parent) == []


@pytest.mark.parametrize('names, found', [((), 0), (('A.uproject', 'B.uproject'), 2)])
def test_zero_or_several_uprojects_are_refused(tmp_path, names, found):
    root = tmp_path / 'src' / 'P'
    h.write_tree(root, {name: b'{}' for name in names} | {'Content/a.uasset': b'a'})
    with pytest.raises(PackagerError, match=f'exactly one .uproject file at its root \\(found {found}'):
        scan(root)


@pytest.mark.parametrize('name', ['1Demo.uproject', 'My-Demo.uproject', 'Dé.uproject', 'A' * 65 + '.uproject'])
def test_an_invalid_project_name_is_refused(tmp_path, name):
    root = tmp_path / 'src' / 'P'
    h.write_tree(root, {name: b'{}'})
    with pytest.raises(PackagerError, match='project name'):
        scan(root)


def test_an_invalid_uproject_json_is_refused(tmp_path):
    root = tmp_path / 'src' / 'P'
    h.write_tree(root, {'Demo.uproject': b'{not json'})
    with pytest.raises(PackagerError, match='not valid JSON'):
        scan(root)


@pytest.mark.parametrize('extra, feature', [
    ({'Source/Demo/Demo.cpp': b'//'}, 'source'),
    ({'Plugins/P/P.uplugin': b'{}'}, 'plugins'),
])
def test_source_or_plugins_fail_closed(tmp_path, out, extra, feature):
    project = h.make_project(tmp_path, extra=extra)
    assert scan(project).detected_features[feature] is True
    label = 'Source' if feature == 'source' else 'Plugins'
    with pytest.raises(UnsupportedProjectFeature, match=f"'{label}/' packaging is not supported yet"):
        package(project, out)
    assert not out.exists()
    result = package(project, out, PackagerOptions(on_unsupported='exclude'))
    assert any(label in warning for warning in result.manifest.warnings)
    assert not any(f'/{label}/' in item.path for item in result.manifest.files)


def test_cpp_modules_fail_closed(tmp_path, out):
    uproject = b'{"FileVersion": 3, "Modules": [{"Name": "ACOMDemo", "Type": "Runtime"}]}'
    project = h.make_project(tmp_path, extra={f'{h.NAME}.uproject': uproject})
    assert scan(project).detected_features['cpp_modules'] is True
    with pytest.raises(UnsupportedProjectFeature, match='C\\+\\+ modules'):
        package(project, out)


def test_empty_source_directory_is_not_a_feature(tmp_path):
    project = h.make_project(tmp_path)
    (project / 'Source').mkdir()
    assert scan(project).detected_features['source'] is False


@pytest.mark.parametrize('extra', [('Plugins',), ('Source',)])
def test_unregistered_extra_top_level_is_refused(project, extra):
    with pytest.raises(UnsupportedProjectFeature) as info:
        scan(project, PackagerOptions(extra_top_level=extra))
    assert str(info.value) == f"'{extra[0]}/' packaging is not supported yet (parked decision)"


def test_a_registered_extension_directory_is_included(tmp_path, out, monkeypatch):
    project = h.make_project(tmp_path, extra={'Movies/intro.mp4': b'mp4'})
    monkeypatch.setitem(EXTENSION_DIRS, 'Movies', ExtensionRule(description='test only'))
    result = package(project, out, PackagerOptions(extra_top_level=('Movies',)))
    assert f'{h.NAME}/Movies/intro.mp4' in [item.path for item in result.manifest.files]


def test_a_missing_output_directory_is_refused(tmp_path, project):
    with pytest.raises(PackagerError, match='does not exist'):
        package(project, tmp_path / 'missing' / 'demo.zip')
