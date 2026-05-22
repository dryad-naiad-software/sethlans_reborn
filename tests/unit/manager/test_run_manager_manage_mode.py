# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for #191 ``--manage <subcommand>`` dispatch in ``run_manager``.

The full per-branch matrix lives in the unit-test agent's deliverable;
this file is the minimum the dev stage needs to prove the implementation
shape is correct (FR-MGMT1, FR-MGMT2 allowlist).
"""

from __future__ import annotations

import pytest

from sethlans_manager import manage_dispatch


class TestDispatchManageMode:

    def test_argv_routed_to_execute_from_command_line(
        self, mocker, monkeypatch, capsys,
    ):
        """``--manage migrate --noinput`` reaches Django with the expected
        argv shape (``["manage", "migrate", "--noinput"]``)."""
        monkeypatch.setattr(
            manage_dispatch, "sys", manage_dispatch.sys,
        )
        # setup_django_for_management must be stubbed so the test does
        # not actually call django.setup() — spec FR-MGMT4 / test-isolation
        # note. We assert the dispatcher REACHES it before
        # execute_from_command_line.
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )
        exec_mock = mocker.MagicMock(return_value=None)
        # Patch the dotted name where dispatch_manage_mode looks it up.
        mocker.patch(
            "django.core.management.execute_from_command_line", exec_mock,
        )

        argv = ["run_manager", "--manage", "migrate", "--noinput"]
        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(argv)
        assert excinfo.value.code == 0

        setup_mock.assert_called_once()
        exec_mock.assert_called_once_with(["manage", "migrate", "--noinput"])

    def test_allowlist_rejects_unknown_subcommand_before_django_init(
        self, mocker,
    ):
        """``--manage shell`` (not in allowlist) exits 2 with the spec's
        message AND does NOT call ``setup_django_for_management``."""
        setup_mock = mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )

        argv = ["run_manager", "--manage", "shell"]
        with pytest.raises(SystemExit) as excinfo:
            manage_dispatch.dispatch_manage_mode(argv)

        assert excinfo.value.code == 2
        # Critical: Django was NOT initialized for the rejected command.
        setup_mock.assert_not_called()

    def test_allowlist_rejection_message_on_stderr(self, mocker, capsys):
        """Stderr message format matches FR-MGMT3 verbatim."""
        mocker.patch.object(
            manage_dispatch, "setup_django_for_management",
        )

        with pytest.raises(SystemExit):
            manage_dispatch.dispatch_manage_mode(
                ["run_manager", "--manage", "createsuperuser"],
            )
        captured = capsys.readouterr()
        assert (
            "manage subcommand 'createsuperuser' not in allowlist"
            in captured.err
        )
