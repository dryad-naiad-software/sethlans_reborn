# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/ipc.py`` (tray spec FR-20 / FR-20a / FR-20b)."""

from __future__ import annotations

import json
import os

import pytest

from shared.tray import ipc as ipc_mod
from shared.tray.ipc import (
    ENV_SECRET,
    MARKER_QUIT,
    MARKER_RESTART,
    marker_exists,
    request_quit,
    request_restart,
)


_SECRET = "test-secret-abc123"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv(ENV_SECRET, _SECRET)


class TestRequestRestart:

    def test_writes_restart_marker(self, tmp_path):
        path = request_restart(tmp_path)
        assert path == tmp_path / MARKER_RESTART
        assert path.exists()

    def test_restart_payload_contains_envelope_fields(self, tmp_path):
        path = request_restart(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["secret"] == _SECRET
        assert payload["pid"] == os.getpid()
        assert payload["target"] == "manager"
        assert "requested_at" in payload


class TestRequestQuit:

    def test_writes_quit_marker_all(self, tmp_path):
        path = request_quit(tmp_path, target="all")
        assert path == tmp_path / MARKER_QUIT
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["target"] == "all"
        assert payload["secret"] == _SECRET
        assert payload["pid"] == os.getpid()

    def test_writes_quit_marker_manager(self, tmp_path):
        path = request_quit(tmp_path, target="manager")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["target"] == "manager"

    def test_rejects_unknown_target(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid IPC target"):
            request_quit(tmp_path, target="destroy")

    def test_rejects_empty_target(self, tmp_path):
        with pytest.raises(ValueError):
            request_quit(tmp_path, target="")


class TestAtomicWrite:

    def test_uses_tempfile_mkstemp_and_os_replace(self, tmp_path, mocker):
        # Spies ensure tempfile+os.replace path is used, not direct write.
        spy_mkstemp = mocker.spy(ipc_mod.tempfile, "mkstemp")
        spy_replace = mocker.spy(ipc_mod.os, "replace")
        request_quit(tmp_path, target="all")
        assert spy_mkstemp.call_count == 1
        assert spy_replace.call_count == 1
        # mkstemp directory is the target data_dir.
        _, kwargs = spy_mkstemp.call_args
        assert kwargs.get("dir") == str(tmp_path)

    def test_no_leftover_tempfile_on_success(self, tmp_path):
        request_quit(tmp_path, target="all")
        leftovers = [
            p for p in tmp_path.iterdir()
            if p.name.startswith(f"{MARKER_QUIT}.tmp.")
        ]
        assert leftovers == []

    def test_tempfile_removed_on_replace_failure(self, tmp_path, mocker):
        mocker.patch.object(
            ipc_mod.os, "replace",
            side_effect=OSError("replace failed"),
        )
        with pytest.raises(OSError):
            request_quit(tmp_path, target="all")
        # No tempfile should linger.
        leftovers = [
            p for p in tmp_path.iterdir()
            if p.name.startswith(f"{MARKER_QUIT}.tmp.")
        ]
        assert leftovers == []


class TestSecretHandling:

    def test_secret_read_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_SECRET, "another-secret")
        path = request_restart(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["secret"] == "another-secret"

    def test_missing_secret_writes_empty_and_warns(
        self, tmp_path, monkeypatch, caplog,
    ):
        monkeypatch.delenv(ENV_SECRET, raising=False)
        import logging
        with caplog.at_level(logging.WARNING, logger=ipc_mod.logger.name):
            path = request_restart(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["secret"] == ""
        assert any(
            ENV_SECRET in rec.message for rec in caplog.records
        )


class TestMarkerExists:

    def test_true_when_present(self, tmp_path):
        request_quit(tmp_path, target="all")
        assert marker_exists(tmp_path, MARKER_QUIT) is True

    def test_false_when_absent(self, tmp_path):
        assert marker_exists(tmp_path, MARKER_QUIT) is False
