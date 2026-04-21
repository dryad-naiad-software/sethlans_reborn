# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Concurrent-thread tests for ``handlers_discovery``.

Companion to ``test_setup_handlers_discovery.py``: exercises the
thread-safety invariants added in Phase 4f for the sync WSGI
rewrite.  Under the Waitress threadpool:

* ``handle_discover`` is lock-free w.r.t. ``setup_mutation_lock`` --
  a discover poll during an in-flight mutation must return 200,
  not 409 (matches the read-lock-free rule in ``setup.lock``).
* ``handle_select_manager`` is wrapped in ``setup_mutation_lock``.
  Two concurrent POSTs: exactly one gets 200, the other gets 409.
* The three ``_selected_manager_*`` module globals are written
  atomically under ``_state_lock``.  Concurrent POSTs cannot persist
  a torn (url, id, meta) triple -- the winner's full triple lands
  and losers persist nothing.
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    handle_discover,
    handle_select_manager,
    get_selected_manager_url,
    get_selected_manager_id,
    get_selected_manager_meta,
)
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture


def _make_environ(method: str, path: str, body: bytes = b'') -> dict:
    env = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


def _call(handler, environ, cap):
    return b''.join(handler(environ, cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


class TestDiscoverLockFree:
    def test_discover_not_blocked_by_setup_mutation_lock(self, mocker):
        """GET /discover/ must not 409 while setup_mutation_lock is
        held by an in-flight POST (readers are lock-free per the
        ``setup.lock`` module docstring -- otherwise the wizard poll
        loop would spinner-storm).
        """
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_discovery"
            "._run_discovery",
            return_value=[],
        )

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
            for _ in range(5):
                cap = StartResponseCapture()
                out = _call(
                    handle_discover,
                    _make_environ('GET', '/api/setup/discover/'),
                    cap,
                )
                assert _status_code(cap) == 200, (
                    "GET discover must be lock-free w.r.t. "
                    "setup_mutation_lock"
                )
                assert json.loads(out)["managers"] == []
        finally:
            release_holder.set()
            t.join(timeout=5)


class TestSelectManagerContention:
    def test_contention_returns_409(self):
        """Hold setup_mutation_lock in a background thread, then
        POST /select-manager/ -- must 409 with the canonical error.
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

            body = json.dumps({
                "manager_url": "https://10.0.0.1:8080/api/",
            }).encode()
            cap = StartResponseCapture()
            out = _call(
                handle_select_manager,
                _make_environ(
                    'POST',
                    '/api/setup/worker/select-manager/',
                    body,
                ),
                cap,
            )

            assert _status_code(cap) == 409
            payload = json.loads(out)
            assert payload == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
        finally:
            release_holder.set()
            t.join(timeout=5)


class TestSelectManagerAtomicity:
    def test_concurrent_posts_persist_one_winner_consistently(self):
        """N threads concurrently POST distinct selections.

        Because ``setup_mutation_lock`` is non-blocking, at most one
        thread observes ``acquired=True`` and emits 200; the rest
        observe ``acquired=False`` and emit 409.  That's the
        *runtime* shape -- timing may mean consecutive (not
        concurrent) acquirers each win their own critical section.
        The invariant this test pins is stronger: whatever the 200
        count is, the *persisted* (url, id, meta) triple must come
        from ONE winner's request body -- never a mix of two
        winners' fields.  A torn write (url from A, meta from B)
        would fail the final equality check.

        To maximize contention we synchronize all N threads on a
        starting-gate Event before they race into the handler.
        """
        N = 12
        start_gate = threading.Event()
        results: list[tuple[int, int]] = []  # (thread_idx, status)
        results_lock = threading.Lock()

        def make_body(i: int) -> bytes:
            # Each thread builds a fully-distinct selection payload
            # -- url, id, AND meta marker all differ so a torn
            # persist would leave (url from A, meta['idx']=B) in
            # module state.
            return json.dumps({
                "manager_url": f"https://10.0.0.{i}:8080/api/",
                "manager_id": f"mid-{i}",
                "idx": i,
            }).encode()

        def worker(i: int):
            start_gate.wait(timeout=5)
            cap = StartResponseCapture()
            _call(
                handle_select_manager,
                _make_environ(
                    'POST',
                    '/api/setup/worker/select-manager/',
                    make_body(i),
                ),
                cap,
            )
            with results_lock:
                results.append((i, _status_code(cap)))

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(N)
        ]
        for t in threads:
            t.start()
        start_gate.set()
        for t in threads:
            t.join(timeout=10)

        # Every thread returned either 200 or 409 -- nothing else.
        status_counts: dict[int, int] = {}
        for _, s in results:
            status_counts[s] = status_counts.get(s, 0) + 1
        assert set(status_counts.keys()).issubset({200, 409}), (
            f"unexpected statuses: {status_counts}"
        )
        # At least one 200 (otherwise no one persisted anything and
        # the invariant we're testing is moot) and the totals sum
        # to N.
        assert status_counts.get(200, 0) >= 1
        assert sum(status_counts.values()) == N

        # The persisted triple must come from exactly ONE request
        # body -- atomic write under _state_lock.  We reverse the
        # URL back to its idx, then check every field matches.
        persisted_url = get_selected_manager_url()
        persisted_id = get_selected_manager_id()
        persisted_meta = get_selected_manager_meta()
        assert persisted_url is not None
        assert persisted_meta is not None

        # Recover which idx (0..N-1) won.  Each idx produces a
        # uniquely-parseable URL; a torn write would pair an idx-A
        # URL with an idx-B manager_id or meta['idx'].
        winner_idx = None
        for i in range(N):
            if persisted_url == f"https://10.0.0.{i}:8080/api/":
                winner_idx = i
                break
        assert winner_idx is not None, (
            f"persisted URL {persisted_url!r} matches no thread"
        )
        assert persisted_id == f"mid-{winner_idx}", (
            f"torn write: url=idx-{winner_idx} but id={persisted_id}"
        )
        assert persisted_meta.get("idx") == winner_idx, (
            f"torn write: url=idx-{winner_idx} but "
            f"meta.idx={persisted_meta.get('idx')}"
        )
        assert (
            persisted_meta.get("manager_url") == persisted_url
        ), "meta.manager_url diverges from url scalar"
        assert (
            persisted_meta.get("manager_id") == persisted_id
        ), "meta.manager_id diverges from id scalar"
