# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Django-integration unit tests for ``sethlans_manager/manage_dispatch.py``.

Complements the argv/allowlist tests in ``test_manage_dispatch_argv.py``
with:
  - setup_django_for_management idempotency (apps.ready guard)
  - setup_django_for_management never calls run_migrations_inprocess
  - Django errors propagate verbatim (no SystemExit wrapping)

All tests that call dispatch_manage_mode stub out
setup_django_for_management so no real Django initialization happens.
"""

from __future__ import annotations

import pytest

from sethlans_manager import manage_dispatch


class TestSetupDjangoForManagementIdempotency:
    """FR-MGMT4 apps.ready guard: calling setup_django_for_management()
    twice must not raise RuntimeError from Django's apps.populate() guard.

    This test is a unit-level proof that the helper is safe to call in
    test sessions where Django has already been initialized.
    """

    def test_no_double_call_error(self, mocker):
        """Simulate apps.ready = True (Django already initialized) and
        assert the second call is silently skipped."""
        # Patch apps.ready to True to simulate already-initialized Django
        mock_apps = mocker.patch("django.apps.apps")
        mock_apps.ready = True

        # If setup_django_for_management tried to call django.setup() again
        # it would raise; with apps.ready guard it must skip silently.
        django_setup = mocker.patch("django.setup")
        mocker.patch(
            "sethlans_manager.logging_config.configure",
        )

        # Should not raise
        manage_dispatch.setup_django_for_management()
        manage_dispatch.setup_django_for_management()

        # django.setup() must not have been called since apps was ready
        django_setup.assert_not_called()

    def test_calls_django_setup_when_not_ready(self, mocker):
        """When apps.ready = False, django.setup() IS called exactly once."""
        mock_apps = mocker.patch("django.apps.apps")
        mock_apps.ready = False

        django_setup = mocker.patch("django.setup")
        mocker.patch("sethlans_manager.logging_config.configure")

        manage_dispatch.setup_django_for_management()

        django_setup.assert_called_once()

    def test_setup_logging_only_runs_when_django_setup_runs(self, mocker):
        """logging_config.configure() must be paired with django.setup() —
        if apps.ready was already True on entry, _cfg() must NOT run.

        Calling setup_django_for_management() twice in a row from a state
        where apps.ready starts False, then becomes True after the first
        django.setup(), must result in configure() being invoked exactly
        once (during the first call, alongside django.setup()).
        """
        mock_apps = mocker.patch("django.apps.apps")
        # First call sees ready=False (runs setup + _cfg);
        # second call sees ready=True (skips both).
        mock_apps.ready = False

        django_setup = mocker.patch("django.setup")
        configure_mock = mocker.patch(
            "sethlans_manager.logging_config.configure",
        )

        manage_dispatch.setup_django_for_management()
        # Simulate Django having become ready as a side effect of the first
        # django.setup() call.
        mock_apps.ready = True
        manage_dispatch.setup_django_for_management()

        # django.setup() ran once (on the first call when ready was False).
        django_setup.assert_called_once()
        # configure() must also run exactly once — paired with the
        # django.setup() call, NOT on the second pass.
        configure_mock.assert_called_once()


class TestSetupDjangoForManagementNeverCallsMigrations:
    """FR-MGMT4 hard constraint: setup_django_for_management() must NEVER
    call run_migrations_inprocess. Migrations are owned by the caller's
    argv (--manage migrate).
    """

    def test_run_migrations_inprocess_not_called(self, mocker):
        mock_apps = mocker.patch("django.apps.apps")
        mock_apps.ready = True  # skip real django.setup
        mocker.patch("sethlans_manager.logging_config.configure")

        # Patch migration runner at the module level
        migrate_mock = mocker.patch(
            "sethlans_manager.migration_runner.run_migrations_inprocess",
        )

        manage_dispatch.setup_django_for_management()

        migrate_mock.assert_not_called()


class TestUnknownAllowlistedCommandSurfacesDjangoError:
    """FR-MGMT1 item 4: if an allowlisted subcommand with a bogus flag is
    dispatched, execute_from_command_line itself raises SystemExit (Django
    decides the exit code). The dispatch must NOT wrap it — the exit
    propagates verbatim.

    This is the "no try/except SystemExit" contract.
    """

    def test_django_system_exit_propagates_verbatim(self, mocker):
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        # Simulate Django raising SystemExit(2) on bad flag
        exec_mock = mocker.MagicMock(side_effect=SystemExit(2))
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "migrate", "--bogus-flag"],
            )

        # Django's exit code (2) must propagate unchanged
        assert excinfo.value.code == 2
        exec_mock.assert_called_once_with(
            ["manage", "migrate", "--bogus-flag"],
        )

    def test_django_exit_code_1_propagates(self, mocker):
        """CommandError from Django exits with 1; dispatch must not absorb it."""
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        exec_mock = mocker.MagicMock(side_effect=SystemExit(1))
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "migrate"],
            )

        assert excinfo.value.code == 1
