# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``owner_only_acl`` owner helpers: exact owner, system owner, user-or-system.

Both branches run on every OS: the Windows one through the
``_win_read_security`` / ``current_user_sid`` seams, the POSIX one through
``os.lstat`` / ``os.geteuid``.
"""
import os
from types import SimpleNamespace

import pytest

from shared import owner_only_acl as acl

USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
OTHER = "S-1-5-21-1111111111-2222222222-3333333333-1002"


class TestConstants:

    def test_system_owner_sids(self):
        assert acl.SYSTEM_SID == "S-1-5-18"
        assert acl.ADMINISTRATORS_SID == "S-1-5-32-544"
        assert acl.TRUSTED_INSTALLER_SID == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
        assert acl.SYSTEM_OWNER_SIDS == {acl.SYSTEM_SID, acl.ADMINISTRATORS_SID, acl.TRUSTED_INSTALLER_SID}


@pytest.fixture
def windows(monkeypatch):
    """A Windows process whose user is ``USER``, elevated (default owner Administrators)."""
    owners = {}
    monkeypatch.setattr(acl, "_is_windows", lambda: True)
    monkeypatch.setattr(acl, "_win_token_user_sid", lambda: USER)
    monkeypatch.setattr(acl, "_win_token_owner_sid", lambda: acl.ADMINISTRATORS_SID)

    def read(path):
        owner = owners[path]
        if isinstance(owner, Exception):
            raise owner
        return True, owner, []

    monkeypatch.setattr(acl, "_win_read_security", read)
    return owners


class TestWindowsOwners:

    @pytest.mark.parametrize("owner, token_user, current, system, either", [
        (USER, True, True, False, True),
        (acl.ADMINISTRATORS_SID, False, True, True, True),
        (acl.SYSTEM_SID, False, False, True, True),
        (acl.TRUSTED_INSTALLER_SID, False, False, True, True),
        (OTHER, False, False, False, False),
    ])
    def test_owner_table(self, windows, owner, token_user, current, system, either):
        windows["p"] = owner
        assert acl.is_owned_by_token_user("p") is token_user
        assert acl.is_owned_by_current_user("p") is current
        assert acl.is_owned_by_system("p") is system
        assert acl.is_owned_by_user_or_system("p") is either

    @pytest.mark.parametrize("error", [OSError("gone"), ValueError("bad path")])
    def test_read_errors_are_false(self, windows, error):
        windows["p"] = error
        assert acl.is_owned_by_token_user("p") is False
        assert acl.is_owned_by_system("p") is False
        assert acl.is_owned_by_user_or_system("p") is False

    def test_paths_are_passed_as_strings(self, windows, tmp_path):
        windows[os.fspath(tmp_path)] = USER
        assert acl.is_owned_by_token_user(tmp_path) is True


@pytest.fixture
def posix(monkeypatch):
    """A POSIX process with euid 1000; ``owners`` maps a path to its uid (or an exception)."""
    owners = {}
    monkeypatch.setattr(acl, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)

    def lstat(path):
        owner = owners[os.fspath(path)]
        if isinstance(owner, Exception):
            raise owner
        return SimpleNamespace(st_uid=owner)

    monkeypatch.setattr(os, "lstat", lstat)
    return owners


class TestPosixOwners:

    @pytest.mark.parametrize("uid, token_user, system, either", [
        (1000, True, False, True), (0, False, True, True), (1001, False, False, False),
    ])
    def test_owner_table(self, posix, uid, token_user, system, either):
        posix["p"] = uid
        assert acl.is_owned_by_token_user("p") is token_user
        assert acl.is_owned_by_system("p") is system
        assert acl.is_owned_by_user_or_system("p") is either

    @pytest.mark.parametrize("error", [OSError("gone"), ValueError("embedded null byte")])
    def test_lstat_errors_are_false(self, posix, error):
        posix["p"] = error
        assert acl.is_owned_by_token_user("p") is False
        assert acl.is_owned_by_system("p") is False
        assert acl.is_owned_by_user_or_system("p") is False


class TestRealFiles:

    def test_own_new_folder_has_the_exact_owner(self, tmp_path):
        folder = tmp_path / "mine"
        assert acl.create_owner_only_dir(folder) is True
        assert acl.is_owned_by_token_user(folder) is True
        assert acl.is_owned_by_user_or_system(folder) is True

    def test_missing_path_is_false(self, tmp_path):
        assert acl.is_owned_by_token_user(tmp_path / "missing") is False
        assert acl.is_owned_by_system(tmp_path / "missing") is False
