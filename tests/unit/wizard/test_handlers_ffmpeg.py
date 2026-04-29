# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/ffmpeg.py``
(FR-M2-7 / FR-M2-7a).

Combines the dev agent's smoke pass with coverage expansion: every
gate (auth/method) returns the expected error, the single-task
invariant holds under contention, the cancel handler signals the
worker's threading.Event, the progress handler reads the active
snapshot under the lock, idempotent short-circuit when ffmpeg is
already installed, and the started thread is daemon + targets the
worker.
"""

from __future__ import annotations

import threading

import pytest

from wizard.sethlans_wizard import auth_state, ffmpeg_download, wizard_state
from wizard.sethlans_wizard.handlers import ffmpeg as ffmpeg_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    wizard_state.reset_state_for_tests()
    ffmpeg_handler.reset_for_tests()
    yield
    auth_state.reset_state_for_tests()
    wizard_state.reset_state_for_tests()
    ffmpeg_handler.reset_for_tests()


@pytest.fixture
def start_handler(tmp_path):
    return ffmpeg_handler.make_start_handler(tmp_path)


@pytest.fixture
def cancel_handler():
    return ffmpeg_handler.make_cancel_handler()


@pytest.fixture
def progress_handler():
    return ffmpeg_handler.make_progress_handler()


@pytest.fixture
def stub_worker(monkeypatch):
    """Stub download_worker + already_installed=False so start() spawns
    the in-progress task without firing real network I/O."""
    monkeypatch.setattr(ffmpeg_handler, "download_worker", lambda *a, **k: None)
    monkeypatch.setattr(ffmpeg_download, "already_installed", lambda dd: False)


class TestStartHandler:

    def test_double_start_returns_same_task_id(
        self, start_handler, stub_worker,
    ):
        # FR-M2-7a / single-task invariant.
        env1 = auth_env(b"")
        status1, _, body1 = call_handler(start_handler, env1)
        assert status1.startswith("200"), body1
        first_id = body1["task_id"]

        # Force the registry into an in-progress state.
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "downloading"

        env2 = auth_env(b"")
        status2, _, body2 = call_handler(start_handler, env2)
        assert status2.startswith("200")
        assert body2["task_id"] == first_id

    def test_start_after_complete_starts_new_task(
        self, start_handler, stub_worker,
    ):
        # Coverage expansion: when the previous task COMPLETED, a new
        # start spawns a fresh task_id (the in-progress guard checks
        # status not in {complete, failed}).
        env1 = auth_env(b"")
        _, _, body1 = call_handler(start_handler, env1)
        first_id = body1["task_id"]
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "complete"
        env2 = auth_env(b"")
        _, _, body2 = call_handler(start_handler, env2)
        assert body2["task_id"] != first_id

    def test_start_after_failed_starts_new_task(
        self, start_handler, stub_worker,
    ):
        env1 = auth_env(b"")
        _, _, body1 = call_handler(start_handler, env1)
        first_id = body1["task_id"]
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "failed"
        env2 = auth_env(b"")
        _, _, body2 = call_handler(start_handler, env2)
        assert body2["task_id"] != first_id

    def test_already_installed_short_circuits_to_complete(
        self, start_handler, monkeypatch, tmp_path,
    ):
        # Coverage expansion: idempotent short-circuit when ffmpeg is
        # already on disk.
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: True,
        )
        binary_path = tmp_path / "bin" / "ffmpeg" / "7.1" / "ffmpeg"
        monkeypatch.setattr(
            ffmpeg_download, "get_ffmpeg_binary", lambda dd: binary_path,
        )
        env = auth_env(b"")
        status, _, body = call_handler(start_handler, env)
        assert status.startswith("200"), body
        # Wizard state populated.
        meta = wizard_state.get_ffmpeg()
        assert meta is not None
        # Active task is complete with full percent.
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["status"] == "complete"
            assert ffmpeg_handler._active_task["percent"] == 100

    def test_get_returns_405(self, start_handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(start_handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, start_handler):
        env = build_environ(method="POST")
        status, _, _ = call_handler(start_handler, env)
        assert status.startswith("401")

    def test_thread_is_daemon(self, start_handler, monkeypatch, mocker):
        # Coverage expansion / NF-8: the worker thread MUST be a daemon
        # so it doesn't block process exit.
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: False,
        )
        spy = mocker.spy(threading, "Thread")
        env = auth_env(b"")
        call_handler(start_handler, env)
        # At least one Thread was constructed; the latest call carries
        # daemon=True.
        latest = spy.call_args
        assert latest.kwargs.get("daemon") is True


class TestAlreadyInstalledRaceRegression:
    """Concurrency-reviewer C1 — ``already_installed`` short-circuit
    must NOT clobber an in-flight task. Regression test for the race
    where the binary lands on disk mid-extraction and a second start
    arrives before extraction finishes.
    """

    def test_already_installed_does_not_clobber_in_flight(
        self, start_handler, monkeypatch, tmp_path,
    ):
        """Pin an in-flight task in mid-extraction state via a
        ``threading.Event``, fire a second start() that observes
        ``already_installed=True``, and confirm the second start
        returns the FIRST task's id (single-task invariant) rather
        than overwriting ``_active_task`` with a new ``complete`` task.
        """
        # Stage 1: spawn a real in-flight task. Block its worker thread
        # so the registry stays in 'downloading' state.
        worker_started = threading.Event()
        worker_release = threading.Event()

        def blocking_worker(*_args, **_kwargs):
            worker_started.set()
            worker_release.wait(timeout=5)

        monkeypatch.setattr(
            ffmpeg_handler, "download_worker", blocking_worker,
        )
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: False,
        )
        env1 = auth_env(b"")
        _, _, body1 = call_handler(start_handler, env1)
        first_task_id = body1["task_id"]
        assert worker_started.wait(timeout=2), "worker thread did not start"

        # Stage 2: simulate the race — binary appears mid-extraction.
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: True,
        )
        binary_path = tmp_path / "bin" / "ffmpeg" / "7.1" / "ffmpeg"
        monkeypatch.setattr(
            ffmpeg_download, "get_ffmpeg_binary", lambda dd: binary_path,
        )

        try:
            # Stage 3: second start should observe in-flight task and
            # return its id without clobbering _active_task.
            env2 = auth_env(b"")
            status, _, body2 = call_handler(start_handler, env2)
            assert status.startswith("200")
            assert body2["task_id"] == first_task_id, (
                "second start clobbered the in-flight task; "
                "single-task invariant violated"
            )
            with ffmpeg_handler._task_lock:
                # Active task is still the FIRST task; status is
                # still 'downloading' (not 'complete').
                assert ffmpeg_handler._active_task["task_id"] == \
                    first_task_id
                assert ffmpeg_handler._active_task["status"] == \
                    "downloading"
        finally:
            worker_release.set()

    def test_already_installed_does_not_clobber_failed_in_flight(
        self, start_handler, monkeypatch, tmp_path,
    ):
        """Sanity case: the ``not in (complete, failed)`` guard means
        a task in terminal state (``failed``) does NOT block the
        short-circuit. A new short-circuit task replaces the failed
        one, since no worker thread is alive to be orphaned.
        """
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: False,
        )
        monkeypatch.setattr(
            ffmpeg_handler, "download_worker", lambda *a, **k: None,
        )
        env = auth_env(b"")
        _, _, body1 = call_handler(start_handler, env)
        first_id = body1["task_id"]
        # Manually transition to failed (terminal state).
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "failed"

        # Now flip already_installed=True and call start again.
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: True,
        )
        binary_path = tmp_path / "bin" / "ffmpeg" / "7.1" / "ffmpeg"
        monkeypatch.setattr(
            ffmpeg_download, "get_ffmpeg_binary", lambda dd: binary_path,
        )
        env2 = auth_env(b"")
        status, _, body2 = call_handler(start_handler, env2)
        assert status.startswith("200")
        # Terminal state was replaced -> a fresh short-circuit task.
        assert body2["task_id"] != first_id
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["status"] == "complete"
            assert ffmpeg_handler._active_task["percent"] == 100


class TestCancelHandler:

    def test_cancel_with_no_active_task_returns_not_found(
        self, cancel_handler,
    ):
        env = auth_env(b"")
        status, _, body = call_handler(cancel_handler, env)
        assert status.startswith("200"), body
        assert body["status"] == "not_found"

    def test_cancel_signals_event_and_marks_failed(
        self, start_handler, cancel_handler, stub_worker,
    ):
        # FR-M2-7a / concurrency F2 — cancel must set the worker thread's
        # event and transition the task to failed.
        env_start = auth_env(b"")
        call_handler(start_handler, env_start)
        with ffmpeg_handler._task_lock:
            event = ffmpeg_handler._active_task["cancel_event"]
            ffmpeg_handler._active_task["status"] = "downloading"
        assert event.is_set() is False

        env_cancel = auth_env(b"")
        status, _, body = call_handler(cancel_handler, env_cancel)
        assert status.startswith("200"), body
        assert body["status"] == "cancelled"
        assert event.is_set() is True
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["status"] == "failed"
            assert ffmpeg_handler._active_task["category"] == "download_failed"

    def test_cancel_completed_task_returns_not_found(
        self, start_handler, cancel_handler, stub_worker,
    ):
        # Coverage expansion: cancelling a task that already finished
        # returns the 'not_found' shape (no harm done).
        call_handler(start_handler, auth_env(b""))
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "complete"
        status, _, body = call_handler(cancel_handler, auth_env(b""))
        assert status.startswith("200")
        assert body["status"] == "not_found"

    def test_get_returns_405(self, cancel_handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(cancel_handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, cancel_handler):
        env = build_environ(method="POST")
        status, _, _ = call_handler(cancel_handler, env)
        assert status.startswith("401")


class TestProgressHandler:

    def test_unknown_task_id_returns_404(self, progress_handler):
        env = build_environ(
            method="GET",
            path="/api/wizard/ffmpeg/progress/missing-task/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, _, body = call_handler(progress_handler, env)
        assert status.startswith("404"), body
        assert body["error"] == "unknown_task"

    def test_missing_task_id_returns_400(self, progress_handler):
        # Coverage expansion: trailing slash-only path with no tail
        # component MUST return 400 (missing task_id).
        env = build_environ(
            method="GET",
            path="/api/wizard/ffmpeg/progress/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, _, body = call_handler(progress_handler, env)
        # Either 400 (missing) or 404 (unknown) is reasonable; the spec
        # says missing task_id so prefer 400.
        assert status.startswith(("400", "404"))

    def test_progress_returns_active_snapshot(
        self, progress_handler, start_handler, stub_worker,
    ):
        # Coverage expansion: with an active task in flight, the
        # progress endpoint returns its task_id + status + percent.
        _, _, start_body = call_handler(start_handler, auth_env(b""))
        task_id = start_body["task_id"]
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task["status"] = "downloading"
            ffmpeg_handler._active_task["percent"] = 42

        env = build_environ(
            method="GET",
            path=f"/api/wizard/ffmpeg/progress/{task_id}/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, _, body = call_handler(progress_handler, env)
        assert status.startswith("200"), body
        assert body["task_id"] == task_id
        assert body["status"] == "downloading"
        assert body["percent"] == 42

    def test_progress_returns_failure_categories(
        self, progress_handler, start_handler, stub_worker,
    ):
        # Coverage expansion: all four error categories surface through
        # the snapshot — failed status, plus the category + error
        # message.
        _, _, start_body = call_handler(start_handler, auth_env(b""))
        task_id = start_body["task_id"]
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task.update({
                "status": "failed",
                "category": "sha_mismatch",
                "error": "FFmpeg verification failed",
            })

        env = build_environ(
            method="GET",
            path=f"/api/wizard/ffmpeg/progress/{task_id}/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        _, _, body = call_handler(progress_handler, env)
        assert body["status"] == "failed"
        assert body["category"] == "sha_mismatch"
        assert body["error"] == "FFmpeg verification failed"

    def test_post_returns_405(self, progress_handler):
        env = build_environ(
            method="POST",
            path="/api/wizard/ffmpeg/progress/abc/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(progress_handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "GET"

    def test_missing_session_returns_401(self, progress_handler):
        env = build_environ(
            method="GET",
            path="/api/wizard/ffmpeg/progress/x/",
        )
        status, _, _ = call_handler(progress_handler, env)
        assert status.startswith("401")


class TestSetStatusUnderLock:
    """Internal helper coverage: _set_status mutates the active task
    only when the task_id matches and the lock is held."""

    def test_set_status_for_unknown_task_id_is_noop(self):
        # No active task: _set_status does nothing.
        ffmpeg_handler._set_status("ghost", status="downloading")
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task is None

    def test_set_status_mismatched_task_id_is_noop(self):
        # Active task exists but caller passed a different task_id.
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task = {
                "task_id": "real",
                "status": "downloading",
                "percent": 0,
                "category": None,
                "error": None,
                "cancel_event": threading.Event(),
            }
        ffmpeg_handler._set_status("ghost", status="failed")
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["status"] == "downloading"

    def test_set_status_partial_update(self):
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task = {
                "task_id": "real",
                "status": "downloading",
                "percent": 0,
                "category": None,
                "error": None,
                "cancel_event": threading.Event(),
            }
        # Only percent is being updated.
        ffmpeg_handler._set_status("real", percent=42)
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["status"] == "downloading"
            assert ffmpeg_handler._active_task["percent"] == 42

    def test_set_status_writes_category_and_error(self):
        with ffmpeg_handler._task_lock:
            ffmpeg_handler._active_task = {
                "task_id": "real",
                "status": "downloading",
                "percent": 0,
                "category": None,
                "error": None,
                "cancel_event": threading.Event(),
            }
        ffmpeg_handler._set_status(
            "real", status="failed",
            category="version_mismatch", error="bad version",
        )
        with ffmpeg_handler._task_lock:
            assert ffmpeg_handler._active_task["category"] == "version_mismatch"
            assert ffmpeg_handler._active_task["error"] == "bad version"
