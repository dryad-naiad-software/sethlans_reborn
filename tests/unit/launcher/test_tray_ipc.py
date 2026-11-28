# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/tray_ipc.py`` (tray spec FR-20b..FR-20g)."""

from __future__ import annotations

import datetime as _dt
import json
import os

import pytest

from launcher import tray_ipc
from launcher.tray_ipc import (
    MARKER_QUIT,
    MARKER_RESET,
    MARKER_RESTART,
    consume_all_markers,
    consume_pending_ipc,
    generate_secret,
    read_and_validate_marker,
    sweep_stale_markers,
)


from shared.tray import ipc as tray_writer
from tests.unit.launcher._marker_helpers import SECRET as _SECRET
from tests.unit.launcher._marker_helpers import TRAY_PID as _TRAY_PID
from tests.unit.launcher._marker_helpers import write_marker as _write_marker_raw


# ------------------------------------------------------------------
# generate_secret
# ------------------------------------------------------------------

class TestGenerateSecret:

    def test_returns_urlsafe_string(self):
        s = generate_secret()
        assert isinstance(s, str)
        assert len(s) >= 30

    def test_unique_per_call(self):
        assert generate_secret() != generate_secret()


# ------------------------------------------------------------------
# sweep_stale_markers
# ------------------------------------------------------------------

class TestSweepStaleMarkers:

    def test_deletes_existing_markers(self, tmp_path):
        """FR-20e; FR-RS-1: the reset marker is swept too."""
        for name in (MARKER_QUIT, MARKER_RESTART, MARKER_RESET):
            (tmp_path / name).write_text("x", encoding="utf-8")
        sweep_stale_markers(tmp_path)
        for name in (MARKER_QUIT, MARKER_RESTART, MARKER_RESET):
            assert not (tmp_path / name).exists()

    def test_no_files_is_noop(self, tmp_path):
        # Must not raise.
        sweep_stale_markers(tmp_path)


# ------------------------------------------------------------------
# read_and_validate_marker
# ------------------------------------------------------------------

class TestReadAndValidateMarker:

    def test_accepts_well_formed_marker_returns_target(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, target="all")
        result = read_and_validate_marker(path, _SECRET, _TRAY_PID)
        assert result == "all"
        # File should have been deleted.
        assert not path.exists()

    def test_rejects_bad_secret(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, secret="wrong")
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None
        assert not path.exists()

    def test_rejects_bad_pid(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, pid=99999)
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None

    def test_rejects_unknown_target(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, target="destroy")
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None

    def test_rejects_stale_marker(self, tmp_path):
        old_ts = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
        ).isoformat()
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, requested_at=old_ts)
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None

    def test_rejects_malformed_json(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        path.write_text("{not-json", encoding="utf-8")
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None

    def test_rejects_oversized(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        path.write_text("x" * 5000, encoding="utf-8")
        assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None

    def test_missing_file_returns_none(self, tmp_path):
        result = read_and_validate_marker(
            tmp_path / "nope", _SECRET, _TRAY_PID,
        )
        assert result is None


# ------------------------------------------------------------------
# consume_pending_ipc (FR-20f collision: quit beats restart)
# ------------------------------------------------------------------

class TestConsumePendingIpc:

    def test_neither_marker_returns_none_none(self, tmp_path):
        assert consume_pending_ipc(
            tmp_path, _SECRET, _TRAY_PID,
        ) == (None, None)

    def test_restart_only(self, tmp_path):
        _write_marker_raw(tmp_path / MARKER_RESTART, target="manager")
        quit_t, restart_t = consume_pending_ipc(
            tmp_path, _SECRET, _TRAY_PID,
        )
        assert quit_t is None
        assert restart_t == "manager"

    def test_quit_only(self, tmp_path):
        _write_marker_raw(tmp_path / MARKER_QUIT, target="all")
        quit_t, restart_t = consume_pending_ipc(
            tmp_path, _SECRET, _TRAY_PID,
        )
        assert quit_t == "all"
        assert restart_t is None

    def test_both_present_quit_wins_and_restart_deleted(
        self, tmp_path, caplog,
    ):
        import logging
        _write_marker_raw(tmp_path / MARKER_QUIT, target="manager")
        _write_marker_raw(tmp_path / MARKER_RESTART, target="manager")
        with caplog.at_level(logging.WARNING, logger=tray_ipc.logger.name):
            quit_t, restart_t = consume_pending_ipc(
                tmp_path, _SECRET, _TRAY_PID,
            )
        assert quit_t == "manager"
        assert restart_t is None
        # Restart marker should be gone.
        assert not (tmp_path / MARKER_RESTART).exists()
        assert any(
            "quit wins" in rec.message for rec in caplog.records
        )


def _iso(seconds_from_now: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=seconds_from_now)).isoformat()


@pytest.mark.parametrize("name, fields, accepted", [
    pytest.param(MARKER_RESET, {"target": "all"}, True, id="reset-ok"),
    pytest.param(MARKER_RESET, {"target": "all", "action": "quit"}, False, id="quit-payload-renamed-to-reset"),
    pytest.param(MARKER_RESTART, {"target": "manager", "action": "reset"}, False, id="reset-payload-as-restart"),
    pytest.param(MARKER_QUIT, {"target": "all", "action": None}, False, id="no-action"),
    pytest.param(MARKER_RESET, {"target": "manager"}, False, id="reset-target-is-all-only"),
    pytest.param(MARKER_RESET, {"target": "all", "requested_at": 60}, False, id="reset-from-the-future"),
    pytest.param(MARKER_RESET, {"target": "all", "requested_at": -60}, False, id="reset-stale"),
])
def test_marker_action_target_and_reset_freshness(tmp_path, name, fields, accepted):
    """FR-RS-1 (chunk 6 review M2): a marker's ``action`` must match its file
    name, each action has its own targets, and a reset must be within 30 s of now."""
    path = tmp_path / name
    if "requested_at" in fields:  # seconds from now, taken when the test runs
        fields = {**fields, "requested_at": _iso(fields["requested_at"])}
    _write_marker_raw(path, **fields)
    result = read_and_validate_marker(path, _SECRET, _TRAY_PID)
    assert (result == fields["target"]) if accepted else result is None


def test_reset_without_a_time_is_refused(tmp_path):
    """FR-RS-1 (review M2): ``requested_at`` is required on a reset."""
    path = tmp_path / MARKER_RESET
    path.write_text(json.dumps({"secret": _SECRET, "pid": _TRAY_PID, "action": "reset", "target": "all"}),
                    encoding="utf-8")
    assert read_and_validate_marker(path, _SECRET, _TRAY_PID) is None


@pytest.mark.parametrize("write, name, target", [
    pytest.param(lambda d: tray_writer.request_quit(d, "all"), MARKER_QUIT, "all", id="quit"),
    pytest.param(lambda d: tray_writer.request_restart(d), MARKER_RESTART, "manager", id="restart"),
])
def test_the_trays_markers_still_validate(monkeypatch, tmp_path, write, name, target):
    """Tray spec FR-20 with review M2: the tray's writer names the action the
    launcher now checks, so Quit and Restart keep working."""
    monkeypatch.setenv(tray_writer.ENV_SECRET, _SECRET)
    write(tmp_path)
    assert read_and_validate_marker(tmp_path / name, _SECRET, os.getpid()) == target


def test_consume_all_markers_reads_and_deletes_all_three(tmp_path):
    """FR-RS-1, chunk 6 plan: quit, reset and restart are each validated and
    deleted; an invalid one reads as None (the loop applies the precedence)."""
    _write_marker_raw(tmp_path / MARKER_QUIT, secret="wrong-secret-xxxxxxxxxxxxxxxxxxxxxxxxx", target="all")
    _write_marker_raw(tmp_path / MARKER_RESET, target="all")
    _write_marker_raw(tmp_path / MARKER_RESTART, target="manager")

    assert consume_all_markers(tmp_path, _SECRET, _TRAY_PID) == (None, "all", "manager")
    for name in (MARKER_QUIT, MARKER_RESET, MARKER_RESTART):
        assert not (tmp_path / name).exists()


# ------------------------------------------------------------------
# tighten_acls_windows — re-export from shared.file_acls
# ------------------------------------------------------------------

class TestTightenAclsWindows:
    """Backward-compat: ``launcher.tray_ipc`` re-exports the helper that
    now lives in ``shared.file_acls`` (issue #169).
    """

    def test_re_export_points_at_shared(self):
        from shared import file_acls
        assert tray_ipc.tighten_acls_windows is file_acls.tighten_acls_windows

    def test_no_op_on_posix(self, tmp_path, mocker):
        from shared import file_acls
        mocker.patch.object(file_acls.sys, "platform", "linux")
        # Must not raise on a POSIX test run.
        tray_ipc.tighten_acls_windows(tmp_path)


# ------------------------------------------------------------------
# Empty-secret guard (Fix D / issue #79)
# ------------------------------------------------------------------

class TestEmptySecretGuard:

    def test_rejects_when_expected_secret_empty(self, tmp_path):
        path = tmp_path / MARKER_QUIT
        # Marker carries the empty secret; without the guard,
        # hmac.compare_digest("", "") would pass and accept it.
        _write_marker_raw(path, secret="", target="all")
        assert read_and_validate_marker(path, "", _TRAY_PID) is None

    def test_rejects_when_expected_secret_too_short(self, tmp_path):
        short = "x" * 16  # 16 < 32 minimum
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, secret=short, target="all")
        assert read_and_validate_marker(path, short, _TRAY_PID) is None

    def test_accepts_proper_token_urlsafe_32_output(self, tmp_path):
        # secrets.token_urlsafe(32) yields ~43 characters.
        proper = generate_secret()
        assert len(proper) >= 32
        path = tmp_path / MARKER_QUIT
        _write_marker_raw(path, secret=proper, target="all")
        assert read_and_validate_marker(
            path, proper, _TRAY_PID,
        ) == "all"
