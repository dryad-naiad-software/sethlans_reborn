# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Owner check with an elevated token (GitHub #228): an object is "owned by
this process" when its owner is the token user **or** the token's default
owner (``TokenOwner``, ``BUILTIN\\Administrators`` when elevated). The
exact owner-only ACL check is unchanged.
"""
import sys

import pytest

from shared import owner_only_acl as acl

USER = 'S-1-5-21-1111111111-2222222222-3333333333-1001'
ADMINS = 'S-1-5-32-544'
OTHER = 'S-1-5-21-1111111111-2222222222-3333333333-1002'
OICI = 0x3
GOOD_ACES = [(0, OICI, USER), (0, OICI, acl.SYSTEM_SID)]


@pytest.fixture()
def elevated(monkeypatch):
    """A fake elevated token: user USER, default owner Administrators."""
    state = {'owner': USER, 'token_owner': ADMINS}
    monkeypatch.setattr(acl, '_is_windows', lambda: True)
    monkeypatch.setattr(acl, '_win_token_user_sid', lambda: USER)
    monkeypatch.setattr(acl, '_win_token_owner_sid', lambda: state['token_owner'])
    monkeypatch.setattr(acl, '_win_read_security', lambda path: (True, state['owner'], list(GOOD_ACES)))
    return state


@pytest.mark.parametrize('owner,expected', [(USER, True), (ADMINS, True), (OTHER, False), ('', False)])
def test_user_or_token_owner_is_accepted(elevated, owner, expected):
    elevated['owner'] = owner
    assert acl.is_owned_by_current_user('D:\\uecache\\j\\1-abcdef01\\output') is expected


def test_non_elevated_token_still_rejects_administrators(elevated):
    elevated['owner'], elevated['token_owner'] = ADMINS, USER
    assert acl.is_owned_by_current_user('D:\\x') is False


def test_token_owner_lookup_failure_falls_back_to_the_user(elevated, monkeypatch):
    def broken():
        raise OSError(5, 'denied')

    monkeypatch.setattr(acl, '_win_token_owner_sid', broken)
    elevated['owner'] = ADMINS
    assert acl.is_owned_by_current_user('D:\\x') is False
    elevated['owner'] = USER
    assert acl.is_owned_by_current_user('D:\\x') is True
    assert acl.current_owner_sids() == frozenset({USER})


def test_owner_only_verification_stays_exact(elevated):
    elevated['owner'] = ADMINS
    assert acl.verify_owner_only('D:\\uecache') is False
    elevated['owner'] = USER
    assert acl.verify_owner_only('D:\\uecache') is True


def test_current_owner_sids(elevated):
    assert acl.current_owner_sids() == frozenset({USER, ADMINS})


def test_current_owner_sids_is_windows_only(monkeypatch):
    monkeypatch.setattr(acl, '_is_windows', lambda: False)
    with pytest.raises(OSError):
        acl.current_owner_sids()


@pytest.mark.skipif(sys.platform != 'win32', reason='real process token (Windows)')
def test_real_token_owner_and_plain_mkdir(tmp_path):
    from shared import owner_only_acl_win
    owner = owner_only_acl_win.token_owner_sid()
    assert owner.startswith('S-1-')
    assert owner in acl.current_owner_sids() and acl.current_user_sid() in acl.current_owner_sids()
    plain = tmp_path / 'plain'
    plain.mkdir()  # owner = the token's default owner (Administrators when elevated)
    assert acl.is_owned_by_current_user(plain) is True
