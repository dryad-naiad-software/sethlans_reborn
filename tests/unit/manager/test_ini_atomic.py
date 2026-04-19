# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/services/ini_atomic.py``.
"""

from __future__ import annotations

import configparser

import pytest

from workers.services.ini_atomic import (
    bind_setup_session_id,
    read_setup_session_id,
    remove_ini_section,
    write_ini_atomic,
)


class TestWriteIniAtomic:

    def test_creates_new_ini(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(ini, {"setup.token": "abc"})
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("setup", "token") == "abc"

    def test_merges_into_existing_sections(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(ini, {"server.port": "8080"})
        write_ini_atomic(ini, {"setup.token": "xyz"})
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("server", "port") == "8080"
        assert cfg.get("setup", "token") == "xyz"

    def test_overwrites_existing_option(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(ini, {"setup.token": "v1"})
        write_ini_atomic(ini, {"setup.token": "v2"})
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("setup", "token") == "v2"

    def test_invalid_key_raises(self, tmp_path):
        with pytest.raises(ValueError):
            write_ini_atomic(tmp_path / "m.ini", {"no_dot_here": "x"})

    def test_atomic_leaves_no_tempfiles(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(ini, {"setup.token": "abc"})
        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 1
        assert remaining[0].name == "manager.ini"


class TestRemoveIniSection:

    def test_removes_existing_section(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(
            ini,
            {"setup.token": "abc", "server.port": "8080"},
        )
        remove_ini_section(ini, "setup")
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert not cfg.has_section("setup")
        assert cfg.get("server", "port") == "8080"

    def test_noop_when_section_missing(self, tmp_path):
        ini = tmp_path / "manager.ini"
        write_ini_atomic(ini, {"server.port": "8080"})
        remove_ini_section(ini, "setup")  # no crash
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("server", "port") == "8080"

    def test_noop_when_file_missing(self, tmp_path):
        remove_ini_section(tmp_path / "nonexistent.ini", "setup")  # no crash


class TestSetupSessionIdRoundtrip:

    def test_bind_then_read(self, tmp_path):
        assert bind_setup_session_id(tmp_path, "sid-1") is True
        assert read_setup_session_id(tmp_path) == "sid-1"

    def test_read_returns_none_when_file_missing(self, tmp_path):
        assert read_setup_session_id(tmp_path) is None

    def test_bind_returns_true_when_same_session(self, tmp_path):
        bind_setup_session_id(tmp_path, "sid-1")
        assert bind_setup_session_id(tmp_path, "sid-1") is True

    def test_bind_returns_false_on_conflict(self, tmp_path):
        bind_setup_session_id(tmp_path, "sid-1")
        assert bind_setup_session_id(tmp_path, "sid-other") is False
        # Original binding preserved.
        assert read_setup_session_id(tmp_path) == "sid-1"


class TestBindSetupSessionIdRace:
    """Verify-after-write guards against two callers both seeing an
    empty ``[setup] session_id`` and racing to write — last-writer-wins
    on ``os.replace`` means only one actually owns the binding."""

    def test_returns_true_when_no_concurrent_writer(self, tmp_path):
        # Baseline: fresh data dir, nobody else wrote; we should win.
        assert bind_setup_session_id(tmp_path, "sid-winner") is True
        assert read_setup_session_id(tmp_path) == "sid-winner"

    def test_returns_false_when_concurrent_caller_overwrites(
        self, tmp_path, mocker,
    ):
        # Simulate a concurrent binder that writes a *different*
        # session_id between our ``write_ini_atomic`` call and the
        # final re-read.  After the race the file holds the other
        # session's value, so our return must be False.
        from workers.services import ini_atomic as mod

        real_write = mod.write_ini_atomic

        def racy_write(ini_path, updates):
            real_write(ini_path, updates)
            # Concurrent caller's write lands AFTER ours.
            real_write(ini_path, {"setup.session_id": "sid-other"})

        mocker.patch.object(mod, "write_ini_atomic", side_effect=racy_write)

        assert bind_setup_session_id(tmp_path, "sid-ours") is False
        # File reflects the concurrent (winning) writer.
        assert read_setup_session_id(tmp_path) == "sid-other"

    def test_idempotent_when_already_bound_to_same_id(
        self, tmp_path, mocker,
    ):
        # First call establishes the binding.
        assert bind_setup_session_id(tmp_path, "sid-same") is True

        # Second call with the same id must short-circuit — no new
        # write should occur.  Spy on ``write_ini_atomic`` to confirm.
        from workers.services import ini_atomic as mod

        spy = mocker.spy(mod, "write_ini_atomic")
        assert bind_setup_session_id(tmp_path, "sid-same") is True
        assert spy.call_count == 0
