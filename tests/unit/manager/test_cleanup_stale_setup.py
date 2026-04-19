# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``cleanup_stale_setup_section`` (GitHub #76)."""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

from sethlans_manager.runtime_init import cleanup_stale_setup_section


def _write_ini(path: Path, sections: dict) -> None:
    parser = configparser.ConfigParser()
    for section, options in sections.items():
        parser.add_section(section)
        for k, v in options.items():
            parser.set(section, k, v)
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)


def _write_sentinel(manager_dir: Path, completed: bool = True) -> None:
    data = {
        "version": 1,
        "checkpoints": [],
    }
    if completed:
        data["completed_at"] = "2025-01-15T12:00:00Z"
    (manager_dir / ".setup_complete").write_text(json.dumps(data))


def test_removes_setup_section_when_sentinel_present(tmp_path: Path):
    _write_sentinel(tmp_path, completed=True)
    ini = tmp_path / "manager.ini"
    _write_ini(ini, {
        "security": {"secret_key": "x", "debug": "False"},
        "setup": {"token": "abc123", "session_id": "sess1"},
    })

    cleanup_stale_setup_section(tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert not parser.has_section("setup")
    assert parser.has_section("security")
    assert parser.get("security", "secret_key") == "x"


def test_no_op_when_sentinel_missing(tmp_path: Path):
    ini = tmp_path / "manager.ini"
    _write_ini(ini, {"setup": {"token": "abc"}})

    cleanup_stale_setup_section(tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert parser.has_section("setup")
    assert parser.get("setup", "token") == "abc"


def test_no_op_when_sentinel_incomplete(tmp_path: Path):
    _write_sentinel(tmp_path, completed=False)
    ini = tmp_path / "manager.ini"
    _write_ini(ini, {"setup": {"token": "abc"}})

    cleanup_stale_setup_section(tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert parser.has_section("setup")


def test_no_op_when_setup_section_absent(tmp_path: Path):
    _write_sentinel(tmp_path, completed=True)
    ini = tmp_path / "manager.ini"
    _write_ini(ini, {"security": {"secret_key": "x"}})

    cleanup_stale_setup_section(tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert not parser.has_section("setup")
    assert parser.has_section("security")


def test_no_op_when_ini_missing(tmp_path: Path):
    _write_sentinel(tmp_path, completed=True)
    cleanup_stale_setup_section(tmp_path)
