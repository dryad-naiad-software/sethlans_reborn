# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/launcher_state.py``: the tray's reader of the launcher state (FR-LN-4, chunk 6c)."""

from __future__ import annotations

import json

import pytest

from shared.tray import launcher_state as ls
from tests.unit.shared.tray.conftest import write_launcher_state


@pytest.mark.parametrize("state", sorted(ls.STATES))
def test_each_launcher_state_is_read(settings_dir, state):
    """FR-LN-4: the tray shows the state the launcher wrote (SETUP, STARTING, RUNNING, FAILED, STOPPING)."""
    write_launcher_state(settings_dir, state, port=8081, reason="The worker ended")
    assert ls.read_launcher_state() == ls.LauncherState(state, 8081, "The worker ended")


def test_neutral_starting_has_no_port(settings_dir):
    """6b writes STARTING with no port before setup answers; the tray reads it as such."""
    write_launcher_state(settings_dir, "STARTING")
    assert ls.read_launcher_state(settings_dir) == ls.LauncherState("STARTING", None, None)


@pytest.mark.parametrize("raw", [
    pytest.param(None, id="missing"),
    pytest.param(b"{not json", id="garbled"),
    pytest.param(b"\xff\xfe", id="not-utf8"),
    pytest.param(b"[" * 2000 + b"]" * 2000, id="too-deep"),
    pytest.param(json.dumps({"state": "WIZARD"}).encode(), id="unknown-state"),
    pytest.param(json.dumps(["RUNNING"]).encode(), id="not-an-object"),
    pytest.param(b" " * (ls.MAX_STATE_BYTES + 1), id="oversized"),
])
def test_bad_file_gives_the_safe_default(settings_dir, raw):
    """A missing or garbled file never raises and gives STARTING: no Copy Token, no Reset."""
    if raw is not None:
        (settings_dir / "setup").mkdir()
        (settings_dir / "setup" / "launcher_state.json").write_bytes(raw)
    assert ls.read_launcher_state() == ls.DEFAULT_STATE == ls.LauncherState("STARTING")


def test_unreadable_file_gives_the_safe_default(settings_dir, mocker):
    """An OS error while reading (e.g. a folder in its place) never escapes."""
    mocker.patch.object(ls, "read_owned_file", side_effect=PermissionError("denied"))
    assert ls.read_launcher_state() == ls.DEFAULT_STATE


@pytest.mark.parametrize(("port", "expected"), [
    (1, 1), (65535, 65535), (0, None), (65536, None), ("8080", None), (True, None),
])
def test_port_must_be_a_port_number(settings_dir, port, expected):
    """Only a real port number is used for Open Setup / Open Dashboard."""
    write_launcher_state(settings_dir, "SETUP", port=port)
    assert ls.read_launcher_state().port == expected


@pytest.mark.parametrize(("reason", "expected"), [
    ("line one\nline two\x1b[31m", "line one line two [31m"),
    ("x" * 600, "x" * ls.MAX_REASON_CHARS),
    ("   ", None),
    (42, None),
])
def test_reason_is_one_capped_line(settings_dir, reason, expected):
    """The free-text reason is shown in the menu: one line, no control characters, capped."""
    write_launcher_state(settings_dir, "FAILED", reason=reason)
    assert ls.read_launcher_state().reason == expected
