# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The POSIX home exemption of the data-folder rules (runs only on Linux and macOS CI).

The home folder is the account's ``pw_dir`` (``pwd.getpwuid(os.geteuid())``),
never ``$HOME`` or ``Path.home()``, and it exempts only when it is an
existing folder owned by the effective uid, at least two levels deep.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import data_folder_rules
from tests.unit._off_system_tmp import off_system_tmp_path_fixture
from tests.unit.shared._data_folder_helpers import make_link, own_dir, posix_only

# On macOS pytest's tmp_path is under /private/var, a refused system folder.
tmp_path = off_system_tmp_path_fixture()

pytestmark = posix_only


def _account(monkeypatch, pw_dir, name="me"):
    import pwd

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=pw_dir, pw_name=name))


def _owned(monkeypatch, *folders):
    """``_is_own_folder`` is True for exactly ``folders``."""
    mine = {os.path.normpath(folder) for folder in folders}
    monkeypatch.setattr(data_folder_rules, "_is_own_folder", lambda path: os.path.normpath(path) in mine)


def _links(monkeypatch, links):
    """``realpath`` follows the fake ``links`` (then the real file system: macOS /var is /private/var)."""
    real = os.path.realpath

    def realpath(path, **kwargs):
        text = os.fspath(path)
        for link, target in links.items():
            if text == link or text.startswith(link + "/"):
                return real(target + text[len(link):], **kwargs)
        return real(path, **kwargs)

    monkeypatch.setattr(os.path, "realpath", realpath)


def _refused(path, prefix="/var"):
    return data_folder_rules.location_problems(path)[0] == f"{path} is inside a system folder ({prefix})"


class TestAccountHome:

    def test_home_under_var_is_usable(self, monkeypatch):
        _account(monkeypatch, "/var/home/me")
        _owned(monkeypatch, "/var/home/me")
        assert data_folder_rules.location_problems("/var/home/me/sethlans") == []

    def test_home_link_to_var_home_is_usable(self, monkeypatch):
        _account(monkeypatch, "/home/me")
        _owned(monkeypatch, "/home/me")
        _links(monkeypatch, {"/home": "/var/home"})
        folders = data_folder_rules._user_folders()
        assert folders[0] == Path("/home/me") and str(folders[1]).endswith("/var/home/me")
        assert data_folder_rules.location_problems("/home/me/sethlans") == []

    def test_other_users_home_under_var_is_refused(self, monkeypatch):
        _account(monkeypatch, "/var/home/me")
        _owned(monkeypatch, "/var/home/me")
        assert data_folder_rules.location_problems("/var/home/me/x") == []  # the account's own: exempt
        assert _refused("/var/home/other/x")

    @pytest.mark.parametrize("path, prefix", [("/var/lib/x", "/var"), ("/run/x", "/run"), ("/var/home/x", "/var")])
    def test_rest_of_var_and_run_is_refused(self, monkeypatch, path, prefix):
        _account(monkeypatch, "/var/home/me")
        _owned(monkeypatch, "/var/home/me")
        assert Path("/var/home/me") in data_folder_rules._user_folders()
        assert _refused(path, prefix)

    def test_home_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("HOME", "/var/lib")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/var/lib")))
        _account(monkeypatch, "/home/me")
        _owned(monkeypatch, "/home/me", "/var/lib")
        assert _refused("/var/lib/sethlans")

    def test_lookup_failure_exempts_nothing(self, monkeypatch):
        import pwd

        def fail(uid):
            raise KeyError(uid)

        monkeypatch.setattr(pwd, "getpwuid", fail)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/var/home/me")))
        _owned(monkeypatch, "/var/home/me")
        assert data_folder_rules._user_folders() == []
        assert _refused("/var/home/me/x")


class TestNoExemption:

    @pytest.mark.parametrize("pw_dir", [
        "/",
        pytest.param("/var", marks=pytest.mark.xfail(
            sys.platform == "darwin", strict=True,
            reason="product gap: the depth rule is applied after resolving, and /var resolves to /private/var")),
    ])
    def test_shallow_home_exempts_nothing(self, monkeypatch, pw_dir):
        _account(monkeypatch, pw_dir)
        _owned(monkeypatch, pw_dir)
        assert data_folder_rules._user_folders() == []
        assert _refused("/var/lib/sethlans")
        assert _refused("/private/var/db/sethlans", "/private/var")

    @pytest.mark.parametrize("pw_dir", ["", None, "home/me", "./me"])
    def test_empty_or_relative_home(self, monkeypatch, pw_dir):
        _account(monkeypatch, pw_dir)
        monkeypatch.setattr(data_folder_rules, "_is_own_folder", lambda path: True)
        assert data_folder_rules._user_folders() == []

    def test_missing_home(self, monkeypatch, tmp_path):
        _account(monkeypatch, str(tmp_path / "missing" / "me"))
        assert data_folder_rules._user_folders() == []

    def test_home_that_is_a_file(self, monkeypatch, tmp_path):
        home = tmp_path / "me"
        home.write_text("x")
        _account(monkeypatch, str(home))
        assert data_folder_rules._user_folders() == []

    def test_home_of_another_uid(self, monkeypatch, tmp_path):
        home = own_dir(tmp_path / "home" / "me")
        _account(monkeypatch, str(home))
        real_euid = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)
        assert data_folder_rules._is_own_folder(home) is False
        assert data_folder_rules._user_folders() == []

    def test_missing_var_home_does_not_exempt(self, monkeypatch):
        _account(monkeypatch, "/var/home/sethlans-test-nobody")  # not on the runner: the real check says no
        assert data_folder_rules._user_folders() == []
        assert _refused("/var/home/sethlans-test-nobody/x")


class TestRealOwnedHome:

    def test_own_folder_is_recognised(self, tmp_path):
        assert data_folder_rules._is_own_folder(own_dir(tmp_path / "home" / "me")) is True
        assert data_folder_rules._is_own_folder(tmp_path / "nope") is False

    def test_owned_pw_dir_exempts_paths_inside_it(self, monkeypatch, tmp_path):
        home = own_dir(tmp_path / "home" / "me")
        assert len(home.parts) >= 3
        _account(monkeypatch, str(home))
        assert data_folder_rules._user_folders() == [home]
        assert data_folder_rules._posix_exempt(home / "sethlans") is True
        assert data_folder_rules._posix_exempt(home) is True
        assert data_folder_rules._posix_exempt(tmp_path / "home" / "other") is False

    def test_linked_pw_dir_exempts_given_and_resolved_paths(self, monkeypatch, tmp_path):
        home = own_dir(tmp_path / "real-home" / "me")
        link = make_link(tmp_path / "home-link", home)
        _account(monkeypatch, str(link))
        assert data_folder_rules._user_folders() == [link, Path(os.path.realpath(home))]
        assert data_folder_rules._posix_exempt(link / "sethlans") is True
        assert data_folder_rules._posix_exempt(home / "sethlans") is True
        assert data_folder_rules._posix_exempt(tmp_path / "elsewhere") is False
