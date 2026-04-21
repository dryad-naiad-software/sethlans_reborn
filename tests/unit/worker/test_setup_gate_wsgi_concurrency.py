# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Concurrency unit tests for ``setup_gate_wrapper_wsgi``.

Split from ``test_setup_gate_wsgi.py`` to keep each module under
the 300-line ceiling. Covers lock-free concurrent reads of
``_setup_complete`` during the writer flip -- establishes the
pattern for additional locked globals.
"""

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


# --- Concurrent readers during mark_setup_complete writer ---

class TestConcurrentReadsDuringGateFlip:
    def test_concurrent_reads_during_mark_setup_complete(self):
        """N reader threads call ``setup_gate_wrapper_wsgi`` while a
        writer thread flips the gate mid-run. Every response must be
        well-formed: either 200 (post-flip, passed through to inner)
        or 503 (pre-flip, blocked by gate). No torn/empty responses.
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

        # All readers returned a documented status. We do not assert
        # the exact mix of 200/503 -- thread scheduling is free to
        # pick either side of the flip for any given reader.
        assert len(codes) == n_readers
        assert all(c in (200, 503) for c in codes)
