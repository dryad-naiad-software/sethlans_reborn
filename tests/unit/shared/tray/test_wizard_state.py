# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/wizard_state.py`` (spec FR-3, NFR-7).

``read_wizard_state(data_dir)`` reads ``<data_dir>/wizard/port`` and
``<data_dir>/wizard/setup_token``. It returns a ``WizardState`` only
when both files are present and well-formed. Otherwise ``None``.

Security invariant (NFR-7): the token VALUE must never appear in any
log record on any failure path. ``token_len=<N>`` is the only token
fact permitted in logs.
"""

from __future__ import annotations

import logging

from shared.tray import wizard_state as ws_mod
from shared.tray.wizard_state import WizardState, read_wizard_state


def _write_wizard_files(tmp_path, port=None, token=None):
    """Helper: create ``<tmp_path>/wizard/{port,setup_token}``."""
    wd = tmp_path / "wizard"
    wd.mkdir(exist_ok=True)
    if port is not None:
        (wd / "port").write_text(str(port), encoding="utf-8")
    if token is not None:
        (wd / "setup_token").write_text(token, encoding="utf-8")
    return wd


# ------------------------------------------------------------------ #
# Happy path
# ------------------------------------------------------------------ #

class TestHappyPath:

    def test_returns_state_when_both_files_present(self, tmp_path):
        _write_wizard_files(tmp_path, port=8443, token="tok-abc")
        state = read_wizard_state(tmp_path)
        assert state is not None
        assert isinstance(state, WizardState)
        assert state.port == 8443
        assert state.token == "tok-abc"

    def test_strips_whitespace_around_token_and_port(self, tmp_path):
        _write_wizard_files(
            tmp_path, port="  8443  \n", token="  tok-abc  \n",
        )
        state = read_wizard_state(tmp_path)
        assert state is not None
        assert state.port == 8443
        assert state.token == "tok-abc"

    def test_dataclass_is_frozen(self, tmp_path):
        _write_wizard_files(tmp_path, port=8443, token="tok-abc")
        state = read_wizard_state(tmp_path)
        # Frozen dataclass: assignment raises FrozenInstanceError.
        try:
            state.port = 9999
        except Exception:
            pass
        else:
            raise AssertionError("WizardState should be frozen")


# ------------------------------------------------------------------ #
# Missing-file branches return None
# ------------------------------------------------------------------ #

class TestMissingFiles:

    def test_returns_none_when_wizard_dir_absent(self, tmp_path):
        # No ``<tmp_path>/wizard/`` exists at all.
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_only_token_present(self, tmp_path):
        _write_wizard_files(tmp_path, token="tok-abc")
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_only_port_present(self, tmp_path):
        _write_wizard_files(tmp_path, port=8443)
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_token_empty(self, tmp_path):
        _write_wizard_files(tmp_path, port=8443, token="")
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_token_only_whitespace(self, tmp_path):
        _write_wizard_files(tmp_path, port=8443, token="   \n  ")
        assert read_wizard_state(tmp_path) is None


# ------------------------------------------------------------------ #
# Corrupt port values return None
# ------------------------------------------------------------------ #

class TestPortValidation:

    def test_returns_none_when_port_unparseable(self, tmp_path):
        _write_wizard_files(tmp_path, port="not-a-number", token="tok")
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_port_zero(self, tmp_path):
        _write_wizard_files(tmp_path, port=0, token="tok")
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_port_negative(self, tmp_path):
        _write_wizard_files(tmp_path, port=-1, token="tok")
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_port_above_max(self, tmp_path):
        _write_wizard_files(tmp_path, port=70000, token="tok")
        assert read_wizard_state(tmp_path) is None

    def test_accepts_min_port_one(self, tmp_path):
        _write_wizard_files(tmp_path, port=1, token="tok")
        state = read_wizard_state(tmp_path)
        assert state is not None
        assert state.port == 1

    def test_accepts_max_port_65535(self, tmp_path):
        _write_wizard_files(tmp_path, port=65535, token="tok")
        state = read_wizard_state(tmp_path)
        assert state is not None
        assert state.port == 65535


# ------------------------------------------------------------------ #
# Oversize files (>1 KB) return None
# ------------------------------------------------------------------ #

class TestOversizeFiles:

    def test_returns_none_when_port_oversize(self, tmp_path, caplog):
        wd = tmp_path / "wizard"
        wd.mkdir()
        # >1024 bytes.
        (wd / "port").write_text("8" * 2000, encoding="utf-8")
        (wd / "setup_token").write_text("tok", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger=ws_mod.logger.name):
            result = read_wizard_state(tmp_path)
        assert result is None
        assert any(
            "exceeds" in rec.getMessage() for rec in caplog.records
        )

    def test_returns_none_when_token_oversize(self, tmp_path, caplog):
        wd = tmp_path / "wizard"
        wd.mkdir()
        (wd / "port").write_text("8443", encoding="utf-8")
        oversize_token = "x" * 2000
        (wd / "setup_token").write_text(oversize_token, encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger=ws_mod.logger.name):
            result = read_wizard_state(tmp_path)
        assert result is None
        # Token value must NOT appear in logs even on oversize path.
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert oversize_token not in combined


# ------------------------------------------------------------------ #
# OSError tolerance — never raises
# ------------------------------------------------------------------ #

class TestOSErrorTolerance:

    def test_returns_none_when_stat_raises(self, tmp_path, mocker):
        _write_wizard_files(tmp_path, port=8443, token="tok")
        mocker.patch(
            "pathlib.Path.stat", side_effect=OSError("denied"),
        )
        # Must not raise.
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_exists_raises(self, tmp_path, mocker):
        mocker.patch(
            "pathlib.Path.exists", side_effect=OSError("denied"),
        )
        # Must not raise.
        assert read_wizard_state(tmp_path) is None

    def test_returns_none_when_read_text_raises(self, tmp_path, mocker):
        _write_wizard_files(tmp_path, port=8443, token="tok")
        mocker.patch(
            "pathlib.Path.read_text", side_effect=OSError("denied"),
        )
        # Must not raise.
        assert read_wizard_state(tmp_path) is None


# ------------------------------------------------------------------ #
# NFR-7: token VALUE never logged on any failure path
# ------------------------------------------------------------------ #

class TestTokenNeverLogged:
    """Spec NFR-7: failure paths may log ``token_len=<N>`` but never the
    token value itself."""

    SECRET = "super-SECRET-TOKEN-do-not-leak"

    def _write_with_secret(self, tmp_path, port_value):
        wd = tmp_path / "wizard"
        wd.mkdir(exist_ok=True)
        (wd / "port").write_text(port_value, encoding="utf-8")
        (wd / "setup_token").write_text(self.SECRET, encoding="utf-8")

    def test_token_not_logged_when_port_unparseable(
        self, tmp_path, caplog,
    ):
        self._write_with_secret(tmp_path, "not-a-number")
        with caplog.at_level(logging.DEBUG, logger=ws_mod.logger.name):
            result = read_wizard_state(tmp_path)
        assert result is None
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert self.SECRET not in combined

    def test_token_not_logged_when_port_out_of_range(
        self, tmp_path, caplog,
    ):
        self._write_with_secret(tmp_path, "70000")
        with caplog.at_level(logging.DEBUG, logger=ws_mod.logger.name):
            result = read_wizard_state(tmp_path)
        assert result is None
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert self.SECRET not in combined

    def test_token_len_is_acceptable_in_logs(self, tmp_path, caplog):
        """Sanity: ``token_len=<N>`` IS allowed; assert at least one
        warning record on the unparseable-port path mentions ``token_len``
        so we know the documented diagnostic is wired in."""
        self._write_with_secret(tmp_path, "not-a-number")
        with caplog.at_level(logging.WARNING, logger=ws_mod.logger.name):
            read_wizard_state(tmp_path)
        # Expect ``token_len=<N>`` in some warning record.
        token_len_str = f"token_len={len(self.SECRET)}"
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert token_len_str in combined

    def test_token_not_logged_on_happy_path(self, tmp_path, caplog):
        """Happy path must also stay silent about the token value."""
        self._write_with_secret(tmp_path, "8443")
        with caplog.at_level(logging.DEBUG, logger=ws_mod.logger.name):
            state = read_wizard_state(tmp_path)
        assert state is not None
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert self.SECRET not in combined
