# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the manager-spec Phase 3 additions to
:mod:`launcher.setup_helpers`.

Covers:

- ``atomic_write_text`` writes content atomically, creates parent
  dirs, rejects empty content, and leaves no temp-file crumbs on
  write failure.
- ``find_available_loopback_port`` returns the requested port when
  free (we trial-bind it to prove the 127.0.0.1 binding).
- Setup-restart-request IPC round-trip: write → read → clear →
  read-returns-None.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from launcher.setup_helpers import (
    atomic_write_text,
    clear_setup_restart_request,
    find_available_loopback_port,
    read_setup_restart_request,
    setup_restart_request_path,
    write_setup_restart_request,
)


# -------------------------------------------------------------------
# atomic_write_text
# -------------------------------------------------------------------

class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / 'out.txt'
        atomic_write_text(target, 'hello')
        assert target.read_text() == 'hello'

    def test_creates_parent_dir(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'out.txt'
        atomic_write_text(target, 'x')
        assert target.read_text() == 'x'

    def test_rejects_empty(self, tmp_path):
        with pytest.raises(ValueError):
            atomic_write_text(tmp_path / 'x', '')

    def test_no_crumbs_on_write_success(self, tmp_path):
        target = tmp_path / 'x.txt'
        atomic_write_text(target, 'abc')
        leftover = [p for p in tmp_path.iterdir() if p != target]
        assert leftover == []

    def test_no_crumbs_on_replace_failure(self, tmp_path):
        target = tmp_path / 'x.txt'
        target.write_text("prior")
        # Force os.replace to fail after the tempfile is written.
        with patch('launcher.setup_helpers.os.replace') as m:
            m.side_effect = OSError("simulated fail")
            with pytest.raises(OSError):
                atomic_write_text(target, 'abc')
        # Prior content preserved (no partial overwrite).
        assert target.read_text() == "prior"
        # No tempfile leaks in the directory.
        leftover = [
            p for p in tmp_path.iterdir()
            if p != target and p.suffix == '.tmp'
        ]
        assert leftover == []


# -------------------------------------------------------------------
# find_available_loopback_port
# -------------------------------------------------------------------

def test_find_available_loopback_port_binds_to_127_0_0_1():
    # Default range (8089..8099) — at least one port in the dev
    # tooling range should be free on any sane CI agent.
    port = find_available_loopback_port(start=50000, end=50010)
    assert 50000 <= port <= 50010


# -------------------------------------------------------------------
# Setup-restart request IPC round-trip
# -------------------------------------------------------------------

class TestSetupRestartRequestIpc:
    def test_roundtrip(self, tmp_path):
        manager_data = tmp_path / 'manager'
        manager_data.mkdir()
        payload = {
            "public_tls_port": 9000,
            "waitress_public_port": 19000,
        }
        write_setup_restart_request(manager_data, payload)

        path = setup_restart_request_path(manager_data)
        assert path.exists()
        assert json.loads(path.read_text()) == payload

        got = read_setup_restart_request(manager_data)
        assert got == payload

    def test_read_missing_returns_none(self, tmp_path):
        assert read_setup_restart_request(tmp_path) is None

    def test_read_invalid_json_returns_none(self, tmp_path):
        manager_data = tmp_path / 'manager'
        manager_data.mkdir()
        setup_restart_request_path(manager_data).write_text(
            "not json {", encoding='utf-8',
        )
        # Parse failures are logged but not raised.
        assert read_setup_restart_request(manager_data) is None

    def test_clear_deletes(self, tmp_path):
        manager_data = tmp_path / 'manager'
        manager_data.mkdir()
        write_setup_restart_request(manager_data, {"x": 1})
        assert setup_restart_request_path(manager_data).exists()
        clear_setup_restart_request(manager_data)
        assert not setup_restart_request_path(manager_data).exists()
        # Second clear is a no-op, not an error.
        clear_setup_restart_request(manager_data)
