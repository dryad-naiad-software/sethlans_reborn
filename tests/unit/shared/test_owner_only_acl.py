# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.owner_only_acl`` with the ctypes layer mocked (Unreal foundation
FR-WC-4, Sec M1/N2; FR-T-5 "Owner-only ACL"). Runs on every OS; the real
Windows behavior is covered by
``tests/integration/shared/test_owner_only_acl_windows.py``.
"""
import os
import stat
import sys

import pytest

from shared import owner_only_acl as acl

USER = 'S-1-5-21-1111111111-2222222222-3333333333-1001'
OTHER = 'S-1-5-21-1111111111-2222222222-3333333333-1002'
ADMINS = 'S-1-5-32-544'
OICI = 0x3
GOOD_ACES = [(0, OICI, USER), (0, OICI, acl.SYSTEM_SID)]


@pytest.fixture()
def windows(monkeypatch):
    """Force the Windows branch with a fake token and security store."""
    state = {'written': [], 'security': (True, USER, list(GOOD_ACES))}
    monkeypatch.setattr(acl, '_is_windows', lambda: True)
    monkeypatch.setattr(acl, '_win_token_user_sid', lambda: USER)
    monkeypatch.setattr(acl, '_win_set_security', lambda path, sddl: state['written'].append((path, sddl)))

    def read(path):
        value = state['security']
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(acl, '_win_read_security', read)
    return state


class TestApply:

    def test_sddl_sets_owner_and_protected_dacl(self, windows):
        acl.apply_owner_only('D:\\uecache')
        assert windows['written'] == [
            ('D:\\uecache', f'O:{USER}D:P(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)')]

    def test_sid_comes_from_the_token_not_username(self, windows, monkeypatch):
        monkeypatch.setenv('USERNAME', 'Administrator')
        monkeypatch.setenv('USERDOMAIN', 'EVIL')
        acl.apply_owner_only('D:\\uecache')
        assert USER in windows['written'][0][1] and 'Administrator' not in windows['written'][0][1]

    def test_system_account_gets_a_single_ace(self):
        assert acl.owner_only_sddl(acl.SYSTEM_SID) == 'O:S-1-5-18D:P(A;OICI;FA;;;S-1-5-18)'

    def test_failure_propagates(self, windows, monkeypatch):
        def denied(path, sddl):
            raise OSError(5, 'SetNamedSecurityInfoW failed (Windows error 5)')

        monkeypatch.setattr(acl, '_win_set_security', denied)
        with pytest.raises(OSError):
            acl.apply_owner_only('D:\\uecache')


class TestVerify:

    def test_exact_acl_verifies(self, windows):
        assert acl.verify_owner_only('D:\\uecache') is True

    @pytest.mark.parametrize('security', [
        (False, USER, GOOD_ACES),                                   # not protected
        (True, OTHER, GOOD_ACES),                                   # another owner (Sec N2)
        (True, ADMINS, GOOD_ACES),                                  # Administrators as owner
        (True, '', GOOD_ACES),                                      # no owner
        (True, USER, None),                                         # NULL DACL
        (True, USER, GOOD_ACES + [(0, OICI, ADMINS)]),              # extra ACE
        (True, USER, [(0, OICI, USER)]),                            # SYSTEM missing
        (True, USER, [(0, OICI, USER), (0, OICI, USER)]),           # duplicate, SYSTEM missing
        (True, USER, [(1, OICI, ''), (0, OICI, USER)]),             # a deny ACE
        (True, USER, [(0, 0x1, USER), (0, OICI, acl.SYSTEM_SID)]),  # not container-inherit
        (True, USER, [(0, 0x13, USER), (0, OICI, acl.SYSTEM_SID)]),  # inherited flag
        (True, USER, [(0, 0xB, USER), (0, OICI, acl.SYSTEM_SID)]),  # inherit-only
        (True, USER, [(0, OICI, OTHER), (0, OICI, acl.SYSTEM_SID)]),
    ])
    def test_rejects(self, windows, security):
        windows['security'] = security
        assert acl.verify_owner_only('D:\\uecache') is False

    def test_read_error_is_false(self, windows):
        windows['security'] = OSError(2, 'missing')
        assert acl.verify_owner_only('D:\\uecache') is False

    def test_token_error_is_false(self, windows, monkeypatch):
        def broken():
            raise OSError(5, 'denied')

        monkeypatch.setattr(acl, '_win_token_user_sid', broken)
        assert acl.verify_owner_only('D:\\uecache') is False


class TestImportSafety:

    def test_no_windows_modules_at_import(self):
        from shared import owner_only_acl_win
        for module in (acl, owner_only_acl_win):
            source = open(module.__file__, encoding='utf-8').read()
            top_level = [line for line in source.splitlines() if line.startswith(('import ', 'from '))]
            assert not [line for line in top_level if 'ctypes' in line or 'winreg' in line or 'msvcrt' in line]

    def test_current_user_sid_is_windows_only(self, monkeypatch):
        monkeypatch.setattr(acl, '_is_windows', lambda: False)
        with pytest.raises(OSError):
            acl.current_user_sid()


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX mode bits')
class TestPosix:

    def test_apply_and_verify(self, tmp_path):
        target = tmp_path / 'cache'
        target.mkdir(mode=0o755)
        assert acl.verify_owner_only(target) is False
        acl.apply_owner_only(target)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
        assert acl.verify_owner_only(target) is True
        os.chmod(target, 0o750)
        assert acl.verify_owner_only(target) is False

    def test_foreign_owner(self, tmp_path, monkeypatch):
        target = tmp_path / 'cache'
        target.mkdir(mode=0o700)
        monkeypatch.setattr(os, 'geteuid', lambda: os.stat(target).st_uid + 1)
        assert acl.verify_owner_only(target) is False


class TestCreateAndOwner:

    def test_create_uses_the_owner_only_sddl(self, windows, monkeypatch):
        seen = []
        monkeypatch.setattr(acl, '_win_create_dir', lambda path, sddl: seen.append((path, sddl)) or True)
        assert acl.create_owner_only_dir('D:\\uecache') is True
        assert seen == [('D:\\uecache', acl.owner_only_sddl(USER))]

    def test_create_existing_returns_false(self, windows, monkeypatch):
        monkeypatch.setattr(acl, '_win_create_dir', lambda path, sddl: False)
        assert acl.create_owner_only_dir('D:\\uecache') is False

    @pytest.mark.parametrize('owner,expected', [(USER, True), (OTHER, False), (ADMINS, False), ('', False)])
    def test_is_owned_by_current_user(self, windows, owner, expected):
        windows['security'] = (True, owner, GOOD_ACES)
        assert acl.is_owned_by_current_user('D:\\uecache') is expected

    def test_owner_read_error_is_not_owned(self, windows):
        windows['security'] = PermissionError(5, 'denied')
        assert acl.is_owned_by_current_user('D:\\uecache') is False


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX mode bits')
class TestCreatePosix:

    def test_create(self, tmp_path):
        target = tmp_path / 'cache'
        assert acl.create_owner_only_dir(target) is True
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
        assert acl.create_owner_only_dir(target) is False
        assert acl.is_owned_by_current_user(target) is True
