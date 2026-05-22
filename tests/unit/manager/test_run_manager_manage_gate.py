# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the ``--manage`` gate in ``manager/run_manager.main()``.

Covers:
  - --manage gate intercepts before the production runtime boot
  - --dev (without --manage) still reaches _dispatch_dev_mode
  - Production boot path is unaffected when neither flag is present

These tests patch at the run_manager module boundary so Django and
the Waitress stack are never actually started.
"""

from __future__ import annotations

import pytest

import run_manager


class TestManageGateInterceptsBeforeBoot:
    """FR-MGMT1: when --manage is in argv, main() must dispatch through
    manage_dispatch.dispatch_manage_mode and never reach setup_certificates
    or the Waitress runtime.
    """

    def test_manage_flag_dispatches_and_never_reaches_certificates(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(
            run_manager.sys, "argv",
            ["run_manager", "--manage", "migrate", "--noinput"],
        )

        # Patch dispatch_manage_mode to simulate a SystemExit(0) (success path)
        dispatch_mock = mocker.patch(
            "sethlans_manager.manage_dispatch.dispatch_manage_mode",
            side_effect=SystemExit(0),
        )
        setup_certs = mocker.patch.object(run_manager, "setup_certificates")

        with pytest.raises(SystemExit) as excinfo:
            run_manager.main()

        assert excinfo.value.code == 0
        dispatch_mock.assert_called_once()
        # Production boot MUST NOT be reached when --manage is present
        setup_certs.assert_not_called()

    def test_manage_flag_runtime_never_starts(
        self, mocker, monkeypatch,
    ):
        """Neither _prepare_runtime nor _launch_waitress are called
        when --manage intercepts in main()."""
        monkeypatch.setattr(
            run_manager.sys, "argv",
            ["run_manager", "--manage", "apply_pending_setup",
             "--data-dir", "/tmp/test"],
        )
        mocker.patch(
            "sethlans_manager.manage_dispatch.dispatch_manage_mode",
            side_effect=SystemExit(0),
        )
        prepare = mocker.patch.object(run_manager, "_prepare_runtime")
        launch = mocker.patch.object(run_manager, "_launch_waitress")

        with pytest.raises(SystemExit):
            run_manager.main()

        prepare.assert_not_called()
        launch.assert_not_called()


class TestRuntimePathUnchangedWhenManageAbsent:
    """Regression: the --manage gate must not affect the production boot
    when --manage is absent from argv.

    main() should reach setup_certificates in normal source-mode startup.
    """

    def test_production_boot_proceeds_without_manage(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(
            run_manager.sys, "argv", ["run_manager"],
        )
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)

        fake_cert = mocker.MagicMock()
        setup_certs = mocker.patch.object(
            run_manager, "setup_certificates",
            return_value=("cert.pem", "key.pem", fake_cert),
        )
        mocker.patch.object(run_manager, "check_cert_expiry_warning")
        mocker.patch.object(run_manager, "is_frozen", return_value=False)
        mocker.patch(
            "sethlans_manager.migration_runner.run_migrations_subprocess",
        )
        mocker.patch(
            "sethlans_manager.migration_runner.run_collectstatic_subprocess",
        )
        mocker.patch.object(run_manager, "_prepare_runtime")
        mocker.patch.object(
            run_manager, "get_manager_bind", return_value=("0.0.0.0", "8080"),
        )
        mocker.patch.object(
            run_manager, "get_waitress_public_port", return_value=8090,
        )
        mocker.patch.object(
            run_manager, "get_waitress_internal_port", return_value=8088,
        )
        mocker.patch.object(run_manager, "_launch_waitress")
        mocker.patch(
            "sethlans_manager.manage_dispatch.dispatch_manage_mode",
        )

        run_manager.main()

        # setup_certificates was reached — production boot proceeded
        setup_certs.assert_called_once()

    def test_manage_dispatch_not_called_without_flag(
        self, mocker, monkeypatch,
    ):
        """dispatch_manage_mode must not be called when --manage is absent."""
        monkeypatch.setattr(
            run_manager.sys, "argv", ["run_manager"],
        )
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)

        fake_cert = mocker.MagicMock()
        mocker.patch.object(
            run_manager, "setup_certificates",
            return_value=("cert.pem", "key.pem", fake_cert),
        )
        mocker.patch.object(run_manager, "check_cert_expiry_warning")
        mocker.patch.object(run_manager, "is_frozen", return_value=False)
        mocker.patch(
            "sethlans_manager.migration_runner.run_migrations_subprocess",
        )
        mocker.patch(
            "sethlans_manager.migration_runner.run_collectstatic_subprocess",
        )
        mocker.patch.object(run_manager, "_prepare_runtime")
        mocker.patch.object(
            run_manager, "get_manager_bind", return_value=("0.0.0.0", "8080"),
        )
        mocker.patch.object(
            run_manager, "get_waitress_public_port", return_value=8090,
        )
        mocker.patch.object(
            run_manager, "get_waitress_internal_port", return_value=8088,
        )
        mocker.patch.object(run_manager, "_launch_waitress")

        dispatch_manage = mocker.patch(
            "sethlans_manager.manage_dispatch.dispatch_manage_mode",
        )

        run_manager.main()

        dispatch_manage.assert_not_called()


class TestRuntimePathUnchangedWhenDevPresentWithoutManage:
    """Regression: --dev (without --manage) still reaches _dispatch_dev_mode
    and does NOT invoke dispatch_manage_mode.
    """

    def test_dev_flag_without_manage_calls_dispatch_dev_mode(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)

        rdw = mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
            return_value=0,
        )
        dispatch_manage = mocker.patch(
            "sethlans_manager.manage_dispatch.dispatch_manage_mode",
        )
        setup_certs = mocker.patch.object(run_manager, "setup_certificates")

        with pytest.raises(SystemExit) as excinfo:
            run_manager.main()

        assert excinfo.value.code == 0
        rdw.assert_called_once()
        dispatch_manage.assert_not_called()
        setup_certs.assert_not_called()
