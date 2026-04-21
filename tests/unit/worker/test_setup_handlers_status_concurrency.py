# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Concurrent reader-writer tests for ``handlers_status``.

Companion to ``test_setup_handlers_status.py``: exercises the
thread-safety invariants added in Phase 4d for GET snapshots
racing with in-flight mutations.

* GETs must not block on ``setup_mutation_lock`` (readers stay
  lock-free w.r.t. the mutation lock and only take the short
  ``_state_lock`` critical section).
* Every GET snapshot must be atomic -- a reader observing a
  checkpoint list must always see a prefix of the writer's append
  sequence, never a torn interleaving.
"""

import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_status import (
    get_current_checkpoints,
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup import handlers_status
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture
from tests.unit.worker._handlers_status_helpers import (
    call_status,
    status_code,
)


class TestConcurrentReaderWriter:
    def test_get_not_blocked_by_setup_mutation_lock(self, mocker):
        """GET must not be gated by ``setup_mutation_lock``.

        Per the lock module's design, read endpoints MUST NOT take
        ``setup_mutation_lock`` -- a GET during a long-running
        mutation should still return 200.  We hold the mutation
        lock on a holder thread and fire GETs; they must all
        succeed with a consistent snapshot.
        """
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

        # Seed a known state under the state lock.
        handlers_status._current_topology = "worker_only"
        append_wizard_checkpoint("topology_chosen")

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

            # Hit GET many times while the mutation lock is held.
            for _ in range(20):
                cap = StartResponseCapture()
                out = call_status(cap)
                assert status_code(cap) == 200, (
                    "GET must not 409 under setup_mutation_lock "
                    "contention"
                )
                body = json.loads(out)
                assert body["topology"] == "worker_only"
                assert body["checkpoints"] == ["topology_chosen"]
        finally:
            release_holder.set()
            t.join(timeout=5)

    def test_get_returns_atomic_snapshot_under_writers(self, mocker):
        """Concurrent writers mutating checkpoints + readers hitting
        GET must never produce a torn snapshot within a single
        response (``_state_lock`` guarantees each GET's
        ``topology``/``checkpoints`` pair is read under a single
        critical section).

        The writer appends a sequence of checkpoint names.  Readers
        poll GET.  Each observed snapshot must be a prefix of the
        writer's append sequence -- i.e., the list is monotonic and
        never contains a name out of order.  A torn read would be
        detectable as a non-prefix list.
        """
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

        expected_seq = [f"cp_{i}" for i in range(30)]
        writer_done = threading.Event()
        reader_errors: list[str] = []

        def writer():
            for name in expected_seq:
                append_wizard_checkpoint(name)
            writer_done.set()

        def reader():
            while not writer_done.is_set():
                cap = StartResponseCapture()
                out = call_status(cap)
                if status_code(cap) != 200:
                    reader_errors.append(f"non-200: {cap.status}")
                    return
                cps = json.loads(out)["checkpoints"]
                # Every observed list must be a prefix of expected.
                if cps != expected_seq[:len(cps)]:
                    reader_errors.append(
                        f"torn snapshot: {cps}",
                    )
                    return

        readers = [threading.Thread(target=reader) for _ in range(4)]
        w = threading.Thread(target=writer)
        for r in readers:
            r.start()
        w.start()
        w.join(timeout=5)
        for r in readers:
            r.join(timeout=5)

        assert not reader_errors, reader_errors
        # Post-condition: writer completed, all checkpoints present.
        assert get_current_checkpoints() == expected_seq
