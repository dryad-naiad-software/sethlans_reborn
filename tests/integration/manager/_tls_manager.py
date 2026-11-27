# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A live manager over TLS on 127.0.0.1 for the enrollment tests (setup-rebuild FR-WK-8, FR-WK-1).

``serve(...)`` runs Django's live server thread with its listening socket
wrapped in TLS (a fresh self-signed certificate), on the test database (the
in-memory SQLite connection is shared with the server thread). The manager's
``runtime_state`` (its id and the certificate fingerprint it signs) and its
enrollment key row are set directly; they lie outside the parts under test.
Every POST body that reaches the manager is kept, unchanged, in
``TlsManager.bodies`` as ``(path, raw bytes)``, so a test can check what went
over the wire even when the client is another process.

Nothing here waits without a deadline (#343): the TLS handshake runs per
connection with ``CONNECTION_TIMEOUT``, never inside ``accept()`` in the
serve loop (where one silent peer blocked the loop, and so ``shutdown()``,
for ever); binding does no reverse DNS lookup (``HTTPServer.server_bind``
calls ``socket.getfqdn``, which took longer than ``READY_TIMEOUT`` on a
macOS runner); a start slower than ``READY_TIMEOUT`` fails the test and
the thread then closes its server itself instead of serving; stopping
shuts every open connection down, waits at most ``STOP_TIMEOUT`` for the
serve loop and for the thread, then fails the test.

The ``tls_manager`` fixture in ``conftest.py`` wraps it. Module name starts
with an underscore so pytest does not collect it.
"""
import contextlib
import gc
import io
import os
import socket
import socketserver
import ssl
import threading
from typing import NamedTuple

from django.core.servers.basehttp import ThreadedWSGIServer, WSGIServer
from django.db import connections
from django.test.testcases import LiveServerThread, QuietWSGIRequestHandler, _StaticFilesHandler

from sethlans_manager import runtime_state
from tests.unit.worker._tls_loopback import _self_signed
from workers.models import ManagerSettings

MANAGER_ID = '11111111-2222-3333-4444-555555555555'
READY_TIMEOUT = 10
CONNECTION_TIMEOUT = 10  # s: a connection silent this long (handshake or next request) is dropped
STOP_TIMEOUT = 15        # s: the longest terminate() waits for the serve loop, then for the thread
# What a connection shut down by ``abort_connections`` at stop may raise on its request thread
# (``SSLSocket`` drops its ``_sslobj`` from the aborting thread): it ends the connection quietly.
_ENDED = (OSError, ValueError, AttributeError)


class TlsManager(NamedTuple):
    base_url: str      # https://127.0.0.1:<port>/api/
    fingerprint: str   # SHA-256 hex of the certificate the manager presents and signs
    bodies: list       # (path, raw body) of every POST the manager received, in order


class _BodyRecorder:
    """WSGI wrapper: keeps the raw body of each POST, then hands the request on unchanged."""

    def __init__(self, app, bodies):
        self._app = app
        self._bodies = bodies

    def __call__(self, environ, start_response):
        if environ.get('REQUEST_METHOD') == 'POST':
            body = environ['wsgi.input'].read(int(environ.get('CONTENT_LENGTH') or 0))
            self._bodies.append((environ.get('PATH_INFO', ''), body))
            environ['wsgi.input'] = io.BytesIO(body)
        return self._app(environ, start_response)


class _Handler(QuietWSGIRequestHandler):
    """Django's quiet handler; a connection idle past ``CONNECTION_TIMEOUT`` or ended at stop is closed."""

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except _ENDED:
            self.close_connection = True


class _HandshakeOnConnection:
    """Server mixin: each connection's TLS handshake runs in ``finish_request`` (#343).

    The listening socket is wrapped with ``do_handshake_on_connect=False``,
    so ``accept()`` returns at once; the handshake then runs on the request
    thread (threaded server) within the connection's ``CONNECTION_TIMEOUT``.
    A peer that leaves or never speaks is dropped quietly, as a failed
    handshake in ``accept()`` was. Every accepted connection is listed until
    it is closed, so :meth:`abort_connections` can shut them all down and no
    request thread outlives the stop.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._open_lock = threading.Lock()
        self._open = set()

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(CONNECTION_TIMEOUT)
        with self._open_lock:
            self._open.add(request)
        return request, client_address

    def close_request(self, request):
        with self._open_lock:
            self._open.discard(request)
        super().close_request(request)

    def finish_request(self, request, client_address):
        try:
            request.do_handshake()
        except _ENDED:
            return
        super().finish_request(request, client_address)

    def abort_connections(self) -> None:
        """Wake every request thread still reading: ``shutdown`` does on POSIX, only ``close`` does on Windows."""
        with self._open_lock:
            still_open = list(self._open)
        for request in still_open:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if os.name == 'nt':
                request.close()


class _BindWithoutLookup:
    """Server mixin: ``server_bind`` without ``socket.getfqdn`` (#343).

    ``http.server.HTTPServer.server_bind`` sets ``server_name`` from
    ``socket.getfqdn(host)``, a reverse DNS lookup of 127.0.0.1 that took
    more than 10 s on a macOS runner and held the live server's start.
    This is wsgiref's ``WSGIServer.server_bind`` with the host as the name.
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
        self.setup_environ()


class _TlsWSGIServer(_BindWithoutLookup, _HandshakeOnConnection, WSGIServer):
    """The single-threaded server (``keep_alive`` off)."""


class _TrackingThreadedWSGIServer(_BindWithoutLookup, _HandshakeOnConnection, ThreadedWSGIServer):
    """Django's threaded server, remembering its request threads so ``serve`` can wait for them.

    Django's own daemon request threads are waited for by nobody: one still
    closing its database connection after the fixture stopped sharing the
    in-memory connection raised a thread exception in a later test.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_threads = []

    def process_request(self, request, client_address):
        thread = threading.Thread(target=self.process_request_thread, args=(request, client_address), daemon=True)
        self.request_threads.append(thread)
        thread.start()


class _TlsLiveServerThread(LiveServerThread):
    """Django's live server thread with its listening socket wrapped in TLS.

    With ``keep_alive`` off it serves with the single-threaded ``WSGIServer``,
    for which Django's handler answers every request with ``Connection: close``.
    """

    ssl_context = None
    keep_alive = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopping = threading.Event()  # set by terminate(); a start finishing after it serves nothing

    def _create_server(self, connections_override=None):
        if self.keep_alive:
            server = _TrackingThreadedWSGIServer((self.host, self.port), _Handler, allow_reuse_address=False,
                                                 connections_override=connections_override)
        else:
            server = _TlsWSGIServer((self.host, self.port), _Handler, allow_reuse_address=False)
        server.socket = self.ssl_context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        if self.stopping.is_set():  # the test gave up on this start: close instead of serving with nobody to stop it
            server.server_close()
            raise RuntimeError('the TLS manager was stopped before it started')
        return server

    def terminate(self):
        """Django's ``terminate`` with deadlines (#343): a stuck serve loop or thread fails the test.

        ``shutdown()`` has no timeout of its own, so it runs on a helper thread;
        the open connections are shut down meanwhile (the single-threaded
        server's loop may be in one) and again once the loop has stopped. A
        thread still starting is told to close its server when it gets there.
        """
        self.stopping.set()
        if not self.is_ready.wait(STOP_TIMEOUT):
            raise AssertionError(f'the TLS manager did not start within {READY_TIMEOUT + STOP_TIMEOUT} s; '
                                 'its thread closes the server itself if it ever gets there')
        httpd = getattr(self, 'httpd', None)
        if httpd is not None:
            stopper = threading.Thread(target=httpd.shutdown, name='tls-manager-shutdown', daemon=True)
            stopper.start()
            httpd.abort_connections()
            stopper.join(STOP_TIMEOUT)
            if stopper.is_alive():
                raise AssertionError(f'the TLS manager serve loop did not stop within {STOP_TIMEOUT} s')
            httpd.abort_connections()
            httpd.server_close()
        self.join(STOP_TIMEOUT)
        if self.is_alive():
            raise AssertionError(f'the TLS manager thread did not end within {STOP_TIMEOUT} s')


def _stop(thread, shared) -> None:
    """End the server, wait for its request threads, stop sharing the in-memory connections."""
    try:
        thread.terminate()
    finally:
        # A finished ``requests`` response keeps its keep-alive connection open until it is
        # freed; a spy's ``spy_return`` sits in reference cycles, so collect them first.
        gc.collect()
        request_threads = getattr(getattr(thread, 'httpd', None), 'request_threads', [])
        for request_thread in request_threads:
            request_thread.join(READY_TIMEOUT)
        if not thread.is_alive():  # a thread still starting keeps the shared connections it was given
            for conn in shared.values():
                conn.dec_thread_sharing()
        alive = [request_thread.name for request_thread in request_threads if request_thread.is_alive()]
        assert not alive, f'request threads still running after {READY_TIMEOUT} s: {alive}'


@contextlib.contextmanager
def serve(folder, monkeypatch, enrollment_key, keep_alive=True):
    """A running TLS manager whose enrollment key is ``enrollment_key``; yields a ``TlsManager``."""
    cert_path, key_path, fingerprint = _self_signed(folder)
    ManagerSettings.objects.update_or_create(pk=1, defaults={'manager_id': MANAGER_ID,
                                                             'enrollment_key': enrollment_key})
    monkeypatch.setattr(runtime_state, 'manager_id', MANAGER_ID)
    monkeypatch.setattr(runtime_state, 'cert_fingerprint', fingerprint)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    shared = {alias: connections[alias] for alias in connections
              if connections[alias].vendor == 'sqlite' and connections[alias].is_in_memory_db()}
    for conn in shared.values():
        conn.inc_thread_sharing()
    bodies = []
    thread = _TlsLiveServerThread('127.0.0.1', lambda app: _BodyRecorder(_StaticFilesHandler(app), bodies),
                                  connections_override=shared)
    thread.ssl_context = context
    thread.keep_alive = keep_alive
    thread.daemon = True
    thread.start()
    try:
        if not thread.is_ready.wait(READY_TIMEOUT):
            raise AssertionError(f'the TLS manager did not start within {READY_TIMEOUT} s')
        assert thread.error is None, thread.error
        yield TlsManager(f'https://127.0.0.1:{thread.port}/api/', fingerprint, bodies)
    finally:
        _stop(thread, shared)
