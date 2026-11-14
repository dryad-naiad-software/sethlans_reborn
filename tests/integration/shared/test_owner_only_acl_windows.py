# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Real Windows ACLs from ``shared.owner_only_acl`` (Unreal foundation FR-WC-4,
AC-24, Sec M1/N2; FR-T-6 "ACL (Windows-only)").
"""
import ctypes
import sys

import pytest

from shared import owner_only_acl as acl

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows DACLs')

ADMINS = 'S-1-5-32-544'
OICI = 0x3


def _is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@pytest.fixture()
def cache(tmp_path):
    target = tmp_path / 'uecache'
    target.mkdir()
    return target


def test_apply_gives_a_protected_two_ace_dacl_owned_by_the_token_user(cache):
    user = acl.current_user_sid()
    assert acl.verify_owner_only(cache) is False  # inherited ACL from the temp dir
    acl.apply_owner_only(cache)
    protected, owner, aces = acl._win_read_security(str(cache))
    assert protected is True and owner == user
    assert sorted(aces) == sorted([(0, OICI, user), (0, OICI, acl.SYSTEM_SID)])
    assert acl.verify_owner_only(cache) is True


def test_children_inherit_only_the_two_sids(cache):
    acl.apply_owner_only(cache)
    child = cache / 'p' / 'x' / 'tree'
    child.mkdir(parents=True)
    (child / 'file.uasset').write_bytes(b'x')
    for path in (child, child / 'file.uasset'):
        _protected, _owner, aces = acl._win_read_security(str(path))
        assert {sid for _t, _f, sid in aces} == {acl.current_user_sid(), acl.SYSTEM_SID}


def test_verify_fails_after_an_extra_ace_and_reapply_fixes_it(cache):
    user = acl.current_user_sid()
    acl.apply_owner_only(cache)
    acl._win_set_security(str(cache), acl.owner_only_sddl(user) + '(A;OICI;FA;;;BU)')
    assert acl.verify_owner_only(cache) is False
    acl.apply_owner_only(cache)
    assert acl.verify_owner_only(cache) is True


@pytest.mark.skipif(not _is_elevated(), reason='setting another owner needs an elevated token')
def test_verify_fails_with_another_owner(cache):
    user = acl.current_user_sid()
    acl.apply_owner_only(cache)
    acl._win_set_security(str(cache), f'O:{ADMINS}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)')
    assert acl.verify_owner_only(cache) is False


def test_missing_path_does_not_verify(tmp_path):
    assert acl.verify_owner_only(tmp_path / 'missing') is False
    with pytest.raises(OSError):
        acl.apply_owner_only(tmp_path / 'missing')


def test_create_owner_only_dir_is_locked_down_from_birth(tmp_path):
    target = tmp_path / 'uecache'
    assert acl.create_owner_only_dir(target) is True
    protected, owner, aces = acl._win_read_security(str(target))
    assert protected is True and owner == acl.current_user_sid()
    assert acl.verify_owner_only(target) is True
    assert acl.is_owned_by_current_user(target) is True
    assert acl.create_owner_only_dir(target) is False  # ERROR_ALREADY_EXISTS


def test_create_owner_only_dir_needs_the_parent(tmp_path):
    with pytest.raises(OSError):
        acl.create_owner_only_dir(tmp_path / 'missing' / 'uecache')
