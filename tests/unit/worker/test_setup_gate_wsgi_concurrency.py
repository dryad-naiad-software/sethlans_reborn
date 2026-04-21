# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Concurrency unit tests for setup_gate_wrapper_wsgi.

Includes transitional async def fixtures for the gate's asyncio.run adapter path. Deleted in Phase 7 with
gate_async_adapter.py.

Split from ``test_setup_gate_wsgi.py`` to keep each module under
the 300-line ceiling.  Covers:

* ``functools.partial`` routing through the async branch (M1).
* Documented behavior of ``_make_capture_send`` for duplicate
  ``http.response.start`` and body-before-start (H1).
* Lock-free concurrent reads of ``_setup_complete`` during the
  writer flip (M2) -- establishes the pattern for Phase 4's FR-18
  additional locked globals.
"""

import functools
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from sethlans_worker_agent.web_ui.setup import gate
from sethlans_worker_agent.web_ui.setup.gate import (
    mark_setup_complete,
    setup_gate_wrapper_wsgi,
)


# --- Minimal WSGI helpers (local copies; see test_setup_gate_wsgi) ---

def _make_environ(path: str = '/dashboard') -> Dict[str, Any]:
    return {
        'REQUEST_METHOD': 'GET',
        'PATH_INFO': path,
        'QUERY_STRING': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8080',
        'wsgi.url_scheme': 'http',
        'wsgi.input': io.BytesIO(b''),
    }


class _Recorder:
    def __init__(self) -> None:
        self.status: str = ''
        self.headers: List[Tuple[str, str]] = []

    def __call__(self, status: str, headers) -> None:
        self.status = status
        self.headers = list(headers)

    @property
    def status_code(self) -> int:
        return int(self.status.split(' ', 1)[0])


def _drain(iterable) -> bytes:
    return b''.join(iterable)


# --- M1: functools.partial unwrapping ---

class TestPartialWrappedAsyncInnerRouting:
    def test_partial_wrapped_async_inner_routes_through_async_branch(self):
        """An async callable wrapped in ``functools.partial`` must be
        recognized as async so it is driven via the asyncio adapter,
        not called as a sync WSGI callable (which would silently
        return an unawaited coroutine).
        """
        mark_setup_complete()
        calls: List[Tuple[str, Dict[str, Any]]] = []

        async def handler(extra: str, scope, receive, send):
            calls.append((extra, scope))
            await send({
                'type': 'http.response.start', 'status': 200,
                'headers': [(b'content-type', b'text/plain')],
            })
            await send({
                'type': 'http.response.body', 'body': b'ok',
                'more_body': False,
            })

        inner = functools.partial(handler, 'bound-arg')
        rec = _Recorder()
        body = _drain(setup_gate_wrapper_wsgi(
            _make_environ('/dashboard'), rec, inner,
        ))

        # If partial had been routed as sync, ``inner(environ,
        # start_response)`` would have returned an un-awaited
        # coroutine and calls would be empty.  An async-branch
        # routing invokes the handler and records the call.
        assert calls, (
            'partial-wrapped async inner was not driven via the '
            'async adapter'
        )
        assert calls[0][0] == 'bound-arg'
        assert calls[0][1]['path'] == '/dashboard'
        assert rec.status_code == 200
        assert body == b'ok'

    def test_nested_partial_still_unwrapped(self):
        """``partial(partial(async_fn, a), b)`` also routes async."""
        mark_setup_complete()
        seen: List[Tuple[str, str]] = []

        async def handler(a, b, scope, receive, send):
            seen.append((a, b))
            await send({
                'type': 'http.response.start', 'status': 204,
                'headers': [],
            })
            await send({
                'type': 'http.response.body', 'body': b'',
                'more_body': False,
            })

        inner = functools.partial(functools.partial(handler, 'A'), 'B')
        rec = _Recorder()
        _drain(setup_gate_wrapper_wsgi(_make_environ(), rec, inner))
        assert seen == [('A', 'B')]
        assert rec.status_code == 204


# --- H1: _make_capture_send ordering contract ---

class TestCaptureSendOrderingContract:
    def test_body_before_start_yields_500(self):
        """Body-before-start is a protocol violation: no start ever
        arrives, so the WSGI side falls through the "inner never
        responded" 500 path.
        """
        mark_setup_complete()

        async def inner(scope, receive, send):
            await send({
                'type': 'http.response.body', 'body': b'oops',
                'more_body': False,
            })

        rec = _Recorder()
        body = _drain(setup_gate_wrapper_wsgi(
            _make_environ(), rec, inner,
        ))
        assert rec.status_code == 500
        assert body == b''

    def test_duplicate_http_response_start_first_status_wins(self):
        """Two ``http.response.start`` events: the WSGI response uses
        the *first* status (``status_out[0]`` in the adapter).  The
        adapter intentionally does not reject the duplicate -- the
        contract is documented in ``_make_capture_send``'s docstring.
        """
        mark_setup_complete()

        async def inner(scope, receive, send):
            await send({
                'type': 'http.response.start', 'status': 201,
                'headers': [(b'x-first', b'yes')],
            })
            await send({
                'type': 'http.response.start', 'status': 500,
                'headers': [(b'x-second', b'yes')],
            })
            await send({
                'type': 'http.response.body', 'body': b'body',
                'more_body': False,
            })

        rec = _Recorder()
        body = _drain(setup_gate_wrapper_wsgi(
            _make_environ(), rec, inner,
        ))
        # First status wins.
        assert rec.status_code == 201
        # Headers from both starts are concatenated in arrival order
        # (documented behavior of list.extend on successive starts).
        header_names = {k.lower() for k, _ in rec.headers}
        assert 'x-first' in header_names
        assert 'x-second' in header_names
        assert body == b'body'


# --- M2: concurrent readers during mark_setup_complete writer ---

class TestConcurrentReadsDuringGateFlip:
    def test_concurrent_reads_during_mark_setup_complete(self):
        """N reader threads call ``setup_gate_wrapper_wsgi`` while a
        writer thread flips the gate mid-run.  Every response must be
        well-formed: either 200 (post-flip, passed through to inner)
        or 503 (pre-flip, blocked by gate).  No torn/empty responses.

        Establishes the locking-invariant pattern that FR-18 (Phase
        4) will extend to additional globals.
        """
        assert gate._setup_complete is False
        n_readers = 16

        def inner(environ, start_response):
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return [b'open']

        barrier = threading.Barrier(n_readers + 1)

        def reader() -> int:
            barrier.wait()
            rec = _Recorder()
            body = _drain(setup_gate_wrapper_wsgi(
                _make_environ('/dashboard'), rec, inner,
            ))
            code = rec.status_code
            # Contract: 200 + "open", or 503 + blocked JSON body.
            if code == 200:
                assert body == b'open'
            elif code == 503:
                assert b'Setup not complete.' in body
            else:
                raise AssertionError(
                    f'unexpected status {code} with body {body!r}'
                )
            return code

        def writer() -> None:
            barrier.wait()
            mark_setup_complete()

        try:
            with ThreadPoolExecutor(max_workers=n_readers + 1) as pool:
                reader_futures = [
                    pool.submit(reader) for _ in range(n_readers)
                ]
                writer_future = pool.submit(writer)
                codes = [f.result(timeout=10) for f in as_completed(
                    reader_futures,
                )]
                writer_future.result(timeout=10)
        finally:
            gate._setup_complete = False

        # All readers returned a documented status.  We do not assert
        # the exact mix of 200/503 -- thread scheduling is free to
        # pick either side of the flip for any given reader.
        assert len(codes) == n_readers
        assert all(c in (200, 503) for c in codes)
