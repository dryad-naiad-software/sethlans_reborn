# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``data_folder_check.require_usable_data_folder`` and where ``run_manager.main`` calls it (exit 13)."""
import sys

import pytest

from sethlans_manager import data_folder_check
from shared import data_folder

RESET = "Run Reset setup to choose another."


def _exit(capsys):
    with pytest.raises(SystemExit) as info:
        data_folder_check.require_usable_data_folder()
    return info.value.code, capsys.readouterr().err


class TestRequireUsableDataFolder:

    def test_usable_folder_returns(self, mocker, capsys):
        ensure = mocker.patch.object(data_folder_check, "ensure_configured_data_root", return_value=None)
        assert data_folder_check.require_usable_data_folder() is None
        ensure.assert_called_once_with("manager")
        assert capsys.readouterr().err == ""

    def test_data_folder_error_is_one_line_and_exit_13(self, mocker, capsys):
        mocker.patch.object(data_folder_check, "ensure_configured_data_root",
                            side_effect=data_folder.DataFolderError("D:\\x is a link or not a folder; choose a "
                                                                    "plain folder."))
        code, err = _exit(capsys)
        assert code == 13 == data_folder_check.EXIT_DATA_FOLDER
        assert err == ("[ERROR] The data folder cannot be used: D:\\x is a link or not a folder; choose a plain "
                       f"folder. {RESET}\n")

    def test_relative_path_value_error_is_exit_13(self, ini_path, capsys):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("[paths]\ndata_dir = relative/data\n")
        code, err = _exit(capsys)
        assert code == 13
        assert err.startswith("[ERROR] The data folder cannot be used: [paths] data_dir must be an absolute path")
        assert err.endswith(f". {RESET}\n") and err.count("\n") == 1

    def test_system_folder_from_the_settings_file_is_exit_13(self, ini_path, capsys):
        ini_path.parent.mkdir(parents=True)
        system = "C:\\Windows\\Sethlans" if sys.platform == "win32" else "/usr/sethlans"
        ini_path.write_text(f"[paths]\ndata_dir = {system}\n")
        code, err = _exit(capsys)
        assert code == 13 and "is inside a system folder" in err

    def test_other_errors_are_not_swallowed(self, mocker):
        mocker.patch.object(data_folder_check, "ensure_configured_data_root", side_effect=RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            data_folder_check.require_usable_data_folder()


@pytest.fixture
def run_manager_module(mocker):
    import run_manager

    calls = []
    mocker.patch("sethlans_manager.data_folder_check.require_usable_data_folder",
                 side_effect=lambda: calls.append("data_folder") or sys.exit(13))
    mocker.patch.object(run_manager, "setup_certificates", side_effect=lambda **kw: calls.append("certs"))
    return run_manager, calls


class TestRunManagerOrder:

    def test_check_runs_before_the_certificates(self, run_manager_module, monkeypatch):
        run_manager, calls = run_manager_module
        monkeypatch.setattr(sys, "argv", ["run_manager.py"])
        with pytest.raises(SystemExit) as info:
            run_manager.main()
        assert info.value.code == 13
        assert calls == ["data_folder"]

    def test_manage_dispatch_comes_first(self, run_manager_module, monkeypatch, mocker):
        run_manager, calls = run_manager_module
        monkeypatch.setattr(sys, "argv", ["run_manager.py", "--manage", "check"])
        mocker.patch("sethlans_manager.manage_dispatch.dispatch_manage_mode", side_effect=SystemExit(0))
        with pytest.raises(SystemExit) as info:
            run_manager.main()
        assert info.value.code == 0
        assert calls == []

    def test_dev_dispatch_comes_first(self, run_manager_module, monkeypatch, mocker):
        run_manager, calls = run_manager_module
        monkeypatch.setattr(sys, "argv", ["run_manager.py", "--dev"])
        mocker.patch.object(run_manager, "_dispatch_dev_mode", side_effect=SystemExit(0))
        with pytest.raises(SystemExit):
            run_manager.main()
        assert calls == []
