# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Argv-parsing and allowlist unit tests for ``sethlans_manager/manage_dispatch.py``.

Complements the dev smoke tests in ``test_run_manager_manage_mode.py``
with:
  - explicit-argv parameter testability hook
  - --manage + --dev mutual exclusion
  - --manage without subcommand
  - duplicate --manage rejection
  - allowlist check happens BEFORE django.setup (ordering contract)

All tests that call dispatch_manage_mode stub out
setup_django_for_management so no real Django initialization happens.
"""

from __future__ import annotations

import pytest

from sethlans_manager import manage_dispatch


class TestDispatchManageModeExplicitArgv:
    """The argv parameter makes dispatch_manage_mode directly testable
    without patching sys.argv (FR-MGMT2 testability hook).
    """

    def test_explicit_argv_migrate(self, mocker):
        """dispatch_manage_mode(argv=[...]) with migrate routes to Django
        with ["manage", "migrate", "--noinput"]."""
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        exec_mock = mocker.MagicMock(return_value=None)
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "migrate", "--noinput"],
            )

        assert excinfo.value.code == 0
        exec_mock.assert_called_once_with(["manage", "migrate", "--noinput"])

    def test_apply_pending_setup_data_dir_preserved_verbatim(self, mocker):
        """The --data-dir path must arrive at execute_from_command_line
        exactly as supplied — no normalization, no stripping.

        FR-MGMT1 item 1: everything after --manage is passed through verbatim.
        """
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        exec_mock = mocker.MagicMock(return_value=None)
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        data_path = r"C:\Users\test user\AppData\Local\Sethlans\manager"
        argv = [
            "run_manager", "--manage", "apply_pending_setup",
            "--data-dir", data_path,
        ]

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(argv=argv)

        assert excinfo.value.code == 0
        exec_mock.assert_called_once_with(
            ["manage", "apply_pending_setup", "--data-dir", data_path],
        )


class TestManageAndDevMutuallyExclusive:
    """FR-MGMT2: --manage and --dev together must exit 2 with the spec's
    exact error message before any Django initialization.
    """

    def test_exits_2_with_message(self, mocker, capsys):
        """Top-level --dev (before --manage) + --manage must exit 2.

        Note: --dev appearing AFTER --manage is the subcommand's own
        argument and is NOT a top-level flag —
        see test_dev_after_manage_subcommand_value_does_not_trip_mutex.
        """
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )
        exec_mock = mocker.MagicMock()
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--dev", "--manage", "migrate"],
            )

        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err
        setup_mock.assert_not_called()
        exec_mock.assert_not_called()

    def test_order_of_flags_does_not_matter(self, mocker, capsys):
        """--dev before --manage should also be rejected."""
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--dev", "--manage", "migrate"],
            )
        assert excinfo.value.code == 2
        assert "mutually exclusive" in capsys.readouterr().err

    def test_dev_after_manage_subcommand_value_does_not_trip_mutex(
        self, mocker,
    ):
        """A literal "--dev" arg appearing AFTER --manage (e.g. as a
        --data-dir value) must NOT false-positive the top-level mutex.
        Only top-level --dev (before --manage) should be rejected.
        """
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        exec_mock = mocker.MagicMock(return_value=None)
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        argv = [
            "run_manager", "--manage", "apply_pending_setup",
            "--data-dir", "--dev",
        ]

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(argv=argv)

        # Success path — Django got the args verbatim, no mutex error.
        assert excinfo.value.code == 0
        exec_mock.assert_called_once_with(
            ["manage", "apply_pending_setup", "--data-dir", "--dev"],
        )


class TestDuplicateManageFlagRejected:
    """Defense-in-depth: argv with --manage specified more than once is
    ambiguous (which subcommand wins?) and must be rejected up front so
    a second --manage can't slip through as a subcommand argument and
    bypass the allowlist intent.
    """

    def test_duplicate_manage_flag_rejected(self, mocker, capsys):
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )
        exec_mock = mocker.MagicMock()
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "migrate",
                      "--manage", "shell"],
            )

        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "more than once" in captured.err
        # Neither Django setup nor dispatch was reached.
        setup_mock.assert_not_called()
        exec_mock.assert_not_called()


class TestManageWithoutSubcommand:
    """FR-MGMT2: --manage with no following subcommand must exit 2 with
    a clear message. Django must not be initialized.
    """

    def test_exits_2_with_message(self, mocker, capsys):
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage"],
            )

        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "requires a Django management command name" in captured.err
        setup_mock.assert_not_called()


class TestAllowlistCheckBeforeDjangoSetup:
    """FR-MGMT3 ordering contract: the allowlist check must run BEFORE
    setup_django_for_management() is called.

    The dev smoke tests assert setup_django_for_management is not called
    for disallowed commands but don't assert against an otherwise-allowed
    mocked path. This test locks in the ordering guarantee explicitly by
    verifying neither setup_django_for_management nor
    execute_from_command_line is ever reached on a disallowed subcommand.
    """

    def test_disallowed_subcommand_neither_setup_nor_execute_called(
        self, mocker,
    ):
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )
        exec_mock = mocker.MagicMock()
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "shell"],
            )

        assert excinfo.value.code == 2
        # Critical: NEITHER was called — the allowlist fired first
        setup_mock.assert_not_called()
        exec_mock.assert_not_called()

    def test_disallowed_createsuperuser_blocked(self, mocker, capsys):
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "createsuperuser"],
            )
        assert excinfo.value.code == 2
        assert "not in allowlist" in capsys.readouterr().err

    def test_disallowed_dumpdata_blocked(self, mocker, capsys):
        mocker.patch.object(manage_dispatch, "setup_django_for_management")
        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(
                argv=["run_manager", "--manage", "dumpdata"],
            )
        assert excinfo.value.code == 2
        assert "dumpdata" in capsys.readouterr().err
