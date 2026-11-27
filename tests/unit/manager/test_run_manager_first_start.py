# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the first-start call site in ``manager/run_manager.py``
(FR-FS-0): ``_run_first_start`` and its place in ``main()``.

Every boot step is mocked; Django, migrations and Waitress never run.
"""
from pathlib import Path

import pytest

import run_manager

INI = Path("manager") / "sethlans.ini"


@pytest.fixture
def boot(mocker, monkeypatch):
    """``main()`` with every boot step mocked; the returned mock records their order."""
    monkeypatch.setattr(run_manager.sys, "argv", ["run_manager"])
    steps = mocker.Mock()
    mocker.patch("sethlans_manager.data_folder_check.require_usable_data_folder")
    mocker.patch.object(run_manager, "setup_certificates", return_value=("cert.pem", "key.pem", mocker.Mock()))
    mocker.patch.object(run_manager, "check_cert_expiry_warning")
    mocker.patch.object(run_manager, "is_frozen", return_value=False)
    mocker.patch("sethlans_manager.migration_runner.run_migrations_subprocess", steps.migrate)
    mocker.patch("sethlans_manager.migration_runner.run_collectstatic_subprocess", steps.collectstatic)
    mocker.patch.object(run_manager, "get_manager_bind", return_value=("0.0.0.0", "8080"))
    mocker.patch.object(run_manager, "_prepare_runtime", steps.prepare_runtime)
    mocker.patch.object(run_manager, "_run_first_start", steps.first_start)
    mocker.patch.object(run_manager, "get_config_file_path", return_value=INI)
    mocker.patch.object(run_manager, "get_waitress_public_port", return_value=8090)
    mocker.patch.object(run_manager, "get_waitress_internal_port", return_value=8088)
    mocker.patch.object(run_manager, "_launch_waitress", steps.launch_waitress)
    return steps


def _order(steps):
    return [name for name, _args, _kwargs in steps.mock_calls]


@pytest.mark.parametrize("exit_code", [None, 12], ids=["first-start-ok", "first-start-exits"])
def test_first_start_runs_after_runtime_and_before_waitress(boot, exit_code):
    """FR-FS-0: after migrations and _prepare_runtime, before _launch_waitress; an exit means Waitress never starts."""
    if exit_code is None:
        run_manager.main()
        expected_tail = ["first_start", "launch_waitress"]
    else:
        boot.first_start.side_effect = SystemExit(exit_code)
        with pytest.raises(SystemExit) as caught:
            run_manager.main()
        assert caught.value.code == exit_code
        expected_tail = ["first_start"]

    assert _order(boot) == ["migrate", "collectstatic", "prepare_runtime", *expected_tail]


def test_run_first_start_uses_the_settings_file(mocker):
    """FR-FS-0: the step reads the manager's own settings file."""
    mocker.patch.object(run_manager, "get_config_file_path", return_value=INI)
    require = mocker.patch("sethlans_manager.first_start.require_first_start")

    run_manager._run_first_start()

    require.assert_called_once_with(INI)
