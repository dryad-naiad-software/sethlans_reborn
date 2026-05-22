# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``cleanup_stale_setup_section`` (GitHub #76, #195)."""

from __future__ import annotations

import configparser
import json
from pathlib import Path

from sethlans_manager.runtime_init import cleanup_stale_setup_section


def _write_ini(path: Path, sections: dict) -> None:
    parser = configparser.ConfigParser()
    for section, options in sections.items():
        parser.add_section(section)
        for k, v in options.items():
            parser.set(section, k, v)
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)


def _write_sentinel(shared_dir: Path, completed: bool = True) -> None:
    """Write ``.setup_complete`` to the shared data root (not manager subdir)."""
    data = {
        "version": 1,
        "checkpoints": [],
    }
    if completed:
        data["completed_at"] = "2025-01-15T12:00:00Z"
    (shared_dir / ".setup_complete").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Existing test coverage — restructured to production-realistic layout
#
# Production layout: <shared>/.setup_complete, <shared>/manager/manager.ini
# Tests must pass shared_data_dir=tmp_path so the function does not call
# get_shared_data_dir() and read from the real filesystem.
# ---------------------------------------------------------------------------

def test_removes_setup_section_when_sentinel_present(tmp_path: Path):
    """Sentinel in shared root + manager.ini in manager subdir — section removed."""
    manager_dir = tmp_path / "manager"
    manager_dir.mkdir()
    _write_sentinel(tmp_path, completed=True)
    ini = manager_dir / "manager.ini"
    _write_ini(ini, {
        "security": {"secret_key": "x", "debug": "False"},
        "setup": {"token": "abc123", "session_id": "sess1"},
    })

    cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert not parser.has_section("setup")
    assert parser.has_section("security")
    assert parser.get("security", "secret_key") == "x"


def test_no_op_when_sentinel_missing(tmp_path: Path):
    """No sentinel in shared root — ini left untouched."""
    manager_dir = tmp_path / "manager"
    manager_dir.mkdir()
    ini = manager_dir / "manager.ini"
    _write_ini(ini, {"setup": {"token": "abc"}})

    cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert parser.has_section("setup")
    assert parser.get("setup", "token") == "abc"


def test_no_op_when_sentinel_incomplete(tmp_path: Path):
    """Sentinel present but missing ``completed_at`` — ini left untouched."""
    manager_dir = tmp_path / "manager"
    manager_dir.mkdir()
    _write_sentinel(tmp_path, completed=False)
    ini = manager_dir / "manager.ini"
    _write_ini(ini, {"setup": {"token": "abc"}})

    cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert parser.has_section("setup")


def test_no_op_when_setup_section_absent(tmp_path: Path):
    """Sentinel present but ini has no [setup] — no error, other sections kept."""
    manager_dir = tmp_path / "manager"
    manager_dir.mkdir()
    _write_sentinel(tmp_path, completed=True)
    ini = manager_dir / "manager.ini"
    _write_ini(ini, {"security": {"secret_key": "x"}})

    cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)

    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    assert not parser.has_section("setup")
    assert parser.has_section("security")


def test_no_op_when_ini_missing(tmp_path: Path):
    """Sentinel present but manager.ini absent — silent no-op, no exception."""
    manager_dir = tmp_path / "manager"
    manager_dir.mkdir()
    _write_sentinel(tmp_path, completed=True)
    cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)


# ---------------------------------------------------------------------------
# AC-6: separate shared_dir / manager_dir layout
# ---------------------------------------------------------------------------

class TestCleanupStaleSentinelFromSharedDir:
    """AC-6: sentinel at shared_root/.setup_complete, manager.ini at
    manager_dir/manager.ini (separate subdirectory) — the function removes
    [setup] when sentinel present and no-ops when absent.
    """

    def test_removes_setup_section_reads_sentinel_from_shared_dir(self, tmp_path):
        """Sentinel at shared root, manager.ini in subdir — [setup] removed."""
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        manager_dir = shared_dir / "manager"
        manager_dir.mkdir()

        _write_sentinel(shared_dir, completed=True)
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {
            "security": {"secret_key": "y"},
            "setup": {"token": "tok1", "session_id": "s1"},
        })

        cleanup_stale_setup_section(manager_dir, shared_data_dir=shared_dir)

        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert not parser.has_section("setup")
        assert parser.has_section("security")

    def test_no_op_when_sentinel_absent_from_shared_dir(self, tmp_path):
        """No sentinel at shared root — [setup] preserved even if manager subdir exists."""
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        manager_dir = shared_dir / "manager"
        manager_dir.mkdir()

        # No sentinel written to shared_dir
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {"setup": {"token": "tok1"}})

        cleanup_stale_setup_section(manager_dir, shared_data_dir=shared_dir)

        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert parser.has_section("setup")
        assert parser.get("setup", "token") == "tok1"

    def test_sentinel_only_in_shared_dir_not_manager_subdir(self, tmp_path):
        """Sentinel at shared root is found; sentinel at manager subdir is NOT read."""
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        manager_dir = shared_dir / "manager"
        manager_dir.mkdir()

        # Write sentinel ONLY in manager subdir — function must NOT find it
        _write_sentinel(manager_dir, completed=True)
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {"setup": {"token": "tok2"}})

        cleanup_stale_setup_section(manager_dir, shared_data_dir=shared_dir)

        # [setup] must remain — shared_dir has no sentinel
        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert parser.has_section("setup")


# ---------------------------------------------------------------------------
# AC-7: explicit shared_data_dir overrides get_shared_data_dir()
# ---------------------------------------------------------------------------

class TestCleanupStaleDefaultSharedDataDir:
    """AC-7: when shared_data_dir is None, the function falls through to
    get_shared_data_dir(). Passing an explicit value must override it.
    """

    def test_explicit_shared_data_dir_overrides_default(self, tmp_path):
        """Explicit shared_data_dir is used; get_shared_data_dir() is not called."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        _write_sentinel(tmp_path, completed=True)
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {"setup": {"token": "over"}})

        # If get_shared_data_dir() were called it would return a real dir
        # and not find the test sentinel — the explicit arg must win.
        cleanup_stale_setup_section(manager_dir, shared_data_dir=tmp_path)

        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert not parser.has_section("setup"), (
            "explicit shared_data_dir must be used, not get_shared_data_dir()"
        )

    def test_default_none_calls_get_shared_data_dir(self, mocker, tmp_path):
        """When shared_data_dir=None, get_shared_data_dir() is consulted."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {"setup": {"token": "default_path"}})

        # Point get_shared_data_dir() at tmp_path which has no sentinel
        import sethlans_manager.runtime_init as ri
        mocker.patch.object(ri, "get_shared_data_dir", return_value=tmp_path)

        # shared_data_dir omitted — falls through to get_shared_data_dir()
        cleanup_stale_setup_section(manager_dir)

        # No sentinel at tmp_path → no-op → [setup] preserved
        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert parser.has_section("setup"), (
            "without sentinel at shared_data_dir, function must no-op"
        )

    def test_default_none_calls_get_shared_data_dir_sentinel_present(self, mocker, tmp_path):
        """When shared_data_dir=None and sentinel present at get_shared_data_dir(),
        the [setup] section is removed."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        ini = manager_dir / "manager.ini"
        _write_ini(ini, {"setup": {"token": "stub_sentinel"}})
        _write_sentinel(tmp_path, completed=True)

        import sethlans_manager.runtime_init as ri
        mocker.patch.object(ri, "get_shared_data_dir", return_value=tmp_path)

        cleanup_stale_setup_section(manager_dir)

        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        assert not parser.has_section("setup"), (
            "sentinel found via get_shared_data_dir() must trigger cleanup"
        )
