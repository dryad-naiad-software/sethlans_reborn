# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_status.py``.

Phase 4d of the Waitress migration: the handlers are sync WSGI.
Tests drive them directly via a WSGI ``environ`` + ``start_response``
capture (no async plumbing).  Module-level state (``_current_topology``
/ ``_current_checkpoints``) is reset by the autouse fixture in
``conftest.py``.

This file covers the request/response behaviour of
``handle_setup_status``, ``handle_set_topology``, and the
``append_wizard_checkpoint`` TOCTOU invariant.  The concurrent
reader-writer snapshot tests live in the sibling
``test_setup_handlers_status_concurrency.py`` file.
"""

import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_status import (
    get_current_topology,
    get_current_checkpoints,
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup import handlers_status
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture
from tests.unit.worker._handlers_status_helpers import (
    call_status,
    call_topology,
    status_code,
)


# -------------------------------------------------------------------
# handle_setup_status
# -------------------------------------------------------------------

class TestHandleSetupStatus:
    def test_returns_complete_when_sentinel_exists(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".config_store.get_data_dir",
            return_value="/fake/dir",
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".read_sentinel",
            return_value={
                "version": 1,
                "topology": "worker_only",
                "checkpoints": ["topology_chosen", "enrolled"],
            },
        )

        cap = StartResponseCapture()
        out = call_status(cap)

        assert status_code(cap) == 200
        body = json.loads(out)
        assert body["complete"] is True
        assert body["topology"] == "worker_only"
        assert "topology_chosen" in body["checkpoints"]

    def test_returns_incomplete_with_in_progress_state(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".config_store.get_data_dir",
            return_value="/fake/dir",
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".read_sentinel",
            return_value=None,
        )
        # Set some in-progress state (use the public setter so we
        # route through the state lock).
        handlers_status._current_topology = "worker_only"
        append_wizard_checkpoint("topology_chosen")

        cap = StartResponseCapture()
        out = call_status(cap)

        assert status_code(cap) == 200
        body = json.loads(out)
        assert body["complete"] is False
        assert body["topology"] == "worker_only"
        assert body["checkpoints"] == ["topology_chosen"]

    def test_returns_incomplete_with_no_state(self, mocker):
        """Sentinel missing + no in-progress state -> empty snapshot."""
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".config_store.get_data_dir",
            return_value="/fake/dir",
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".read_sentinel",
            return_value=None,
        )

        cap = StartResponseCapture()
        out = call_status(cap)

        assert status_code(cap) == 200
        body = json.loads(out)
        assert body == {
            "complete": False,
            "topology": None,
            "checkpoints": [],
        }


# -------------------------------------------------------------------
# handle_set_topology
# -------------------------------------------------------------------

class TestHandleSetTopology:
    def test_accepts_worker_only(self):
        body = json.dumps({"topology": "worker_only"}).encode()
        cap = StartResponseCapture()
        out = call_topology(body, cap)

        assert status_code(cap) == 200
        resp = json.loads(out)
        assert resp["status"] == "ok"
        assert resp["topology"] == "worker_only"
        assert "topology_chosen" in resp["checkpoints"]
        assert get_current_topology() == "worker_only"

    def test_rejects_invalid_topology(self):
        body = json.dumps({"topology": "manager"}).encode()
        cap = StartResponseCapture()
        out = call_topology(body, cap)

        assert status_code(cap) == 400
        assert "Invalid topology" in json.loads(out)["error"]

    def test_rejects_missing_topology(self):
        body = json.dumps({}).encode()
        cap = StartResponseCapture()
        call_topology(body, cap)

        assert status_code(cap) == 400

    def test_rejects_non_object_body(self):
        body = json.dumps([1, 2]).encode()
        cap = StartResponseCapture()
        out = call_topology(body, cap)

        assert status_code(cap) == 400
        assert "JSON object" in json.loads(out)["error"]

    def test_rejects_malformed_json(self):
        body = b'not-json{'
        cap = StartResponseCapture()
        out = call_topology(body, cap)

        assert status_code(cap) == 400
        assert json.loads(out) == {"error": "Invalid JSON"}

    def test_body_over_cap_returns_413(self):
        body = b'x' * (4096 + 1)
        cap = StartResponseCapture()
        out = call_topology(body, cap)

        assert status_code(cap) == 413
        assert json.loads(out) == {"error": "Request body too large"}

    def test_tracks_checkpoint(self):
        body = json.dumps({"topology": "worker_only"}).encode()
        cap = StartResponseCapture()
        call_topology(body, cap)

        assert "topology_chosen" in get_current_checkpoints()

    def test_contention_returns_409(self):
        """Hold ``setup_mutation_lock`` on one thread, call the handler
        on the main thread, expect 409.

        Uses ``threading.Event`` pairs for deterministic hand-off.
        """
        lock_held = threading.Event()
        release_holder = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                release_holder.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            body = json.dumps({"topology": "worker_only"}).encode()
            cap = StartResponseCapture()
            out = call_topology(body, cap)

            assert status_code(cap) == 409
            assert json.loads(out) == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
            # Topology must not have been written.
            assert get_current_topology() is None
        finally:
            release_holder.set()
            t.join(timeout=5)


# -------------------------------------------------------------------
# append_wizard_checkpoint
# -------------------------------------------------------------------

class TestAppendWizardCheckpoint:
    def test_appends_checkpoint(self):
        append_wizard_checkpoint("enrolled")
        assert "enrolled" in get_current_checkpoints()

    def test_skips_duplicate(self):
        append_wizard_checkpoint("enrolled")
        append_wizard_checkpoint("enrolled")
        assert get_current_checkpoints().count("enrolled") == 1

    def test_concurrent_callers_do_not_double_append(self):
        """TOCTOU regression: N threads call append_wizard_checkpoint
        with the same name concurrently -> exactly one entry.

        Without a lock around the ``not in`` / ``append`` pair, two
        threads could both observe ``"foo" not in list`` before either
        appends and both append.  A ``threading.Barrier`` makes the
        race maximally tight.
        """
        n = 8
        barrier = threading.Barrier(n)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                append_wizard_checkpoint("foo")
            except Exception as exc:  # pragma: no cover - debug aid
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        cps = get_current_checkpoints()
        assert cps.count("foo") == 1, (
            f"double-append detected: {cps}"
        )
