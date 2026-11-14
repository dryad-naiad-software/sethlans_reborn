# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Containment of the packager's reads (Unreal foundation FR-PK-5, FR-PK-10;
security review of chunk 5): a directory or the project root swapped for a
junction (Windows) or symlink (POSIX) at any point of the scan or the
package run is refused, and nothing outside the project is read.
"""
import os
import sys
import zipfile

import pytest

from shared.project_packager import PackagerError, SourceChangedError, api, package, scan, scanner, source_io

from . import _helpers as h

WINDOWS = sys.platform == 'win32'
MAPS = 'Content/Maps'
SECRET = b'-----BEGIN SECRET-----'


def _link_dir(target, link):
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture()
def setup(tmp_path):
    project = h.make_project(tmp_path)
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'id_rsa').write_bytes(SECRET)
    out = tmp_path / 'out'
    out.mkdir()
    return project, profile, out / 'demo.zip'


def _assert_nothing_leaked(out):
    assert not out.exists() and h.partials(out.parent) == []


def _swap_dir_after_its_lstat(monkeypatch, project, target, tmp_path):
    """The reviewer's PoC: swap ``Content/Maps`` for a link right after the walk ``lstat``-ed it."""
    real, done = source_io.lstat_checked, []

    def racing(path, relpath, dir_fd=None):
        st = real(path, relpath) if dir_fd is None else real(path, relpath, dir_fd=dir_fd)
        if relpath == MAPS and not done:
            done.append(1)
            here = project.joinpath(*MAPS.split('/'))
            os.rename(here, tmp_path / 'maps-away')
            _link_dir(target, here)
        return st
    monkeypatch.setattr(source_io, 'lstat_checked', racing)
    return done


@pytest.mark.parametrize('call', ['scan', 'package'])
def test_a_directory_swapped_for_a_link_during_the_walk_is_refused(tmp_path, setup, monkeypatch, call):
    project, profile, out = setup
    done = _swap_dir_after_its_lstat(monkeypatch, project, profile, tmp_path)
    with pytest.raises(SourceChangedError) as info:
        scan(project) if call == 'scan' else package(project, out)
    assert done and info.value.relpath == MAPS
    _assert_nothing_leaked(out)


def test_a_directory_swapped_for_a_link_between_scan_and_open_is_refused(tmp_path, setup, monkeypatch):
    """The link points at the moved original, so every file identity still matches the scan."""
    project, _profile, out = setup
    real = scanner.scan

    def scan_then_swap(*args, **kwargs):
        result = real(*args, **kwargs)
        here = project.joinpath(*MAPS.split('/'))
        os.rename(here, tmp_path / 'maps-away')
        _link_dir(tmp_path / 'maps-away', here)
        return result
    monkeypatch.setattr(scanner, 'scan', scan_then_swap)
    with pytest.raises(SourceChangedError) as info:
        package(project, out)
    assert info.value.relpath == f'{MAPS}/Main.umap'
    if WINDOWS:
        assert 'not inside the project directory' in str(info.value)
    _assert_nothing_leaked(out)


def test_the_project_root_swapped_for_a_link_before_the_uproject_read_is_refused(tmp_path, setup, monkeypatch):
    project, _profile, _out = setup
    real = scanner._inspect_root

    def inspect_then_swap(root_fs, extras):
        top = real(root_fs, extras)
        os.rename(project, tmp_path / 'moved')
        _link_dir(tmp_path / 'moved', project)
        return top
    monkeypatch.setattr(scanner, '_inspect_root', inspect_then_swap)
    with pytest.raises(SourceChangedError) as info:
        scan(project)
    assert info.value.relpath == f'{h.NAME}.uproject'


def test_an_unswapped_project_still_packages_through_the_checks(setup):
    project, _profile, out = setup
    result = package(project, out)
    with zipfile.ZipFile(result.out_path) as zf:
        assert all(SECRET not in zf.read(name) for name in zf.namelist())


def test_an_output_whose_ancestor_is_the_root_by_identity_is_refused(setup, monkeypatch):
    """Covers aliases the lexical check cannot see (APFS case folding, SMB)."""
    project, _profile, _out = setup
    monkeypatch.setattr(api, '_lexically_inside', lambda _root, _out: False)
    with pytest.raises(PackagerError, match='outside the project directory'):
        package(project, project / 'Saved' / 'demo.zip')
    with pytest.raises(PackagerError, match='outside the project directory'):
        package(project, project / 'not-yet' / 'deeper' / 'demo.zip')
    assert api._ancestor_is_root(str(project), project.parent / 'demo.zip') is False


def test_claim_and_replace_never_clobbers(tmp_path):
    partial, out = tmp_path / 'p.partial', tmp_path / 'demo.zip'
    partial.write_bytes(b'new')
    out.write_bytes(b'theirs')
    with pytest.raises(FileExistsError):
        api._claim_and_replace(partial, out)
    assert out.read_bytes() == b'theirs' and partial.read_bytes() == b'new'
    out.unlink()
    api._claim_and_replace(partial, out)
    assert out.read_bytes() == b'new' and not partial.exists()


@pytest.mark.skipif(WINDOWS, reason="the hard-link fallback is the POSIX path")
def test_posix_publish_without_hard_links_uses_the_claim(tmp_path, monkeypatch):
    partial, out = tmp_path / 'p.partial', tmp_path / 'demo.zip'
    partial.write_bytes(b'new')

    def no_links(*_args, **_kwargs):
        raise PermissionError(1, 'Operation not permitted')
    monkeypatch.setattr(api.os, 'link', no_links)
    api._publish(partial, out, overwrite=False)
    assert out.read_bytes() == b'new' and not partial.exists()
