# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the per-path request body limits of the Waitress
listeners (issue #260, :mod:`sethlans_manager.waitress_body_limits`).

Each test starts a real Waitress server on a loopback port with the
manager's channel class and a WSGI app that records what it was given,
then talks raw HTTP/1.1 over a socket so it can stop sending part way
through a body. Covers:

- A ``Content-Length`` above the small limit on a JSON path gets 413
  from the headers alone: the client never sends the body and the app
  never runs.
- The same size on an upload path is received in full and reaches the
  app; the server's own ceiling still applies there.
- A chunked body above the small limit gets 413 and never reaches the
  app.
- A body of exactly the small limit is accepted.
- The internal-origin listener gives every path the small limit.
- ``Expect: 100-continue``: ``100 Continue`` then 413 on a small path,
  ``100`` then 200 on an upload path.
- Path variants (case, doubled slash, ``..``) get the small limit.
"""

from __future__ import annotations

import re
import socket
import threading

import pytest
import waitress
from waitress import wasyncore

from sethlans_manager.body_limits import SMALL_BODY_LIMIT
from sethlans_manager.waitress_body_limits import limit_request_bodies

LARGE = 1024 * 1024  # above the small limit, below the test ceiling
CEILING = 2 * 1024 * 1024  # the server's max_request_body_size here


class _Recorder:
    """WSGI app that records (path, body length) and answers 200."""

    def __init__(self):
        self.calls = []

    def __call__(self, environ, start_response):
        body = environ['wsgi.input'].read()
        self.calls.append((environ['PATH_INFO'], len(body)))
        start_response('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', '2')])
        return [b'ok']


def _stop(server, thread):
    """Close every dispatcher from inside the loop thread, then drain."""
    server.trigger.pull_trigger(lambda: wasyncore.close_all(server._map, ignore_all=True))
    thread.join(timeout=10)
    server.task_dispatcher.shutdown()
    assert not thread.is_alive()


@pytest.fixture
def serve():
    """Factory: start a manager-style Waitress listener, return (port, app)."""
    started = []

    def _start(*, allow_uploads=True):
        app = _Recorder()
        server = waitress.create_server(
            app, host='127.0.0.1', port=0, threads=2,
            max_request_body_size=CEILING, ident='body-limit-test',
        )
        limit_request_bodies(server, allow_uploads=allow_uploads)
        thread = threading.Thread(target=server.run, name='body-limit-waitress', daemon=True)
        thread.start()
        started.append((server, thread))
        return server.effective_port, app

    yield _start
    for server, thread in started:
        _stop(server, thread)


def _head(port, path, extra):
    return (
        f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
        f'Content-Type: application/json\r\nConnection: close\r\n{extra}\r\n'
    ).encode()


def _read_all(sock):
    data = b''
    while True:
        try:
            chunk = sock.recv(65536)
        except (ConnectionResetError, ConnectionAbortedError):
            break
        if not chunk:
            break
        data += chunk
    return data


def _statuses(response):
    """All status codes in the response stream (``100`` first when sent)."""
    return [int(code) for code in re.findall(rb'HTTP/1\.1 (\d{3}) ', response)]


def _status(response):
    """The final status code of the response stream."""
    codes = _statuses(response)
    return codes[-1] if codes else None


def _post_with_length(port, path, declared, send_bytes):
    """Declare *declared* bytes, send only *send_bytes* of them, read the answer."""
    with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
        sock.sendall(_head(port, path, f'Content-Length: {declared}\r\n') + b'x' * send_bytes)
        return _status(_read_all(sock))


def _post_streamed(port, path, total, *, chunked, extra='', chunk=16 * 1024):
    """Send a body of *total* bytes in pieces while a reader waits for the answer.

    The sender stops once the server closed the connection, so the
    server is never forced to read the whole body. Returns the raw
    response stream.
    """
    framing = 'Transfer-Encoding: chunked\r\n' if chunked else f'Content-Length: {total}\r\n'
    piece = b'x' * chunk
    if chunked:
        piece = f'{chunk:x}\r\n'.encode() + piece + b'\r\n'
    with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
        answer = []
        reader = threading.Thread(target=lambda: answer.append(_read_all(sock)), daemon=True)
        reader.start()
        try:
            sock.sendall(_head(port, path, framing + extra))
            sent = 0
            while sent < total and reader.is_alive():
                sock.sendall(piece)
                sent += chunk
            if chunked and reader.is_alive():
                sock.sendall(b'0\r\n\r\n')
        except OSError:
            pass  # the server answered and closed while we were sending
        reader.join(timeout=10)
        return answer[0] if answer else b''


def _post_chunked(port, path, total, extra=''):
    """Send a chunked body of *total* bytes; return the final status."""
    return _status(_post_streamed(port, path, total, chunked=True, extra=extra))


class TestSmallPaths:
    def test_large_content_length_refused_before_the_body(self, serve):
        port, app = serve()
        # Only 1 KiB of the declared 1 MiB is ever sent.
        assert _post_with_length(port, '/api/enroll/', LARGE, 1024) == 413
        assert app.calls == []

    def test_heartbeat_path_is_small(self, serve):
        port, app = serve()
        assert _post_with_length(port, '/api/heartbeat/3/', SMALL_BODY_LIMIT + 1, 0) == 413
        assert app.calls == []

    def test_body_of_exactly_the_limit_accepted(self, serve):
        port, app = serve()
        status = _post_with_length(port, '/api/heartbeat/', SMALL_BODY_LIMIT, SMALL_BODY_LIMIT)
        assert status == 200
        assert app.calls == [('/api/heartbeat/', SMALL_BODY_LIMIT)]

    def test_chunked_body_over_the_limit_refused(self, serve):
        port, app = serve()
        assert _post_chunked(port, '/api/enroll/', LARGE) == 413
        assert app.calls == []

    def test_chunked_body_under_the_limit_accepted(self, serve):
        port, app = serve()
        assert _post_chunked(port, '/api/heartbeat/', 64 * 1024) == 200
        assert app.calls == [('/api/heartbeat/', 64 * 1024)]


class TestUploadPaths:
    @pytest.mark.parametrize('path', ['/api/assets/', '/api/jobs/7/upload_output/'])
    def test_same_size_accepted_on_upload_path(self, serve, path):
        port, app = serve()
        assert _post_with_length(port, path, LARGE, LARGE) == 200
        assert app.calls == [(path, LARGE)]

    def test_chunked_upload_accepted(self, serve):
        port, app = serve()
        assert _post_chunked(port, '/api/assets/', LARGE) == 200
        assert app.calls == [('/api/assets/', LARGE)]

    def test_server_ceiling_still_applies_to_uploads(self, serve):
        port, app = serve()
        assert _post_with_length(port, '/api/assets/', CEILING + 1, 0) == 413
        assert app.calls == []


class TestInternalListener:
    def test_upload_path_is_small_on_the_internal_listener(self, serve):
        port, app = serve(allow_uploads=False)
        assert _post_with_length(port, '/api/assets/', LARGE, 1024) == 413
        assert app.calls == []

    def test_small_body_still_served(self, serve):
        port, app = serve(allow_uploads=False)
        assert _post_with_length(port, '/api/status/public/', 10, 10) == 200
        assert app.calls == [('/api/status/public/', 10)]


EXPECT = 'Expect: 100-continue\r\n'


class TestExpectContinue:
    """With ``Expect: 100-continue`` Waitress sends ``100 Continue`` even
    for an oversized ``Content-Length``, then reads the body only up to
    the path's limit and answers 413."""

    @pytest.mark.parametrize('chunked', [False, True])
    def test_over_limit_gets_100_then_413(self, serve, chunked):
        port, app = serve()
        response = _post_streamed(port, '/api/enroll/', LARGE, chunked=chunked, extra=EXPECT)
        assert _statuses(response) == [100, 413]
        assert app.calls == []

    def test_upload_path_accepted(self, serve):
        port, app = serve()
        response = _post_streamed(port, '/api/assets/', LARGE, chunked=False, extra=EXPECT)
        assert _statuses(response) == [100, 200]
        assert app.calls == [('/api/assets/', LARGE)]


class TestPathVariants:
    """Variants that are not upload paths for Django get the small limit."""

    @pytest.mark.parametrize('path', [
        '/API/ASSETS/',
        '//api/assets/x',
        '/api/enroll/../assets/',
        '/api/jobs/7/../../assets/',
    ])
    def test_variant_gets_small_limit(self, serve, path):
        port, app = serve()
        assert _post_with_length(port, path, LARGE, 1024) == 413
        assert app.calls == []

    def test_encoded_slash_counts_as_upload_path(self, serve):
        """``%2F`` decodes to ``/``: ``/api/assets/x`` is an upload path
        (Django sees the same decoded path and answers 404 for it)."""
        port, app = serve()
        assert _post_with_length(port, '/api/assets%2Fx', LARGE, LARGE) == 200
        assert app.calls == [('/api/assets/x', LARGE)]
