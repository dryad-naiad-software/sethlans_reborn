# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/bootstrap.py`` — secrets,
port-file, and logging facets.

Exercises the FR-W6 secret-file read+unlink, the FR-CFG2 port-file
write, and the FR-W11 log rotation in isolation from the entry-point
script.

The serve-with-port-scan and signal-handler facets live in
``test_bootstrap_serve.py`` so each file stays under the 300-line
ceiling enforced by CLAUDE.md.
"""

from __future__ import annotations

import logging

import pytest

from wizard.sethlans_wizard import bootstrap


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
