# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``launcher/setup_helpers.py``.

Focus: ``generate_setup_token`` must reuse an existing non-empty
``[setup] token`` in the settings file across calls so in-progress
wizard sessions survive launcher restarts (GitHub #73).
"""

import configparser

from launcher.setup_helpers import (
    generate_setup_token,
    remove_setup_section,
)


def _read_token(ini_path):
    config = configparser.ConfigParser()
    config.read(ini_path)
    if not config.has_section("setup"):
        return None
    return config.get("setup", "token", fallback=None)


def test_first_call_generates_and_writes_token(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    assert not ini_path.exists()

    token = generate_setup_token(tmp_path / "sethlans.ini")

    assert token
    assert ini_path.exists()
    assert _read_token(ini_path) == token


def test_second_call_reuses_existing_token(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    first = generate_setup_token(tmp_path / "sethlans.ini")
    mtime_before = ini_path.stat().st_mtime_ns
    contents_before = ini_path.read_bytes()

    second = generate_setup_token(tmp_path / "sethlans.ini")

    assert second == first
    assert _read_token(ini_path) == first
    # File must not be rewritten with a different value.
    assert ini_path.read_bytes() == contents_before
    # Best-effort: mtime should not have advanced either.
    assert ini_path.stat().st_mtime_ns == mtime_before


def test_empty_token_treated_as_missing(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    config = configparser.ConfigParser()
    config.add_section("setup")
    config.set("setup", "token", "")
    with open(ini_path, "w") as f:
        config.write(f)

    token = generate_setup_token(tmp_path / "sethlans.ini")

    assert token
    assert token != ""
    assert _read_token(ini_path) == token


def test_remove_then_regenerate_creates_fresh_token(tmp_path):
    first = generate_setup_token(tmp_path / "sethlans.ini")
    remove_setup_section(tmp_path / "sethlans.ini")

    ini_path = tmp_path / "sethlans.ini"
    assert _read_token(ini_path) is None

    second = generate_setup_token(tmp_path / "sethlans.ini")

    assert second
    assert second != first
    assert _read_token(ini_path) == second


def test_many_sequential_calls_return_same_token(tmp_path):
    first = generate_setup_token(tmp_path / "sethlans.ini")
    tokens = [generate_setup_token(tmp_path / "sethlans.ini") for _ in range(5)]

    assert all(t == first for t in tokens)


def test_default_path_is_the_settings_file(tmp_path, monkeypatch):
    ini_path = tmp_path / "settings" / "sethlans.ini"
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(ini_path))
    token = generate_setup_token()
    assert _read_token(ini_path) == token
    remove_setup_section()
    assert _read_token(ini_path) is None


def test_remove_on_missing_file_creates_nothing(tmp_path):
    remove_setup_section(tmp_path / "sethlans.ini")
    assert not (tmp_path / "sethlans.ini").exists()


def test_remove_keeps_setup_finished_keys(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    ini_path.write_text(
        "[setup]\ntoken = t\nsession_id = s\nversion = 1\nmode = manager\n"
        "completed_at = 2026-11-24T10:00:00+00:00\n",
        encoding="utf-8",
    )
    remove_setup_section(ini_path)
    config = configparser.RawConfigParser(interpolation=None)
    config.read(ini_path)
    assert dict(config["setup"]) == {
        "version": "1", "mode": "manager", "completed_at": "2026-11-24T10:00:00+00:00",
    }


def test_remove_drops_section_left_empty(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    ini_path.write_text("[setup]\ntoken = t\nsession_id = s\n[server]\nport = 1\n", encoding="utf-8")
    remove_setup_section(ini_path)
    config = configparser.RawConfigParser(interpolation=None)
    config.read(ini_path)
    assert config.sections() == ["server"]
