# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/bootstrap.py``.

Exercises the FR-W3 port-file write, FR-W6 secret-file read+unlink,
FR-W11 log rotation, FR-W17 SIGTERM handler installation, and the
FR-W3 port-scan loop in isolation from the entry-point script.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from wizard.sethlans_wizard import bootstrap, server


# ---------------------------------------------------------------------
# read_secrets (FR-W6 / SEC-MED-11)
# ---------------------------------------------------------------------

class TestReadSecrets:

    def test_reads_and_unlinks_both(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / ".setup_token").write_bytes(b"  token-bytes  ")
        (subdir / ".ipc_secret").write_bytes(b"secret-bytes\n")

        token, secret = bootstrap.read_secrets(subdir)
        assert token == b"token-bytes"
        assert secret == b"secret-bytes"
        assert not (subdir / ".setup_token").exists()
        assert not (subdir / ".ipc_secret").exists()

    def test_missing_token_raises_filenotfound(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / ".ipc_secret").write_bytes(b"x")
        with pytest.raises(FileNotFoundError):
            bootstrap.read_secrets(subdir)

    def test_missing_secret_raises_filenotfound(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / ".setup_token").write_bytes(b"x")
        with pytest.raises(FileNotFoundError):
            bootstrap.read_secrets(subdir)

    def test_empty_token_raises_value_error(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / ".setup_token").write_bytes(b"   ")
        (subdir / ".ipc_secret").write_bytes(b"x")
        with pytest.raises(ValueError, match="setup token"):
            bootstrap.read_secrets(subdir)

    def test_empty_secret_raises_value_error(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / ".setup_token").write_bytes(b"tok")
        (subdir / ".ipc_secret").write_bytes(b"")
        with pytest.raises(ValueError, match="IPC secret"):
            bootstrap.read_secrets(subdir)


# ---------------------------------------------------------------------
# write_port_file (FR-CFG2)
# ---------------------------------------------------------------------

class TestWritePortFile:

    def test_writes_atomically(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        bootstrap.write_port_file(subdir, 8101)
        port_file = subdir / "port"
        assert port_file.exists()
        assert port_file.read_text(encoding="ascii").strip() == "8101"
        # No leftover .tmp.
        assert not (subdir / "port.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / "port").write_text("9999\n")
        bootstrap.write_port_file(subdir, 8102)
        assert (subdir / "port").read_text(encoding="ascii").strip() == "8102"

    def test_failure_is_logged_not_raised(self, tmp_path, caplog):
        # Point at a non-existent parent directory; OSError is caught.
        subdir = tmp_path / "does-not-exist" / "wizard"
        with caplog.at_level("WARNING"):
            bootstrap.write_port_file(subdir, 8100)
        assert any(
            "Could not write wizard port file" in rec.message
            for rec in caplog.records
        )


# ---------------------------------------------------------------------
# configure_logging (FR-W11)
# ---------------------------------------------------------------------

class TestConfigureLogging:

    def test_creates_log_file(self, tmp_path):
        subdir = tmp_path / "wizard"
        try:
            bootstrap.configure_logging(subdir)
            logging.getLogger("test").info("hello")
            log_path = subdir / "wizard.log"
            assert log_path.exists()
        finally:
            for h in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass

    def test_rotates_existing_log_to_prev(self, tmp_path):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / "wizard.log").write_text("old log content")
        try:
            bootstrap.configure_logging(subdir)
        finally:
            for h in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        prev = subdir / "wizard.log.prev"
        assert prev.exists()
        assert prev.read_text() == "old log content"

    def test_drops_older_prev_before_rotation(self, tmp_path):
        """SEC-LOW-4: max 1 prior log retained."""
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        (subdir / "wizard.log.prev").write_text("ancient log")
        (subdir / "wizard.log").write_text("yesterday's log")
        try:
            bootstrap.configure_logging(subdir)
        finally:
            for h in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        prev = subdir / "wizard.log.prev"
        assert prev.exists()
        assert prev.read_text() == "yesterday's log"


# ---------------------------------------------------------------------
# serve_with_port_scan (FR-W3)
# ---------------------------------------------------------------------

class TestServeWithPortScan:

    def test_env_port_uses_single_port(self, tmp_path, monkeypatch):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            attempts.append(port)
            return None  # clean shutdown

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=8103,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8103]
        assert (subdir / "port").read_text().strip() == "8103"

    def test_env_port_bind_failure_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port, cert_path, key_path):
            raise OSError("port in use")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=8100,
            subdir=subdir,
        )
        assert rc == 2

    def test_default_scans_until_one_binds(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            attempts.append(port)
            if port < 8102:
                raise OSError("port in use")
            return None  # binds on third candidate

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=None,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8100, 8101, 8102]
        assert (subdir / "port").read_text().strip() == "8102"

    def test_default_all_ports_busy_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port, cert_path, key_path):
            raise OSError("everything is busy")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=None,
            subdir=subdir,
        )
        assert rc == 2

    def test_app_wizard_port_is_restamped(
        self, tmp_path, monkeypatch,
    ):
        """The done-marker port must reflect the port we actually bound."""
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        seen_ports: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            seen_ports.append(app._wizard_port)
            if port < 8101:
                raise OSError("nope")
            return None

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        app = FakeApp()
        rc = bootstrap.serve_with_port_scan(
            app, Path("cert"), Path("key"),
            env_port=None, subdir=subdir,
        )
        assert rc == 0
        assert seen_ports == [8100, 8101]
        assert app._wizard_port == 8101


# ---------------------------------------------------------------------
# install_signal_handlers (FR-W17)
# ---------------------------------------------------------------------

class TestInstallSignalHandlers:

    def test_does_not_raise_in_main_thread(self):
        """Bare smoke: running it on the test thread doesn't blow up."""
        # signal.signal raises ValueError on non-main threads; the
        # helper swallows that. On the test main thread, it should
        # install successfully.
        bootstrap.install_signal_handlers()
