# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/sethlans_manager/migration_runner.py``.

Covers the in-process and subprocess runners for migrations and
collectstatic, including success paths, error handling, and process
exit behavior.
"""

import subprocess
import sys

import pytest

from sethlans_manager import migration_runner
from sethlans_manager.migration_runner import (
    child_env,
    run_collectstatic_inprocess,
    run_collectstatic_subprocess,
    run_migrations_inprocess,
    run_migrations_subprocess,
)


# ---- The child processes' environment --------------------------------------

def test_child_env_drops_the_admin_variables(mocker):
    """FR-FS-2: the migrate/collectstatic children never see SETHLANS_ADMIN_* (any case); everything else is kept."""
    environ = {
        "SETHLANS_ADMIN_PASSWORD": "Admin-Secret-7e8f", "sethlans_admin_username": "dockadmin",
        "SETHLANS_ADMINISTRATOR": "kept", "SETHLANS_DB_ENGINE": "sqlite", "PATH": "/usr/bin",
    }
    mocker.patch.object(migration_runner, "os", mocker.Mock(environ=environ))

    assert child_env() == {"SETHLANS_ADMINISTRATOR": "kept", "SETHLANS_DB_ENGINE": "sqlite", "PATH": "/usr/bin"}


# ---- In-process migration runner -------------------------------------------

class TestRunMigrationsInprocess:

    def test_calls_migrate_command(self, mocker):
        mock_cmd = mocker.patch(
            'django.core.management.call_command',
        )
        run_migrations_inprocess()
        mock_cmd.assert_called_once_with('migrate', '--noinput')

    def test_exits_on_exception(self, mocker):
        mocker.patch(
            'django.core.management.call_command',
            side_effect=Exception("migration failed"),
        )
        with pytest.raises(SystemExit) as exc_info:
            run_migrations_inprocess()
        assert exc_info.value.code == 11


# ---- In-process collectstatic runner ---------------------------------------

class TestRunCollectstaticInprocess:

    def test_calls_collectstatic_command(self, mocker):
        mocker.patch('django.core.management.call_command')
        run_collectstatic_inprocess()
        from django.core.management import call_command
        call_command.assert_called_once_with(
            'collectstatic', '--noinput',
        )

    def test_does_not_exit_on_exception(self, mocker):
        """collectstatic failure is a warning, not fatal."""
        mocker.patch(
            'django.core.management.call_command',
            side_effect=Exception("static fail"),
        )
        # Should NOT raise SystemExit
        run_collectstatic_inprocess()


# ---- Subprocess migration runner ------------------------------------------

class TestRunMigrationsSubprocess:

    def test_calls_subprocess_with_correct_args(self, mocker):
        """FR-FS-2: migrate runs in a child process that gets child_env(), not the manager's environment."""
        env = mocker.patch.object(migration_runner, "child_env", return_value={"PATH": "/usr/bin"})
        mock_run = mocker.patch(
            'sethlans_manager.migration_runner.subprocess.run',
        )
        run_migrations_subprocess("/path/to/manage.py")
        mock_run.assert_called_once_with(
            [sys.executable, "/path/to/manage.py", "migrate"],
            check=True,
            env=env.return_value,
        )

    def test_exits_on_subprocess_failure(self, mocker):
        mocker.patch(
            'sethlans_manager.migration_runner.subprocess.run',
            side_effect=subprocess.CalledProcessError(1, "migrate"),
        )
        with pytest.raises(SystemExit) as exc_info:
            run_migrations_subprocess("/path/to/manage.py")
        assert exc_info.value.code == 11


# ---- Subprocess collectstatic runner --------------------------------------

class TestRunCollectstaticSubprocess:

    def test_calls_subprocess_with_correct_args(self, mocker):
        """FR-FS-2: collectstatic runs in a child process that gets child_env(), not the manager's environment."""
        env = mocker.patch.object(migration_runner, "child_env", return_value={"PATH": "/usr/bin"})
        mock_run = mocker.patch(
            'sethlans_manager.migration_runner.subprocess.run',
        )
        run_collectstatic_subprocess("/path/to/manage.py")
        mock_run.assert_called_once_with(
            [
                sys.executable, "/path/to/manage.py",
                "collectstatic", "--noinput",
            ],
            check=True,
            env=env.return_value,
        )

    def test_does_not_exit_on_subprocess_failure(self, mocker):
        """collectstatic subprocess failure is a warning, not fatal."""
        mocker.patch(
            'sethlans_manager.migration_runner.subprocess.run',
            side_effect=subprocess.CalledProcessError(
                1, "collectstatic",
            ),
        )
        # Should NOT raise SystemExit
        run_collectstatic_subprocess("/path/to/manage.py")
