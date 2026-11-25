# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``shared.data_folder.check_data_root``, ``install_dir`` and ``free_bytes``."""
import os
import sys
from pathlib import Path

import pytest

from shared import data_folder, owner_only_acl
from tests.unit._off_system_tmp import off_system_tmp_path_fixture
from tests.unit.shared._data_folder_helpers import make_link, override_owner, own_dir, windows_owner

# On macOS pytest's tmp_path is under /private/var, a refused system folder.
tmp_path = off_system_tmp_path_fixture()

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows system folders and drive paths")
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX system prefixes and symlinks")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path / "settings"))
    for name in ("SETHLANS_DATA_ROOT", "SETHLANS_MANAGER_DATA_DIR", "SETHLANS_WORKER_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


class TestBasicRefusals:

    @pytest.mark.parametrize("value", [None, "", "relative", "./data", Path("data")])
    def test_must_be_absolute(self, value):
        assert data_folder.check_data_root(value) == ["The data folder must be an absolute path."]

    def test_filesystem_root(self, tmp_path):
        root = Path(tmp_path.anchor)
        assert data_folder.check_data_root(root) == [f"{root} is the root of a drive or file system"]

    def test_new_folder_is_fine_and_not_created(self, tmp_path):
        target = tmp_path / "new" / "data"
        assert data_folder.check_data_root(target) == []
        assert not target.parent.exists()

    def test_existing_own_folder_is_fine(self, tmp_path):
        assert data_folder.check_data_root(own_dir(tmp_path / "data")) == []

    def test_a_file_is_refused(self, tmp_path):
        target = tmp_path / "file"
        target.write_text("x")
        assert data_folder.check_data_root(target) == [f"{target} is a link or not a folder; choose a plain folder."]

    def test_other_owner_is_refused(self, tmp_path, mocker):
        target = own_dir(tmp_path / "data")
        owned = override_owner(mocker, "is_owned_by_token_user", {target: False})
        assert data_folder.check_data_root(target) == [f"{target} belongs to another account; choose a folder "
                                                       "you own."]
        assert [c.args[0] for c in owned.call_args_list] == [target]

    def test_exact_owner_is_asked_not_the_loose_one(self, tmp_path, mocker):
        target = own_dir(tmp_path / "data")
        loose = mocker.spy(data_folder.owner_only_acl, "is_owned_by_current_user")
        exact = mocker.spy(data_folder.owner_only_acl, "is_owned_by_token_user")
        data_folder.check_data_root(target)
        assert target in [c.args[0] for c in exact.call_args_list]
        assert target not in [c.args[0] for c in loose.call_args_list]

    def test_a_link_is_refused(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = make_link(tmp_path / "link", real)
        assert data_folder.check_data_root(link) == [f"{link} is a link or not a folder; choose a plain folder."]

    def test_dangling_link_is_refused(self, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        link = make_link(tmp_path / "dangling", gone)
        gone.rmdir()
        assert data_folder.check_data_root(link) == [f"{link} is a link or not a folder; choose a plain folder."]


@windows_only
class TestAdministratorsOwnedRoot:

    def test_administrators_owned_root_is_refused(self, tmp_path, mocker):
        target = own_dir(tmp_path / "data")
        windows_owner(mocker, target, owner_only_acl.ADMINISTRATORS_SID)
        assert data_folder.check_data_root(target) == [f"{target} belongs to another account; choose a folder "
                                                       "you own."]

    def test_administrators_owned_parent_is_fine(self, tmp_path, mocker):
        parent = own_dir(tmp_path / "parent")
        windows_owner(mocker, parent, owner_only_acl.ADMINISTRATORS_SID)
        assert data_folder.check_data_root(parent / "data") == []


@windows_only
class TestWindowsSystemFolders:

    @pytest.mark.parametrize("value, folder", [
        ("C:\\Windows\\Sethlans", "C:\\Windows"),
        ("c:\\windows", "c:\\windows"),
        ("C:\\Program Files\\Sethlans", "C:\\Program Files"),
        ("C:\\Program Files (x86)\\Sethlans", "C:\\Program Files (x86)"),
        ("D:\\Program Files\\data", "D:\\Program Files"),
    ])
    def test_refused(self, value, folder):
        assert data_folder.check_data_root(value) == [f"{value} is inside a system folder ({folder})"]

    @pytest.mark.parametrize("env", ["SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"])
    def test_folders_the_os_names(self, monkeypatch, tmp_path, env):
        system = tmp_path / "os-folder"
        monkeypatch.setenv(env, str(system))
        target = system / "Sethlans"
        assert data_folder.check_data_root(target) == [f"{target} is inside a system folder ({system})"]

    def test_similar_names_are_fine(self):
        assert data_folder.check_data_root("Z:\\WindowsData\\Sethlans") == []

    def test_junction_into_a_system_folder_is_refused(self, tmp_path):
        import _winapi

        link = tmp_path / "to-windows"
        _winapi.CreateJunction(os.environ["SystemRoot"], str(link))
        problems = data_folder.check_data_root(link / "Sethlans")
        resolved = os.path.realpath(link / "Sethlans")
        assert len(problems) == 1
        assert problems[0].casefold().startswith(f"{resolved} is inside a system folder (".casefold())

    def test_junction_is_refused_as_a_link(self, tmp_path):
        import _winapi

        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "junction"
        _winapi.CreateJunction(str(real), str(link))
        assert data_folder.check_data_root(link) == [f"{link} is a link or not a folder; choose a plain folder."]


@posix_only
class TestPosixSystemFolders:

    @pytest.mark.parametrize("value, prefix", [
        ("/usr/share/sethlans", "/usr"), ("/usr", "/usr"), ("/etc/sethlans", "/etc"),
        ("/opt/sethlans", "/opt"), ("/var/lib/sethlans", "/var"),
        ("/private/etc/sethlans", "/private/etc"), ("/private/var/sethlans", "/private/var"),
        ("/System/sethlans", "/System"), ("/Library/Sethlans", "/Library"), ("/Applications/Sethlans", "/Applications"),
        ("/bin/sethlans", "/bin"), ("/sbin/sethlans", "/sbin"), ("/lib/sethlans", "/lib"), ("/lib32/x", "/lib32"),
        ("/lib64/x", "/lib64"), ("/boot/sethlans", "/boot"), ("/dev/sethlans", "/dev"), ("/proc/sethlans", "/proc"),
        ("/sys/sethlans", "/sys"), ("/run/sethlans", "/run"),
    ])
    def test_refused(self, value, prefix):
        # The given form comes first; where it resolves elsewhere (macOS
        # /etc, merged /usr on Linux) the resolved form may add a second line.
        assert data_folder.check_data_root(value)[0] == f"{value} is inside a system folder ({prefix})"

    @pytest.mark.parametrize("value", ["/usrdata/x", "/home/user/sethlans", "/srv/sethlans", "/tmp/sethlans",
                                       "/private/tmp/sethlans", "/libdata/x", "/runner/x", "/Users/someone/x"])
    def test_similar_names_and_temp_are_fine(self, value):
        assert all("system folder" not in problem for problem in data_folder.check_data_root(value))

    def test_a_link_into_a_system_folder_is_refused_by_its_resolved_path(self, tmp_path):
        link = tmp_path / "to-etc"
        _symlink(link, Path("/etc"))
        target = link / "sethlans"
        resolved = Path(os.path.realpath(target))
        prefix = "/private/etc" if str(resolved).startswith("/private/etc") else "/etc"  # macOS: /etc -> /private/etc
        assert data_folder.check_data_root(target) == [f"{resolved} is inside a system folder ({prefix})"]


class TestInstallFolder:

    @pytest.fixture
    def frozen(self, mocker, tmp_path):
        install = tmp_path / "Sethlans"
        mocker.patch.object(data_folder.frozen_paths, "is_frozen", return_value=True)
        mocker.patch.object(data_folder.frozen_paths, "get_install_root", return_value=install / "bin")
        return install

    def test_from_source_there_is_no_install_folder(self):
        assert data_folder.install_dir() is None

    def test_frozen_install_folder_is_the_parent_of_bin(self, frozen):
        assert data_folder.install_dir() == frozen

    def test_inside_the_install_folder_is_refused_when_frozen(self, frozen):
        target = frozen / "data"
        assert data_folder.check_data_root(target) == [f"{target} is inside the install folder ({frozen})"]

    def test_the_install_folder_itself_is_refused(self, frozen):
        assert data_folder.check_data_root(frozen) == [f"{frozen} is inside the install folder ({frozen})"]

    def test_next_to_the_install_folder_is_fine(self, frozen, tmp_path):
        assert data_folder.check_data_root(tmp_path / "SethlansData") == []

    def test_same_path_is_fine_from_source(self, tmp_path):
        assert data_folder.check_data_root(tmp_path / "Sethlans" / "data") == []


class TestFreeBytes:

    def test_nearest_existing_parent(self, tmp_path, mocker):
        usage = mocker.patch.object(data_folder.shutil, "disk_usage", return_value=mocker.Mock(free=123))
        assert data_folder.free_bytes(tmp_path / "a" / "b") == 123
        assert Path(usage.call_args.args[0]) == tmp_path

    def test_error_is_none(self, tmp_path, mocker):
        mocker.patch.object(data_folder.shutil, "disk_usage", side_effect=OSError("gone"))
        assert data_folder.free_bytes(tmp_path) is None

    def test_real_value_is_an_int(self, tmp_path):
        assert isinstance(data_folder.free_bytes(tmp_path), int)

    def test_threshold_is_20_gb(self):
        assert data_folder.LOW_SPACE_BYTES == 20 * 1000 ** 3
