# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/run_wizard.py`` — successful-wiring paths.

Covers FR-W3 / FR-W6 / FR-W11 wiring through the entry-point with
``server.run`` patched so no TCP socket is ever bound.

Argparse / --print-url / missing-secret tests live in
``test_run_wizard_entry_cli.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

import importlib

# Fixtures (_ensure_wizard_on_path autouse, provisioned_data_dir) are
# provided by tests/unit/wizard/conftest.py.


class TestSuccessfulWiring:

    def test_main_invokes_server_run_when_secrets_present(
        self, provisioned_data_dir, monkeypatch,
    ):
        """Happy path: cert generated, app built, server.run called."""
        bootstrap = importlib.import_module(
            "wizard.sethlans_wizard.bootstrap",
        )

        invocations: list[dict] = []

        def fake_serve(app, cert_path, key_path, env_port, subdir):
            invocations.append(
                dict(
                    has_app=app is not None,
                    cert_path=cert_path,
                    key_path=key_path,
                    env_port=env_port,
                    subdir=subdir,
                ),
            )
            return 0

        monkeypatch.setattr(
            bootstrap, "serve_with_port_scan", fake_serve,
        )
        monkeypatch.setattr(
            bootstrap, "install_signal_handlers", lambda: None,
        )
        # Neuter the FR-W17 post-done polling thread so the test
        # doesn't leak a daemon thread that polls for 5 minutes
        # waiting for .wizard_done.
        monkeypatch.setattr(
            bootstrap, "start_post_done_threads",
            lambda *a, **kw: None,
        )

        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 0
        assert len(invocations) == 1
        inv = invocations[0]
        assert inv["has_app"] is True
        assert inv["cert_path"].name == "tls.crt"
        assert inv["key_path"].name == "tls.key"
        # No env override → env_port is None.
        assert inv["env_port"] is None

    def test_secrets_unlinked_after_successful_read(
        self, provisioned_data_dir, monkeypatch,
    ):
        """FR-W6 / SEC-MED-11: secrets are gone immediately after read."""
        bootstrap = importlib.import_module(
            "wizard.sethlans_wizard.bootstrap",
        )
        monkeypatch.setattr(
            bootstrap, "serve_with_port_scan",
            lambda *a, **kw: 0,
        )
        monkeypatch.setattr(
            bootstrap, "install_signal_handlers", lambda: None,
        )
        # Neuter the FR-W17 post-done polling thread so the test
        # doesn't leak a daemon thread that polls for 5 minutes
        # waiting for .wizard_done.
        monkeypatch.setattr(
            bootstrap, "start_post_done_threads",
            lambda *a, **kw: None,
        )

        data_dir, _, _ = provisioned_data_dir
        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 0
        token_path = data_dir / "wizard" / ".setup_token"
        secret_path = data_dir / "wizard" / ".ipc_secret"
        assert not token_path.exists(), "FR-W6 requires unlink after read"
        assert not secret_path.exists(), "FR-W6 requires unlink after read"

    def test_env_port_override_is_propagated(
        self, provisioned_data_dir, monkeypatch,
    ):
        """``SETHLANS_WIZARD_PORT`` reaches ``serve_with_port_scan``."""
        bootstrap = importlib.import_module(
            "wizard.sethlans_wizard.bootstrap",
        )
        captured: dict = {}

        def fake_serve(app, cert_path, key_path, env_port, subdir):
            captured["env_port"] = env_port
            return 0

        monkeypatch.setenv("SETHLANS_WIZARD_PORT", "8103")
        monkeypatch.setattr(
            bootstrap, "serve_with_port_scan", fake_serve,
        )
        monkeypatch.setattr(
            bootstrap, "install_signal_handlers", lambda: None,
        )
        # Neuter the FR-W17 post-done polling thread so the test
        # doesn't leak a daemon thread that polls for 5 minutes
        # waiting for .wizard_done.
        monkeypatch.setattr(
            bootstrap, "start_post_done_threads",
            lambda *a, **kw: None,
        )

        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 0
        assert captured["env_port"] == 8103

    def test_invalid_env_port_returns_non_zero(
        self, tmp_path, monkeypatch,
    ):
        """Malformed ``SETHLANS_WIZARD_PORT`` short-circuits with rc=2."""
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("SETHLANS_WIZARD_PORT", "not-a-port")
        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 2
