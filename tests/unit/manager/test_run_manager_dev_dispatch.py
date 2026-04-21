# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the Phase 6 ``--dev`` dispatch path in
``run_manager.main``.

Covers:

* ``--dev`` without ``SETHLANS_DEV_IS_PARENT`` → parent path; calls
  ``run_dev_watchdog`` and exits with its rc; never reaches
  ``setup_certificates``.
* ``--dev`` with ``SETHLANS_DEV_IS_PARENT=1`` (pathological — child
  should not see ``--dev``) → defensive warning to stderr, then falls
  through to production boot.
"""

from __future__ import annotations

import pytest

import run_manager


class TestDispatchDevParent:

    def test_parent_invokes_watchdog_and_exits(
        self, mocker, monkeypatch,
    ):
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        rdw = mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
            return_value=7,
        )
        setup_certs = mocker.patch.object(
            run_manager, "setup_certificates",
        )

        with pytest.raises(SystemExit) as excinfo:
            run_manager.main()

        # run_dev_watchdog returns 7 → main exits with 7
        assert excinfo.value.code == 7
        rdw.assert_called_once()
        setup_certs.assert_not_called()

    def test_parent_does_not_import_wsgi_or_setup_django(
        self, mocker, monkeypatch,
    ):
        """The watchdog parent must not trigger Django boot. We assert
        that ``setup_certificates`` and the migration helpers are not
        touched in the ``--dev`` parent path."""
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
            return_value=0,
        )
        setup_certs = mocker.patch.object(
            run_manager, "setup_certificates",
        )
        prepare_runtime = mocker.patch.object(
            run_manager, "_prepare_runtime",
        )

        with pytest.raises(SystemExit):
            run_manager.main()

        setup_certs.assert_not_called()
        prepare_runtime.assert_not_called()


class TestDispatchDevChildDefensive:
    """The child should never see ``--dev`` (the parent strips it
    before spawn). If it ever does, we want a defensive warning, not a
    crash or an infinite respawn loop."""

    def test_child_with_dev_falls_through_and_warns(
        self, mocker, monkeypatch, capsys,
    ):
        monkeypatch.setenv("SETHLANS_DEV_IS_PARENT", "1")
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        # Stub the production boot so main() does not actually boot.
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
        mocker.patch.object(run_manager, "get_manager_bind", return_value=("0.0.0.0", "8080"))
        mocker.patch.object(run_manager, "get_waitress_public_port", return_value=8090)
        mocker.patch.object(run_manager, "get_waitress_internal_port", return_value=8088)
        mocker.patch.object(run_manager, "_launch_waitress")
        # Must not invoke the watchdog when we're the child.
        rdw = mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
        )

        run_manager.main()

        rdw.assert_not_called()
        err = capsys.readouterr().err
        assert "--dev" in err
        assert "child" in err.lower()
