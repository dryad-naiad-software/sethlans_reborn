# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``launcher/setup_helpers.py``.

Focus: ``generate_setup_token`` must reuse an existing non-empty
``[setup] token`` across calls so in-progress wizard sessions
survive launcher restarts (GitHub #73).
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
    ini_path = tmp_path / "manager.ini"
    assert not ini_path.exists()

    token = generate_setup_token(tmp_path)

    assert token
    assert ini_path.exists()
    assert _read_token(ini_path) == token


def test_second_call_reuses_existing_token(tmp_path):
    ini_path = tmp_path / "manager.ini"
    first = generate_setup_token(tmp_path)
    mtime_before = ini_path.stat().st_mtime_ns
    contents_before = ini_path.read_bytes()

    second = generate_setup_token(tmp_path)

    assert second == first
    assert _read_token(ini_path) == first
    # File must not be rewritten with a different value.
    assert ini_path.read_bytes() == contents_before
    # Best-effort: mtime should not have advanced either.
    assert ini_path.stat().st_mtime_ns == mtime_before


def test_empty_token_treated_as_missing(tmp_path):
    ini_path = tmp_path / "manager.ini"
    config = configparser.ConfigParser()
    config.add_section("setup")
    config.set("setup", "token", "")
    with open(ini_path, "w") as f:
        config.write(f)

    token = generate_setup_token(tmp_path)

    assert token
    assert token != ""
    assert _read_token(ini_path) == token


def test_remove_then_regenerate_creates_fresh_token(tmp_path):
    first = generate_setup_token(tmp_path)
    remove_setup_section(tmp_path)

    ini_path = tmp_path / "manager.ini"
    assert _read_token(ini_path) is None

    second = generate_setup_token(tmp_path)

    assert second
    assert second != first
    assert _read_token(ini_path) == second


def test_many_sequential_calls_return_same_token(tmp_path):
    first = generate_setup_token(tmp_path)
    tokens = [generate_setup_token(tmp_path) for _ in range(5)]

    assert all(t == first for t in tokens)
