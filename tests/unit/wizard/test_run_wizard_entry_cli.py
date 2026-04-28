# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/run_wizard.py`` — argparse, --print-url, and
missing-secret failure paths.

The successful-wiring (server.run patched) tests live in
``test_run_wizard_entry_wiring.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

from ._run_wizard_helpers import PROJECT_ROOT, RUN_WIZARD

# Fixtures (_ensure_wizard_on_path autouse, provisioned_data_dir) are
# provided by tests/unit/wizard/conftest.py.


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
        # Issue #170: wizard now binds plain HTTP on loopback; banner
        # is the loopback URL Caddy reverse-proxies to.
        assert (
            "Wizard loopback URL: http://127.0.0.1:" in captured.out
        )

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
        assert "Wizard loopback URL:" in captured.out
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
