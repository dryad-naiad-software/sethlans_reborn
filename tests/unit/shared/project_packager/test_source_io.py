# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Link and placeholder refusal, open-failure mapping and output-location
rules (Unreal foundation FR-PK-5, FR-PK-10).
"""
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from shared.project_packager import PackagerError, SourceChangedError, api, source_io


def _root(tmp_path) -> str:
    return source_io.fs_path(str(tmp_path.resolve()))


def _fake_stat(mode=stat.S_IFREG | 0o644, attrs=0, ino=1, size=5, mtime_ns=7):
    return SimpleNamespace(st_mode=mode, st_file_attributes=attrs, st_ino=ino, st_dev=2, st_size=size,
                           st_mtime_ns=mtime_ns)


@pytest.mark.parametrize('attrs, label', [
    (source_io.FILE_ATTRIBUTE_REPARSE_POINT, 'reparse'),
    (source_io.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, 'cloud placeholder'),
    (source_io.FILE_ATTRIBUTE_RECALL_ON_OPEN, 'cloud placeholder'),
    (source_io.FILE_ATTRIBUTE_OFFLINE, 'cloud placeholder'),
])
def test_reparse_points_and_cloud_placeholders_are_refused(monkeypatch, attrs, label):
    monkeypatch.setattr(source_io, '_lstat', lambda _path, _dir_fd=None: _fake_stat(attrs=attrs))
    with pytest.raises(PackagerError, match=label) as info:
        source_io.lstat_checked('x', 'Content/x.uasset')
    assert info.value.paths == ('Content/x.uasset',)


@pytest.mark.parametrize('mode, label', [(stat.S_IFLNK | 0o777, 'symbolic link'),
                                         (stat.S_IFIFO | 0o644, 'not a regular file')])
def test_links_and_special_files_are_refused(monkeypatch, mode, label):
    monkeypatch.setattr(source_io, '_lstat', lambda _path, _dir_fd=None: _fake_stat(mode=mode))
    with pytest.raises(PackagerError, match=label):
        source_io.lstat_checked('x', 'Content/x')


def test_a_failed_open_of_a_vanished_file_is_a_change(tmp_path):
    with pytest.raises(SourceChangedError, match='disappeared'):
        source_io.stream_file(str(tmp_path.resolve()), 'gone', (1, 2, 3, 4), lambda chunk: None)


def test_a_failed_open_of_an_unchanged_file_is_a_read_error(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    path.write_bytes(b'abc')
    expected = source_io.identity(os.lstat(path))

    def denied(_path):
        raise PermissionError(13, 'Permission denied')
    monkeypatch.setattr(source_io, '_open', lambda _root, _rel: denied(_rel))
    with pytest.raises(PackagerError, match='cannot read') as info:
        source_io.stream_file(_root(tmp_path), 'f', expected, lambda chunk: None)
    assert not isinstance(info.value, SourceChangedError)


def test_stream_file_reads_the_scanned_file(tmp_path):
    path = tmp_path / 'f'
    path.write_bytes(b'abc' * 1000)
    chunks = []
    size = source_io.stream_file(_root(tmp_path), 'f', source_io.identity(os.lstat(path)), chunks.append)
    assert size == 3000 and b''.join(chunks) == b'abc' * 1000


def test_a_stale_identity_is_refused_after_the_open(tmp_path):
    path = tmp_path / 'f'
    path.write_bytes(b'abc')
    ino, dev, size, mtime = source_io.identity(os.lstat(path))
    with pytest.raises(SourceChangedError, match='not the file that was scanned'):
        source_io.stream_file(_root(tmp_path), 'f', (ino + 1, dev, size, mtime), lambda chunk: None)


@pytest.mark.skipif(sys.platform != 'win32', reason="long-path prefix is Windows-only")
def test_long_path_prefix():
    assert source_io.fs_path('C:\\p\\x') == '\\\\?\\C:\\p\\x'
    assert source_io.fs_path('\\\\nas\\share\\x') == '\\\\?\\UNC\\nas\\share\\x'
    assert source_io.fs_path('\\\\?\\C:\\x') == '\\\\?\\C:\\x'
    assert source_io.fs_path('relative') == 'relative'


def test_output_location_rules(tmp_path):
    project = tmp_path / 'Demo'
    project.mkdir()
    for inside in (project, project / 'x.zip', project / 'Saved' / 'x.zip'):
        with pytest.raises(PackagerError, match='outside the project directory'):
            api.check_output_location(project, inside)
    api.check_output_location(project, tmp_path / 'Demo.zip')
    api.check_output_location(project, tmp_path / 'Demo2' / 'x.zip')  # a sibling sharing the prefix


@pytest.mark.skipif(sys.platform != 'win32', reason="final-path checks are the Windows containment test")
def test_a_directory_whose_final_path_is_elsewhere_is_refused(tmp_path, monkeypatch):
    (tmp_path / 'Content').mkdir()
    monkeypatch.setattr(source_io._win, 'final_path', lambda _handle: r'\\?\C:\Users\someone\profile')
    with pytest.raises(SourceChangedError, match='not inside the project directory'):
        with source_io.opened_dir(_root(tmp_path), 'Content'):
            pass


@pytest.mark.skipif(sys.platform != 'win32', reason="final-path checks are the Windows containment test")
def test_a_file_whose_final_path_is_elsewhere_is_refused(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    path.write_bytes(b'abc')
    monkeypatch.setattr(source_io._win, 'fd_final_path', lambda _fd: r'\\?\C:\elsewhere\f')
    with pytest.raises(SourceChangedError, match='not inside the project directory'):
        source_io.stream_file(_root(tmp_path), 'f', source_io.identity(os.lstat(path)), lambda chunk: None)


@pytest.mark.skipif(sys.platform != 'win32', reason="Windows path forms")
def test_same_path_ignores_case_and_a_trailing_separator():
    win = source_io._win
    assert win.same_path(r'\\?\C:\Proj\Content' + '\\', r'\\?\c:\proj\content')
    assert win.same_path(r'\\?\UNC\nas\share\P', r'\\?\unc\NAS\share\p')
    assert not win.same_path(r'\\?\C:\Proj2', r'\\?\C:\Proj')
