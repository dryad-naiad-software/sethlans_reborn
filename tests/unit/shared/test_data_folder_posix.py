# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""POSIX data-folder rules (home and ``/run/media`` exceptions, descriptor adopt) and the marker race.

The POSIX classes run only on Linux and macOS CI.
"""
import errno
import os
import stat
from pathlib import Path

import pytest

from shared import data_folder, data_folder_rules, owner_only_acl
from tests.unit._off_system_tmp import off_system_tmp_path_fixture
from tests.unit.shared._data_folder_helpers import make_link, own_dir, posix_only

# On macOS pytest's tmp_path is under /private/var, a refused system folder.
tmp_path = off_system_tmp_path_fixture()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path / "settings"))


def _home(monkeypatch, home, links=None):
    """``Path.home()`` is ``home``; ``links`` maps a fake link to its target for ``realpath``."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(home)))
    if links:
        real = os.path.realpath

        def realpath(path, **kwargs):
            text = os.fspath(path)
            for link, target in links.items():
                if text == link or text.startswith(link + "/"):
                    return real(target + text[len(link):], **kwargs)  # macOS: /var is /private/var
            return real(path, **kwargs)

        monkeypatch.setattr(os.path, "realpath", realpath)


def _media_user(monkeypatch, name):
    import pwd

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: type("P", (), {"pw_name": name})())


@posix_only
class TestHomeException:

    def test_home_under_var_is_usable(self, monkeypatch):
        _home(monkeypatch, "/var/home/me")
        assert data_folder_rules.location_problems("/var/home/me/sethlans") == []

    def test_home_link_to_var_home_is_usable(self, monkeypatch):
        _home(monkeypatch, "/home/me", links={"/home": "/var/home"})
        assert data_folder_rules.location_problems("/home/me/sethlans") == []

    def test_other_users_home_under_var_is_refused(self, monkeypatch):
        _home(monkeypatch, "/var/home/me")
        # [0]: on macOS the resolved /private/var form adds a second line.
        problems = data_folder_rules.location_problems("/var/home/other/x")
        assert problems[0] == "/var/home/other/x is inside a system folder (/var)"

    @pytest.mark.parametrize("home", ["/", "/var"])
    def test_shallow_home_exempts_nothing(self, monkeypatch, home):
        _home(monkeypatch, home)
        problems = data_folder_rules.location_problems("/var/lib/sethlans")
        assert problems[0] == "/var/lib/sethlans is inside a system folder (/var)"

    @pytest.mark.parametrize("path, prefix", [("/var/lib/x", "/var"), ("/run/x", "/run"), ("/var/home/x", "/var")])
    def test_rest_of_var_and_run_is_refused(self, monkeypatch, path, prefix):
        _home(monkeypatch, "/home/me")
        assert data_folder_rules.location_problems(path)[0] == f"{path} is inside a system folder ({prefix})"

    def test_home_lookup_failure_exempts_nothing(self, monkeypatch):
        def fail(cls):
            raise RuntimeError("no home")

        monkeypatch.setattr(Path, "home", classmethod(fail))
        assert data_folder_rules._user_folders() == []


@posix_only
class TestRunMedia:

    def test_inside_my_media_folder_is_usable(self, monkeypatch):
        _media_user(monkeypatch, "me")
        assert data_folder_rules.location_problems("/run/media/me/disk/x") == []

    @pytest.mark.parametrize("path", ["/run/media/me", "/run/media/other/disk", "/run/media", "/run/mediax/me/d"])
    def test_my_media_folder_itself_and_others_are_refused(self, monkeypatch, path):
        _media_user(monkeypatch, "me")
        assert data_folder_rules.location_problems(path) == [f"{path} is inside a system folder (/run)"]

    def test_unknown_user_has_no_media_exception(self, monkeypatch):
        import pwd

        monkeypatch.setattr(pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError(uid)))
        assert data_folder_rules.location_problems("/run/media/me/disk/x") == [
            "/run/media/me/disk/x is inside a system folder (/run)"]


@pytest.fixture
def unchecked(mocker):
    """``prepare_data_root`` without the up-front checks, to reach the descriptor adopt."""
    return mocker.patch.object(data_folder, "check_data_root", return_value=[])


@posix_only
class TestPosixAdopt:

    def test_link_is_refused_with_eloop(self, tmp_path, unchecked):
        real = own_dir(tmp_path / "real")
        link = make_link(tmp_path / "data", real)
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(link)
        assert str(info.value) == f"{link} is a link or not a folder; choose a plain folder."
        assert stat.S_IMODE(os.stat(real).st_mode) == 0o700 and list(real.iterdir()) == []

    def test_file_is_refused_with_enotdir(self, tmp_path, unchecked):
        target = tmp_path / "data"
        target.write_text("x")
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(target)
        assert str(info.value) == f"{target} is a link or not a folder; choose a plain folder."

    def test_emlink_counts_as_a_link(self, tmp_path, unchecked, mocker):
        target = own_dir(tmp_path / "data")
        mocker.patch.object(data_folder.os, "open", side_effect=OSError(errno.EMLINK, "Too many links"))
        with pytest.raises(data_folder.DataFolderError, match="is a link or not a folder"):
            data_folder.prepare_data_root(target)

    def test_other_open_errors_become_could_not_prepare(self, tmp_path, unchecked, mocker):
        target = own_dir(tmp_path / "data")
        mocker.patch.object(data_folder.os, "open", side_effect=PermissionError(errno.EACCES, "Permission denied"))
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(target)
        assert str(info.value) == f"Could not prepare {target}: Permission denied"

    def test_uid_mismatch_is_refused(self, tmp_path, unchecked, monkeypatch):
        target = own_dir(tmp_path / "data")
        real_euid = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(target)
        assert str(info.value) == f"{target} belongs to another account; choose a folder you own."

    @pytest.mark.parametrize("mode, tightened", [(0o700, False), (0o750, True), (0o701, True), (0o777, True)])
    def test_fchmod_only_when_group_or_other_bits_are_set(self, tmp_path, mocker, mode, tightened):
        target = own_dir(tmp_path / "data")
        os.chmod(target, mode)
        fchmod = mocker.spy(data_folder.os, "fchmod")
        data_folder.prepare_data_root(target)
        assert [c.args[1] for c in fchmod.call_args_list if c.args[1] == 0o700][:1] == ([0o700] if tightened else [])
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700

    def test_folder_that_stays_loose_is_refused(self, tmp_path, mocker):
        target = own_dir(tmp_path / "data")
        os.chmod(target, 0o755)
        mocker.patch.object(data_folder.os, "fchmod")
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(target)
        assert str(info.value) == f"Could not limit {target} to the current user."


class TestMarkerRace:

    @pytest.fixture
    def root(self, tmp_path):
        return own_dir(tmp_path / "data")

    def _race(self, mocker, make):
        def lose(path, **kwargs):
            make(Path(path))
            raise FileExistsError(path)

        mocker.patch.object(owner_only_acl, "create_owner_only_file", side_effect=lose)

    def test_plain_file_from_the_winner_is_accepted(self, root, mocker):
        self._race(mocker, lambda marker: marker.write_bytes(b"other process"))
        assert data_folder.prepare_data_root(root) == root
        assert (root / ".sethlans-data").read_bytes() == b"other process"

    def test_folder_from_the_winner_is_refused(self, root, mocker):
        self._race(mocker, lambda marker: marker.mkdir())
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(root)
        assert str(info.value) == f"{root / '.sethlans-data'} exists and is not a regular file."

    def test_link_from_the_winner_is_refused(self, root, tmp_path, mocker):
        elsewhere = own_dir(tmp_path / "elsewhere")
        self._race(mocker, lambda marker: make_link(marker, elsewhere))
        with pytest.raises(data_folder.DataFolderError, match="exists and is not a regular file"):
            data_folder.prepare_data_root(root)
