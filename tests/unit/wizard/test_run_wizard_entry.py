# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the wizard's entry-point wiring (``wizard/run_wizard.py``).

Covers the post-A1 wiring that ties cert / ipc / server into a runnable
HTTPS waitress server (FR-W1, FR-W3, FR-W6, FR-W11).

We exercise behaviour in two ways:
  * In-process by calling ``run_wizard.main()`` with patched
    ``server.run`` (so we never actually bind a TCP socket inside unit
    tests).
  * Subprocess for the ``--print-url`` headless-introspection path
    (also patched env so no privileged ports are touched).
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WIZARD_DIR = PROJECT_ROOT / "wizard"
RUN_WIZARD = WIZARD_DIR / "run_wizard.py"


@pytest.fixture(autouse=True)
def _ensure_wizard_on_path():
    """Make the ``wizard/`` script directory importable.

    Mirrors ``test_package_scaffold._ensure_wizard_on_path``.
    """
    wizard_dir = str(WIZARD_DIR)
    added = False
    if wizard_dir not in sys.path:
        sys.path.insert(0, wizard_dir)
        added = True
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(wizard_dir)
            except ValueError:
                pass


@pytest.fixture
def provisioned_data_dir(tmp_path, monkeypatch):
    """A tmp data dir with chmod-600 ``.setup_token`` + ``.ipc_secret``.

    Mirrors what the launcher's FR-L3a / FR-L4a writes immediately
    before spawning the wizard.
    """
    monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
    wizard_subdir = tmp_path / "wizard"
    wizard_subdir.mkdir(parents=True, exist_ok=True)

    token = b"setup-token-deadbeef-cafe-ffff"
    secret = b"ipc-hmac-secret-bytes-aaaa-bbbb"
    (wizard_subdir / ".setup_token").write_bytes(token)
    (wizard_subdir / ".ipc_secret").write_bytes(secret)
    if os.name != "nt":
        os.chmod(wizard_subdir / ".setup_token", 0o600)
        os.chmod(wizard_subdir / ".ipc_secret", 0o600)
    return tmp_path, token, secret


# ---------------------------------------------------------------------
# argparse / scaffold sanity
# ---------------------------------------------------------------------

def test_run_wizard_module_imports():
    module = importlib.import_module("run_wizard")
    assert module is not None
    assert hasattr(module, "main")
    assert hasattr(module, "build_parser")


def test_argparse_flags_defined():
    module = importlib.import_module("run_wizard")
    parser = module.build_parser()
    flags = {
        a.option_strings[0] for a in parser._actions if a.option_strings
    }
    assert "--version" in flags
    assert "--no-browser" in flags
    assert "--print-url" in flags


def test_version_flag_exits_zero():
    """``run_wizard.py --version`` still works (FR-W1, AC-W1)."""
    result = subprocess.run(
        [sys.executable, str(RUN_WIZARD), "--version"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=15,
    )
    assert result.returncode == 0
    assert "sethlans-wizard" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------
# --print-url path
# ---------------------------------------------------------------------

class TestPrintUrl:

    def test_main_prints_url_and_returns_zero(
        self, provisioned_data_dir, capsys,
    ):
        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main(["--print-url"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Wizard URL: https://localhost:" in captured.out

    def test_print_url_does_not_consume_secrets(
        self, provisioned_data_dir,
    ):
        """``--print-url`` exits before reading ``.setup_token``.

        Important: a CI introspection invocation must not unlink the
        launcher's secret files (FR-W6 unlink is gated on actually
        starting the server).
        """
        data_dir, _, _ = provisioned_data_dir
        run_wizard = importlib.import_module("run_wizard")
        run_wizard.main(["--print-url"])
        token_path = data_dir / "wizard" / ".setup_token"
        secret_path = data_dir / "wizard" / ".ipc_secret"
        assert token_path.exists(), "--print-url must not consume secrets"
        assert secret_path.exists(), "--print-url must not consume secrets"

    def test_print_url_no_browser_combo(
        self, provisioned_data_dir, capsys,
    ):
        """``--print-url --no-browser`` is accepted and prints both lines."""
        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main(["--print-url", "--no-browser"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Wizard URL:" in captured.out
        assert "--no-browser" in captured.out


# ---------------------------------------------------------------------
# Missing-secret-file failure paths
# ---------------------------------------------------------------------

class TestMissingSecrets:

    def test_missing_setup_token_exits_non_zero(
        self, tmp_path, monkeypatch, capsys,
    ):
        """No ``.setup_token`` → log + return 2 before any port bind.

        We assert via stdout (capsys) rather than caplog because the
        wizard's ``configure_logging`` step replaces the root logger's
        handlers with its own console + file handlers, which sidesteps
        pytest's ``caplog`` propagation hook.
        """
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        wizard_subdir = tmp_path / "wizard"
        wizard_subdir.mkdir()
        # Only the ipc secret is written; .setup_token is missing.
        (wizard_subdir / ".ipc_secret").write_bytes(b"ipc-secret")

        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 2
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "secret file missing" in combined
        assert ".setup_token" in combined

    def test_missing_ipc_secret_exits_non_zero(
        self, tmp_path, monkeypatch, capsys,
    ):
        """No ``.ipc_secret`` → log + return 2."""
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        wizard_subdir = tmp_path / "wizard"
        wizard_subdir.mkdir()
        (wizard_subdir / ".setup_token").write_bytes(b"setup-token")

        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 2
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "secret file missing" in combined
        assert ".ipc_secret" in combined

    def test_both_missing_exits_non_zero(
        self, tmp_path, monkeypatch,
    ):
        """No ``.setup_token`` AND no ``.ipc_secret`` → return 2."""
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        run_wizard = importlib.import_module("run_wizard")
        rc = run_wizard.main([])
        assert rc == 2


# ---------------------------------------------------------------------
# Successful wiring (server.run is patched so no TCP bind happens)
# ---------------------------------------------------------------------

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
