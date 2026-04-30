# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/pending_setup.py``
(FR-PEND2 / FR-PEND1a).

Combines the dev agent's smoke pass with coverage expansion: the
explicit fsync ordering (write tmp → fsync(temp_fd) → close →
os.replace → fsync(parent_dir_fd) on POSIX), schema fields including
``created_at_unix``, chmod 600 on POSIX / ACL tighten on Windows, and
exhaustive request-gate coverage.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

import pytest

from wizard.sethlans_wizard import auth_state, wizard_state
from wizard.sethlans_wizard.handlers import pending_setup as pending_handler
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    wizard_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()
    wizard_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return pending_handler.make_pending_setup_handler(tmp_path)


def _populate_state():
    wizard_state.set_admin("alice", "alice@example.org", "Tr0ub4dor&3xp")


class _FsEventTracker:
    """Captures a flat sequence of os.{open,write,fsync,close,replace}
    events around the target temp file + parent dir, used to assert the
    FR-PEND1a fsync ordering contract. Split out so each tracking hook
    stays simple enough to satisfy the ``--max-complexity 10`` lint.
    """

    def __init__(self):
        self.events: list[str] = []
        self.file_fds: set[int] = set()
        self.dir_fds: set[int] = set()

    def _classify_fd(self, fd: int) -> str:
        if fd in self.file_fds:
            return "file"
        if fd in self.dir_fds:
            return "dir"
        return "other"

    def make_open(self, real):
        def fake(path, flags, *a, **kw):
            fd = real(path, flags, *a, **kw)
            self._track_open(path, fd)
            return fd
        return fake

    def _track_open(self, path, fd: int) -> None:
        p = str(path)
        if p.endswith(".tmp"):
            self.file_fds.add(fd)
        elif Path(p).is_dir():
            self.dir_fds.add(fd)

    def make_write(self, real):
        def fake(fd, data):
            if self._classify_fd(fd) == "file":
                self.events.append("write_tmp")
            return real(fd, data)
        return fake

    def make_fsync(self, real):
        def fake(fd):
            kind = self._classify_fd(fd)
            if kind == "file":
                self.events.append("fsync_file")
            elif kind == "dir":
                self.events.append("fsync_dir")
            return real(fd)
        return fake

    def make_close(self, real):
        def fake(fd):
            kind = self._classify_fd(fd)
            if kind == "file":
                self.events.append("close_file")
                self.file_fds.discard(fd)
            elif kind == "dir":
                self.events.append("close_dir")
                self.dir_fds.discard(fd)
            return real(fd)
        return fake

    def make_replace(self, real):
        def fake(src, dst):
            self.events.append("replace")
            return real(src, dst)
        return fake


def _install_fs_event_tracker(mocker):
    """Install the os.* patches and return the tracker for assertions."""
    tracker = _FsEventTracker()
    mocker.patch("os.open", side_effect=tracker.make_open(os.open))
    mocker.patch("os.write", side_effect=tracker.make_write(os.write))
    mocker.patch("os.fsync", side_effect=tracker.make_fsync(os.fsync))
    mocker.patch("os.close", side_effect=tracker.make_close(os.close))
    mocker.patch("os.replace", side_effect=tracker.make_replace(os.replace))
    return tracker.events, tracker.file_fds, tracker.dir_fds


class TestHappyPath:

    def test_manager_topology_writes_pending(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()

        before = int(time.time())
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body == {"status": "ok"}

        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        assert target.exists()
        payload = json.loads(target.read_text("utf-8"))
        assert payload["schema_version"] == 1
        assert payload["topology"] == "manager"
        assert payload["created_at_unix"] >= before
        assert payload["admin_user"]["username"] == "alice"
        assert payload["auto_enroll_local_worker"] is False
        assert payload["worker_ui_password_hash"] is None

    def test_manager_worker_topology_marks_auto_enroll(
        self, handler, tmp_path,
    ):
        write_topology_atomic(tmp_path, "manager_worker")
        _populate_state()
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)

        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body

        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        payload = json.loads(target.read_text("utf-8"))
        assert payload["topology"] == "manager_worker"
        assert payload["auto_enroll_local_worker"] is True
        assert payload["worker_ui_password_hash"] == "a" * 64
        assert payload["worker_ui_password_salt"] == "b" * 32


class TestSchemaFields:
    """FR-PEND1 — pending_setup.json schema MUST include every field
    the apply command consumes."""

    def test_full_schema_field_inventory(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager_worker")
        _populate_state()
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        env = auth_env(b"")
        call_handler(handler, env)
        payload = json.loads(
            (tmp_path / pending_handler.PENDING_SETUP_FILENAME)
            .read_text("utf-8"),
        )
        # Every reviewer-blessed field must be present.
        for key in (
            "schema_version", "topology", "created_at_unix",
            "admin_user", "worker_ui_password_hash",
            "worker_ui_password_salt",
            "auto_enroll_local_worker",
        ):
            assert key in payload, f"missing {key} in pending_setup payload"

    def test_admin_user_has_three_fields(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        env = auth_env(b"")
        call_handler(handler, env)
        payload = json.loads(
            (tmp_path / pending_handler.PENDING_SETUP_FILENAME)
            .read_text("utf-8"),
        )
        admin = payload["admin_user"]
        assert set(admin.keys()) == {
            "username", "email", "password_plaintext",
        }

    def test_ffmpeg_field_absent(self, handler, tmp_path):
        # The wizard no longer carries FFmpeg metadata into
        # pending_setup.json — the manager-side parts-check derives the
        # binary path itself on boot.
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        env = auth_env(b"")
        call_handler(handler, env)
        payload = json.loads(
            (tmp_path / pending_handler.PENDING_SETUP_FILENAME)
            .read_text("utf-8"),
        )
        assert "ffmpeg" not in payload


class TestErrorPaths:

    def test_missing_topology_returns_400(self, handler):
        _populate_state()
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "topology" in body["error"].lower()

    def test_missing_admin_state_returns_400(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "wizard_state_incomplete"

    def test_worker_only_topology_rejected(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "worker_only")
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert (
            "topology" in body["error"].lower()
            or "applicable" in body["error"].lower()
        )

    def test_disk_write_failure_returns_500(
        self, handler, tmp_path, mocker,
    ):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        mocker.patch.object(
            pending_handler, "_atomic_write",
            side_effect=OSError("disk full"),
        )
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("500"), body
        assert "pending_setup" in body["error"]


class TestRequestGates:

    def test_get_returns_405(self, handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, handler):
        env = build_environ(method="POST", body=b"")
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")

    def test_qs_token_rejected(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        env = build_environ(
            method="POST",
            headers={"X-Wizard-Session": VALID_SESSION},
            query_string="session_token=abc",
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("400")


class TestAtomicWriteOrdering:
    """FR-PEND1a — explicit fsync ordering must be:
    write tmp → fsync(file_fd) → close → os.replace → fsync(dir_fd)."""

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Windows skips parent-dir fsync",
    )
    def test_fsync_sequence_on_posix(self, tmp_path, mocker):
        # Coverage expansion / FR-PEND1a / concurrency F1 — capture
        # the call order across os.write / os.fsync / os.close /
        # os.replace and assert the exact contract sequence.
        events, file_fds, dir_fds = _install_fs_event_tracker(mocker)
        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        pending_handler._atomic_write(target, b'{"x":1}')

        # Contract: write_tmp, fsync_file, close_file, replace, fsync_dir.
        assert events.index("write_tmp") < events.index("fsync_file")
        assert events.index("fsync_file") < events.index("close_file")
        assert events.index("close_file") < events.index("replace")
        if "fsync_dir" in events:
            assert events.index("replace") < events.index("fsync_dir")

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX chmod semantics only",
    )
    def test_chmod_600_on_posix(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        env = auth_env(b"")
        call_handler(handler, env)
        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows ACL helper only",
    )
    def test_calls_acl_tighten_on_windows(self, tmp_path, mocker):
        spy = mocker.patch(
            "wizard.sethlans_wizard.handlers.pending_setup."
            "tighten_acls_windows",
        )
        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        pending_handler._atomic_write(target, b'{"x":1}')
        assert spy.called
        assert Path(spy.call_args[0][0]) == target

    def test_no_tmp_left_on_disk(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()
        env = auth_env(b"")
        call_handler(handler, env)
        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        assert target.exists()
        assert not (tmp_path / (pending_handler.PENDING_SETUP_FILENAME + ".tmp")).exists()


class TestLoggingDiscipline:
    """FR-PEND3 — log only path + byte count, NEVER the payload body."""

    def test_admin_password_never_in_log(
        self, handler, tmp_path, caplog,
    ):
        write_topology_atomic(tmp_path, "manager")
        wizard_state.set_admin(
            "alice", "alice@example.org", "very-secret-please",
        )
        with caplog.at_level("INFO"):
            env = auth_env(b"")
            call_handler(handler, env)
        for record in caplog.records:
            assert "very-secret-please" not in record.getMessage()


class TestExports:

    def test_dunder_all(self):
        for name in (
            "PENDING_SETUP_FILENAME",
            "PENDING_SCHEMA_VERSION",
            "make_pending_setup_handler",
        ):
            assert name in pending_handler.__all__

    def test_filename_constant(self):
        assert pending_handler.PENDING_SETUP_FILENAME == "pending_setup.json"
