# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_blender.py``.

Phase 4e of the Waitress migration: the handlers are sync WSGI.
Tests drive them directly via a WSGI ``environ`` + ``start_response``
capture (no async plumbing).  The download-progress task registry
is reset by the autouse fixture in ``conftest.py``.

This file covers ``handle_blender_progress`` and
``handle_cancel_blender``.  The ``handle_start_blender_download``
permutations (happy path, in-progress guard, already-installed
guard, default version fallback, body-validation, contention) live
in the sibling ``test_setup_handlers_blender_download.py`` to keep
both files under the 300-line ceiling.
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_blender import (
    handle_blender_progress,
    handle_cancel_blender,
)
from sethlans_worker_agent.web_ui.setup import download_progress
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture


# -------------------------------------------------------------------
# Local helpers -- kept in-file so this suite has no external dep
# beyond ``_wsgi_helpers``.
# -------------------------------------------------------------------

def _environ_get(path: str = '/api/setup/blender/progress/') -> dict:
    return {
        'REQUEST_METHOD': 'GET',
        'PATH_INFO': path,
        'wsgi.input': io.BytesIO(b''),
        'CONTENT_LENGTH': '0',
    }


def _environ_post(
    body: bytes, path: str = '/api/setup/blender/cancel/',
) -> dict:
    return {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': path,
        'CONTENT_LENGTH': str(len(body)),
        'wsgi.input': io.BytesIO(body),
    }


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


def _call_progress(cap: StartResponseCapture) -> bytes:
    return b''.join(handle_blender_progress(_environ_get(), cap))


def _call_cancel(body: bytes, cap: StartResponseCapture) -> bytes:
    return b''.join(handle_cancel_blender(_environ_post(body), cap))


def _seed_active_task(status: str = "downloading", percent: int = 42):
    """Create a blender_* task in the registry and return it."""
    tid, prog = download_progress.create_tagged_task("blender_")
    download_progress.update_task(
        tid, status=status, percent=percent,
    )
    return tid, download_progress.get_task(tid)


# -------------------------------------------------------------------
# handle_blender_progress -- read-only, no mutation lock
# -------------------------------------------------------------------

class TestHandleBlenderProgress:
    def test_returns_idle_when_no_active_task(self):
        cap = StartResponseCapture()
        out = _call_progress(cap)

        assert _status_code(cap) == 200
        assert json.loads(out) == {
            "task_id": None,
            "status": "idle",
            "percent": 0,
            "error": None,
        }

    def test_returns_active_task_info(self):
        tid, _ = _seed_active_task(status="downloading", percent=42)

        cap = StartResponseCapture()
        out = _call_progress(cap)

        assert _status_code(cap) == 200
        body = json.loads(out)
        assert body["task_id"] == tid
        assert body["status"] == "downloading"
        assert body["percent"] == 42
        assert body["error"] is None

    def test_returns_extracting_task(self):
        """Extracting is one of the active statuses."""
        tid, _ = _seed_active_task(status="extracting", percent=95)

        cap = StartResponseCapture()
        out = _call_progress(cap)

        body = json.loads(out)
        assert body["task_id"] == tid
        assert body["status"] == "extracting"
        assert body["percent"] == 95

    def test_does_not_take_mutation_lock(self):
        """GET must not 409 even when a mutation is in flight.

        The wizard polls this endpoint repeatedly; taking the
        mutation lock would cause a spinner storm whenever another
        endpoint is mid-mutation.
        """
        holder_done = threading.Event()
        release = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                holder_done.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert holder_done.wait(timeout=5)

            cap = StartResponseCapture()
            _call_progress(cap)

            # Still 200 even though the mutation lock is held.
            assert _status_code(cap) == 200
        finally:
            release.set()
            t.join(timeout=5)


# -------------------------------------------------------------------
# handle_cancel_blender -- POST, mutation-locked
# -------------------------------------------------------------------

class TestHandleCancelBlender:
    def test_404_when_no_active_task(self):
        cap = StartResponseCapture()
        out = _call_cancel(b'', cap)

        assert _status_code(cap) == 404
        assert json.loads(out) == {
            "error": "No active download to cancel.",
        }

    def test_cancels_active_task(self):
        tid, prog = _seed_active_task()
        assert not prog.cancel_event.is_set()

        cap = StartResponseCapture()
        out = _call_cancel(b'', cap)

        assert _status_code(cap) == 200
        body = json.loads(out)
        assert body == {"status": "cancelling", "task_id": tid}

        # The cancel_event on the original snapshot is preserved
        # across update_task(), so checking the freshly-fetched
        # task's event is equivalent.
        fresh = download_progress.get_task(tid)
        assert fresh.cancel_event.is_set()

    def test_contention_returns_409(self):
        """Hold the mutation lock elsewhere; cancel must 409."""
        # Seed an active task so we would otherwise succeed.
        tid, prog = _seed_active_task()

        lock_held = threading.Event()
        release = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            cap = StartResponseCapture()
            out = _call_cancel(b'', cap)

            assert _status_code(cap) == 409
            assert json.loads(out) == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
            # The cancel_event on the original task must NOT have
            # been set -- contention must prevent the mutation.
            fresh = download_progress.get_task(tid)
            assert not fresh.cancel_event.is_set()
        finally:
            release.set()
            t.join(timeout=5)
