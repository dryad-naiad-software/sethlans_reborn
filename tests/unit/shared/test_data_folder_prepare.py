# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``shared.data_folder.prepare_data_root`` and ``ensure_configured_data_root``."""
import os
import stat
import sys

import pytest

from shared import data_folder, owner_only_acl
from tests.unit._off_system_tmp import off_system_tmp_path_fixture

# On macOS pytest's tmp_path is under /private/var, a refused system folder.
tmp_path = off_system_tmp_path_fixture()

WINDOWS = sys.platform == "win32"
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX mode bits and symlinks")
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows junctions")


def _junction(link, target):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(tmp_path / "settings" / "sethlans.ini"))
    for name in ("SETHLANS_DATA_ROOT", "SETHLANS_MANAGER_DATA_DIR", "SETHLANS_WORKER_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "parent" / "sethlans-data"


def _all_owner_only(root):
    return [owner_only_acl.verify_owner_only(path) for path in (root, root / "manager", root / "worker")]


class TestCreate:

    def test_creates_root_components_and_marker(self, root):
        assert data_folder.prepare_data_root(root) == root
        assert _all_owner_only(root) == [True, True, True]
        marker = root / ".sethlans-data"
        assert marker.read_bytes() == data_folder.MARKER_TEXT
        assert owner_only_acl.verify_owner_only(marker)

    def test_returns_the_normalised_path(self, root):
        messy = str(root.parent / "x" / ".." / root.name)
        assert data_folder.prepare_data_root(messy) == root

    def test_second_run_keeps_everything(self, root):
        data_folder.prepare_data_root(root)
        (root / "manager" / "db.sqlite3").write_text("data")
        data_folder.prepare_data_root(root)
        assert (root / "manager" / "db.sqlite3").read_text() == "data"
        assert _all_owner_only(root) == [True, True, True]

    def test_existing_marker_is_left_alone(self, root):
        root.mkdir(parents=True)
        (root / ".sethlans-data").write_bytes(b"older marker")
        data_folder.prepare_data_root(root)
        assert (root / ".sethlans-data").read_bytes() == b"older marker"


class TestAdopt:

    def test_existing_loose_folder_is_made_owner_only(self, root):
        root.mkdir(parents=True)
        if not WINDOWS:
            os.chmod(root, 0o755)
        assert owner_only_acl.verify_owner_only(root) is False
        data_folder.prepare_data_root(root)
        assert _all_owner_only(root) == [True, True, True]

    @posix_only
    def test_mode_is_0700_after_adopting(self, root):
        root.mkdir(parents=True)
        os.chmod(root, 0o777)
        data_folder.prepare_data_root(root)
        assert stat.S_IMODE(os.stat(root).st_mode) == 0o700

    def test_folder_that_stays_loose_is_refused(self, root, mocker):
        root.mkdir(parents=True)
        mocker.patch.object(owner_only_acl, "verify_owner_only", return_value=False)
        apply = mocker.patch.object(owner_only_acl, "apply_owner_only")
        with pytest.raises(data_folder.DataFolderError, match="Could not limit .* to the current user"):
            data_folder.prepare_data_root(root)
        apply.assert_called_once_with(root)

    def test_already_owner_only_is_not_touched(self, root, mocker):
        data_folder.prepare_data_root(root)
        apply = mocker.patch.object(owner_only_acl, "apply_owner_only")
        data_folder.prepare_data_root(root)
        apply.assert_not_called()


class TestRefusals:

    def test_checks_run_again(self, mocker):
        create = mocker.patch.object(owner_only_acl, "create_owner_only_dir")
        with pytest.raises(data_folder.DataFolderError, match="must be an absolute path"):
            data_folder.prepare_data_root("relative")
        create.assert_not_called()

    def test_other_owner_is_refused(self, root, mocker):
        root.mkdir(parents=True)
        mocker.patch.object(owner_only_acl, "is_owned_by_current_user", return_value=False)
        with pytest.raises(data_folder.DataFolderError, match="belongs to another account"):
            data_folder.prepare_data_root(root)

    def test_component_that_is_a_file_is_refused(self, root):
        root.mkdir(parents=True)
        (root / "worker").write_text("x")
        with pytest.raises(data_folder.DataFolderError, match="worker is a link or not a folder"):
            data_folder.prepare_data_root(root)

    def test_marker_that_is_a_folder_is_refused(self, root):
        (root / ".sethlans-data").mkdir(parents=True)
        with pytest.raises(data_folder.DataFolderError, match="exists and is not a regular file"):
            data_folder.prepare_data_root(root)

    @posix_only
    def test_marker_that_is_a_link_is_refused(self, root, tmp_path):
        root.mkdir(parents=True)
        (tmp_path / "elsewhere").write_text("x")
        os.symlink(tmp_path / "elsewhere", root / ".sethlans-data")
        with pytest.raises(data_folder.DataFolderError, match="exists and is not a regular file"):
            data_folder.prepare_data_root(root)

    @posix_only
    def test_component_that_is_a_link_is_refused(self, root, tmp_path):
        root.mkdir(parents=True)
        (tmp_path / "other").mkdir()
        os.symlink(tmp_path / "other", root / "manager")
        with pytest.raises(data_folder.DataFolderError, match="manager is a link or not a folder"):
            data_folder.prepare_data_root(root)

    @windows_only
    def test_root_that_is_a_junction_is_refused(self, root, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        root.parent.mkdir(parents=True)
        _junction(root, real)
        with pytest.raises(data_folder.DataFolderError, match="sethlans-data is a link or not a folder"):
            data_folder.prepare_data_root(root)
        assert list(real.iterdir()) == []  # nothing was created through the junction

    @windows_only
    def test_component_that_is_a_junction_is_refused(self, root, tmp_path, mocker):
        real = tmp_path / "other"
        real.mkdir()
        root.mkdir(parents=True)
        _junction(root / "manager", real)
        apply = mocker.spy(owner_only_acl, "apply_owner_only")
        with pytest.raises(data_folder.DataFolderError, match="manager is a link or not a folder"):
            data_folder.prepare_data_root(root)
        assert all(call.args[0] != root / "manager" for call in apply.call_args_list)
        assert list(real.iterdir()) == []

    @windows_only
    def test_is_link_sees_a_junction(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        _junction(tmp_path / "j", real)
        assert data_folder.is_link(tmp_path / "j") is True
        assert data_folder.is_link(real) is False

    def test_os_error_becomes_a_data_folder_error(self, root, mocker):
        mocker.patch.object(owner_only_acl, "create_owner_only_dir", side_effect=PermissionError(13, "Access denied"))
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(root)
        assert str(info.value) == f"Could not prepare {root}: Access denied"
        assert info.value.__cause__ is None


class TestEnsureConfigured:

    def _configure(self, tmp_path, root):
        ini = tmp_path / "settings" / "sethlans.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text(f"[paths]\ndata_dir = {root}\n")

    def test_prepares_the_configured_folder(self, tmp_path, root):
        self._configure(tmp_path, root)
        assert data_folder.ensure_configured_data_root("manager") == root
        assert (root / ".sethlans-data").is_file()

    def test_nothing_configured(self):
        assert data_folder.ensure_configured_data_root("manager") is None

    @pytest.mark.parametrize("env", ["SETHLANS_DATA_ROOT", "SETHLANS_MANAGER_DATA_DIR"])
    def test_env_override_skips_it(self, tmp_path, root, monkeypatch, env):
        self._configure(tmp_path, root)
        monkeypatch.setenv(env, str(tmp_path / "env-data"))
        assert data_folder.ensure_configured_data_root("manager") is None
        assert not root.exists()

    def test_configured_system_folder_is_refused(self, tmp_path):
        system = "C:\\Windows\\Sethlans" if WINDOWS else "/usr/sethlans"
        self._configure(tmp_path, system)
        with pytest.raises(data_folder.DataFolderError, match="system folder"):
            data_folder.ensure_configured_data_root("manager")
