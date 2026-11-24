# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``cleanup_stale_setup_section`` (GitHub #76, #195).

Layout: ``<settings folder>/.setup_complete`` and the settings file
``sethlans.ini``. Only the wizard's ``[setup] token`` / ``session_id``
go; the setup-finished keys of the same section stay.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path

from sethlans_manager.runtime_init import cleanup_stale_setup_section
from shared.settings_file import settings_path


def _write_ini(path: Path, sections: dict) -> Path:
    parser = configparser.RawConfigParser(interpolation=None)
    for section, options in sections.items():
        parser.add_section(section)
        for k, v in options.items():
            parser.set(section, k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)
    return path


def _read_ini(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def _write_sentinel(settings_dir: Path, completed: bool = True) -> None:
    """Write ``.setup_complete`` to the settings folder (not ``manager/``)."""
    data = {
        "version": 1,
        "checkpoints": [],
    }
    if completed:
        data["completed_at"] = "2025-01-15T12:00:00Z"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / ".setup_complete").write_text(json.dumps(data))


FINISHED = {"version": "1", "mode": "manager", "completed_at": "2025-01-15T12:00:00+00:00"}


def test_removes_wizard_keys_when_sentinel_present(tmp_path: Path):
    """Sentinel present: token/session_id go, other sections stay."""
    _write_sentinel(tmp_path, completed=True)
    ini = _write_ini(tmp_path / "sethlans.ini", {
        "security": {"secret_key": "x%y", "debug": "False"},
        "setup": {"token": "abc123", "session_id": "sess1"},
    })

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    parser = _read_ini(ini)
    assert not parser.has_section("setup")
    assert parser.get("security", "secret_key") == "x%y"


def test_keeps_setup_finished_keys(tmp_path: Path):
    """The setup-finished keys share ``[setup]``; a normal start keeps them."""
    _write_sentinel(tmp_path, completed=True)
    ini = _write_ini(tmp_path / "sethlans.ini", {
        "setup": {**FINISHED, "token": "abc123", "session_id": "sess1"},
    })

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    parser = _read_ini(ini)
    assert dict(parser["setup"]) == FINISHED


def test_no_op_when_sentinel_missing(tmp_path: Path):
    """No sentinel in the settings folder — file left untouched."""
    ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "abc"}})
    before = ini.read_bytes()

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    assert ini.read_bytes() == before


def test_no_op_when_sentinel_incomplete(tmp_path: Path):
    """Sentinel present but missing ``completed_at`` — file left untouched."""
    _write_sentinel(tmp_path, completed=False)
    ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "abc"}})

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    assert _read_ini(ini).get("setup", "token") == "abc"


def test_no_op_when_setup_section_absent(tmp_path: Path):
    """Sentinel present but no [setup] — no error, nothing rewritten."""
    _write_sentinel(tmp_path, completed=True)
    ini = _write_ini(tmp_path / "sethlans.ini", {"security": {"secret_key": "x"}})
    before = ini.read_bytes()

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    assert ini.read_bytes() == before


def test_no_op_when_ini_missing(tmp_path: Path):
    """Sentinel present but no settings file — silent no-op, nothing created."""
    _write_sentinel(tmp_path, completed=True)
    ini = tmp_path / "sethlans.ini"

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    assert not ini.exists()


def test_sentinel_in_manager_subdir_is_not_read(tmp_path: Path):
    """Only ``<settings folder>/.setup_complete`` counts (#195)."""
    _write_sentinel(tmp_path / "manager", completed=True)
    ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "tok2"}})

    cleanup_stale_setup_section(ini, settings_dir=tmp_path)

    assert _read_ini(ini).has_section("setup")


class TestDefaults:
    """With no arguments: ``settings_path()`` and ``get_settings_dir()``."""

    def test_default_settings_dir_is_get_settings_dir(self, mocker, tmp_path):
        ini = _write_ini(tmp_path / "ini" / "sethlans.ini", {"setup": {"token": "t"}})
        _write_sentinel(tmp_path / "settings", completed=True)
        import sethlans_manager.runtime_init as ri
        mocker.patch.object(ri, "get_settings_dir", return_value=tmp_path / "settings")

        cleanup_stale_setup_section(ini)

        assert not _read_ini(ini).has_section("setup")

    def test_default_settings_dir_without_sentinel_is_no_op(self, mocker, tmp_path):
        ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "t"}})
        import sethlans_manager.runtime_init as ri
        mocker.patch.object(ri, "get_settings_dir", return_value=tmp_path / "empty")

        cleanup_stale_setup_section(ini)

        assert _read_ini(ini).get("setup", "token") == "t"

    def test_default_ini_is_settings_path(self, tmp_path):
        """No *ini_path*: the file at ``settings_path()`` (the conftest's per-test file)."""
        ini = _write_ini(settings_path(), {"setup": {**FINISHED, "token": "t"}})
        _write_sentinel(tmp_path / "settings", completed=True)

        cleanup_stale_setup_section(settings_dir=tmp_path / "settings")

        assert dict(_read_ini(ini)["setup"]) == FINISHED


class TestWriteFailures:
    """A failed write never stops the start; it is logged and retried next start."""

    def test_lock_timeout_logs_a_warning_and_continues(self, mocker, tmp_path):
        from shared.file_lock import FileLockTimeoutError
        import sethlans_manager.runtime_init as ri
        _write_sentinel(tmp_path, completed=True)
        ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "t"}})
        mocker.patch.object(
            ri, "remove_setup_wizard_keys",
            side_effect=FileLockTimeoutError("lock held"),
        )
        log = mocker.patch.object(ri, "logger")

        cleanup_stale_setup_section(ini, settings_dir=tmp_path)  # no raise

        log.warning.assert_called_once()
        assert "lock held" in str(log.warning.call_args)
        assert _read_ini(ini).get("setup", "token") == "t"

    def test_other_os_errors_also_continue(self, mocker, tmp_path):
        import sethlans_manager.runtime_init as ri
        _write_sentinel(tmp_path, completed=True)
        ini = _write_ini(tmp_path / "sethlans.ini", {"setup": {"token": "t"}})
        mocker.patch.object(ri, "remove_setup_wizard_keys", side_effect=PermissionError("denied"))
        log = mocker.patch.object(ri, "logger")

        cleanup_stale_setup_section(ini, settings_dir=tmp_path)

        log.warning.assert_called_once()
