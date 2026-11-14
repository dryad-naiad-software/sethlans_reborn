# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reparse safety under ``CACHE_DIR`` (Unreal foundation FR-PA-21, FR-T-6
"Reparse safety"). Junctions on Windows, symlinks elsewhere.
"""
import os
import sys

import pytest

from sethlans_worker_agent.project_archive import fs_safe
from shared import owner_only_acl

from . import _helpers as h


def _dir_link(link, target):
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _file_link(link, target):
    try:
        os.symlink(target, link)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a file symlink here: {exc}")


@pytest.fixture()
def profile(tmp_path):
    home = tmp_path / 'profile'
    (home / 'Documents').mkdir(parents=True)
    (home / 'Documents' / 'thesis.docx').write_bytes(b'precious')
    return home


def test_junction_planted_in_w_is_removed_and_target_survives(tmp_path, profile):
    cache = h.make_cache(tmp_path)
    workdir = cache.layout.w / 'abc-s0' / 'Demo'
    workdir.mkdir(parents=True)
    (workdir / 'real.txt').write_bytes(b'r')
    _dir_link(workdir / 'Saved', profile)
    assert fs_safe.remove_tree(cache.layout.w / 'abc-s0') is True
    assert not (cache.layout.w / 'abc-s0').exists()
    assert (profile / 'Documents' / 'thesis.docx').read_bytes() == b'precious'


def test_link_as_the_root_is_removed_as_a_link(tmp_path, profile):
    link = tmp_path / 'link'
    _dir_link(link, profile)
    assert fs_safe.remove_tree(link) is True
    assert not os.path.lexists(link)
    assert (profile / 'Documents' / 'thesis.docx').exists()


def test_file_symlink_is_unlinked_not_followed(tmp_path, profile):
    root = tmp_path / 'tree'
    root.mkdir()
    _file_link(root / 'doc', profile / 'Documents' / 'thesis.docx')
    assert fs_safe.remove_tree(root) is True
    assert (profile / 'Documents' / 'thesis.docx').read_bytes() == b'precious'


def test_walk_refuses_reparse_points(tmp_path, profile):
    root = tmp_path / 'tree'
    (root / 'a').mkdir(parents=True)
    _dir_link(root / 'a' / 'link', profile)
    with pytest.raises(fs_safe.ReparsePointError):
        fs_safe.walk_tree(root)


def test_output_file_must_be_a_regular_file_inside_output(tmp_path, profile):
    output = tmp_path / 'j' / '42-abcdef01' / 'output'
    output.mkdir(parents=True)
    good = output / 'sethlans_42.1000.png'
    good.write_bytes(b'png')
    fs_safe.check_output_file(good, output)
    _dir_link(output / 'junction.png', profile)
    with pytest.raises(OSError):
        fs_safe.check_output_file(output / 'junction.png', output)
    outside = tmp_path / 'elsewhere.png'
    outside.write_bytes(b'x')
    with pytest.raises(OSError):
        fs_safe.check_output_file(outside, output)


def test_output_file_symlink_rejected(tmp_path, profile):
    output = tmp_path / 'output'
    output.mkdir()
    _file_link(output / 'out.png', profile / 'Documents' / 'thesis.docx')
    with pytest.raises(OSError):
        fs_safe.check_output_file(output / 'out.png', output)


def test_fixed_child_that_is_a_link_is_refused(tmp_path, profile):
    cache = h.make_cache(tmp_path)
    fs_safe.remove_tree(cache.layout.o)
    _dir_link(cache.layout.o, profile)
    with pytest.raises(Exception, match='unusable'):
        cache.layout.check_child('o')
    with pytest.raises(Exception, match='unusable'):
        cache.layout.ensure_children()


def test_hardlinked_output_file_rejected(tmp_path, profile):
    """Sec S4: a link into the pristine cache (or anywhere) is never uploaded."""
    output = tmp_path / 'output'
    output.mkdir()
    os.link(profile / 'Documents' / 'thesis.docx', output / 'out.png')
    with pytest.raises(OSError, match='hard links'):
        fs_safe.open_output_file(output / 'out.png', output)


def test_open_output_file_returns_the_checked_handle(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'out.png').write_bytes(b'png-bytes')
    with fs_safe.open_output_file(output / 'out.png', output) as handle:
        assert handle.read() == b'png-bytes'
        assert os.fstat(handle.fileno()).st_nlink == 1


def test_output_dir_that_is_a_link_rejected(tmp_path, profile):
    (profile / 'out.png').write_bytes(b'x')
    link = tmp_path / 'output'
    _dir_link(link, profile)
    with pytest.raises(OSError):
        fs_safe.open_output_file(link / 'out.png', link)


def test_output_file_swapped_after_the_check_is_rejected(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'out.png').write_bytes(b'one')
    (output / 'other.png').write_bytes(b'two')
    real_lstat = fs_safe.lstat
    monkeypatch.setattr(fs_safe, 'lstat', lambda p: real_lstat(output / 'other.png')
                        if str(p).endswith('out.png') else real_lstat(p))
    with pytest.raises(OSError, match='changed between'):
        fs_safe.open_output_file(output / 'out.png', output)


ADMINS = 'S-1-5-32-544'


@pytest.fixture()
def elevated_owner(monkeypatch):
    """GitHub #228: simulate an elevated token, under which a plain mkdir is owned by Administrators."""
    acl = owner_only_acl
    real_read = acl._win_read_security
    token = {'owner': ADMINS}

    def read(path):
        protected, owner, aces = real_read(path)
        return protected, (ADMINS if owner == acl.current_user_sid() else owner), aces

    monkeypatch.setattr(acl, '_win_read_security', read)
    monkeypatch.setattr(acl, '_win_token_owner_sid', lambda: token['owner'])
    return token


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows owners (#228)')
def test_output_checks_with_an_elevated_token(tmp_path, profile, elevated_owner, monkeypatch):
    output = tmp_path / 'j' / '42-abcdef01' / 'output'
    output.mkdir(parents=True)
    (output / 'out.png').write_bytes(b'png')
    with fs_safe.open_output_file(output / 'out.png', output) as handle:
        assert handle.read() == b'png'
    os.link(profile / 'Documents' / 'thesis.docx', output / 'linked.png')
    with pytest.raises(OSError, match='hard links'):
        fs_safe.open_output_file(output / 'linked.png', output)
    (output / 'other.png').write_bytes(b'two')
    real_lstat = fs_safe.lstat
    monkeypatch.setattr(fs_safe, 'lstat', lambda p: real_lstat(output / 'other.png')
                        if str(p).endswith('out.png') else real_lstat(p))
    with pytest.raises(OSError, match='changed between'):
        fs_safe.open_output_file(output / 'out.png', output)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows owners (#228)')
def test_administrators_owner_is_refused_for_a_non_elevated_token(tmp_path, elevated_owner):
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'out.png').write_bytes(b'png')
    elevated_owner['owner'] = owner_only_acl.current_user_sid()  # token default owner = the user
    with pytest.raises(PermissionError, match='not owned'):
        fs_safe.open_output_file(output / 'out.png', output)
