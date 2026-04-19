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
