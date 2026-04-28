# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/tray_ipc.py`` (tray spec FR-20b..FR-20g)."""

from __future__ import annotations

import datetime as _dt
import json

from launcher import tray_ipc
from launcher.tray_ipc import (
    MARKER_QUIT,
    MARKER_RESTART,
    consume_pending_ipc,
    generate_secret,
    read_and_validate_marker,
    sweep_stale_markers,
)


# Must be >= 32 chars per _validate_payload's empty-secret guard;
# production uses secrets.token_urlsafe(32) which yields ~43 chars.
_SECRET = "test-launcher-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxx"
_TRAY_PID = 42424


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _write_marker_raw(
    path, secret=_SECRET, pid=_TRAY_PID,
    target="manager", requested_at=None,
):
    payload = {
        "secret": secret,
        "pid": pid,
        "target": target,
        "requested_at": requested_at or _now_iso(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


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
        (tmp_path / MARKER_QUIT).write_text("x", encoding="utf-8")
        (tmp_path / MARKER_RESTART).write_text("x", encoding="utf-8")
        sweep_stale_markers(tmp_path)
        assert not (tmp_path / MARKER_QUIT).exists()
        assert not (tmp_path / MARKER_RESTART).exists()

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
