# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.setup_state` (FR-SEC-1, FR-LN-6, FR-RS-1, FR-SF-3)."""

from __future__ import annotations

import json

import pytest

from launcher import setup_state
from shared import owner_only_acl, settings_file


def test_fresh_token_is_written_owner_only(settings_dir):
    """FR-SEC-1: a new ``token_urlsafe(32)`` per SETUP entry, in an owner-only
    file inside an owner-only ``setup/`` folder."""
    first = setup_state.write_fresh_token()
    second = setup_state.write_fresh_token()

    path = settings_dir / "setup" / "setup_token"
    assert path.read_bytes() == second.encode("ascii")
    assert first != second and len(second) >= 43
    assert owner_only_acl.verify_owner_only(path)
    assert owner_only_acl.verify_owner_only(settings_dir / "setup")


def test_rewrite_keeps_the_same_token_byte_equal(settings_dir):
    """FR-LN-6: after a failed first start the same token is written again,
    byte for byte, so the open setup page keeps working."""
    token = setup_state.write_fresh_token()
    path = settings_dir / "setup" / "setup_token"
    before = path.read_bytes()
    assert setup_state.delete_token() is True
    assert setup_state.delete_token() is False

    setup_state.rewrite_token(token)

    assert path.read_bytes() == before
    assert owner_only_acl.verify_owner_only(path)


@pytest.mark.parametrize("bad", ["", " padded", "töken"])
def test_rewrite_refuses_a_token_setup_mode_could_not_read(settings_dir, bad):
    """FR-SEC-1: setup mode reads an ASCII token with no outer spaces; anything
    else is refused before it is written."""
    with pytest.raises(ValueError):
        setup_state.rewrite_token(bad)
    assert not (settings_dir / "setup" / "setup_token").exists()


def test_last_failure_is_one_capped_line_and_clears(settings_dir):
    """FR-LN-6: the reason setup mode shows is one line, capped."""
    setup_state.write_last_failure("database error:\n  could not connect\t" + "x" * 2000)

    text = (settings_dir / "setup" / "last_failure").read_text(encoding="utf-8")
    assert text.startswith("database error: could not connect x")
    assert len(text) == setup_state.MAX_REASON_CHARS
    assert setup_state.clear_last_failure() is True
    assert setup_state.clear_last_failure() is False


def test_launcher_state_gives_the_tray_state_port_and_reason(settings_dir):
    """FR-LN-4 (chunk 6 plan): ``launcher_state.json`` carries state, port and reason."""
    setup_state.write_launcher_state("FAILED", port=8081, reason="no\nadmin")

    data = json.loads((settings_dir / "setup" / "launcher_state.json").read_text(encoding="utf-8"))
    assert data == {"state": "FAILED", "port": 8081, "reason": "no admin"}


def test_remove_completed_at_leaves_the_rest_byte_equal(settings_dir):
    """FR-RS-1, FR-SF-3 (SR nit 7): Reset removes ``[setup] completed_at`` only."""
    ini = settings_dir / "sethlans.ini"
    settings_file.set_values("setup", {"version": "1", "mode": "manager_worker",
                                       "completed_at": "2026-11-28T10:00:00+00:00"}, ini)
    settings_file.set_values("server", {"host": "0.0.0.0", "port": "8080"}, ini)
    settings_file.set_values("database", {"password": "p%w"}, ini)
    before = ini.read_bytes()

    assert setup_state.remove_completed_at() is True

    expected = before.replace(b"completed_at = 2026-11-28T10:00:00+00:00\n", b"")
    assert expected != before
    assert ini.read_bytes() == expected
    assert settings_file.setup_finished(ini) is None
    assert setup_state.remove_completed_at() is False
    assert ini.read_bytes() == expected


def test_remove_completed_at_leaves_a_missing_file_missing(settings_dir):
    """FR-RS-2: Reset creates nothing."""
    assert setup_state.remove_completed_at() is False
    assert not (settings_dir / "sethlans.ini").exists()
