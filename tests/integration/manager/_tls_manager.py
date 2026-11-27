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

The ``tls_manager`` fixture in ``conftest.py`` wraps it. Module name starts
with an underscore so pytest does not collect it.
"""
import contextlib
import gc
import io
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


class _TrackingThreadedWSGIServer(ThreadedWSGIServer):
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

    server_class = _TrackingThreadedWSGIServer
    ssl_context = None
    keep_alive = True

    def _create_server(self, connections_override=None):
        if self.keep_alive:
            server = super()._create_server(connections_override=connections_override)
        else:
            server = WSGIServer((self.host, self.port), QuietWSGIRequestHandler, allow_reuse_address=False)
        server.socket = self.ssl_context.wrap_socket(server.socket, server_side=True)
        return server


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
        assert thread.is_ready.wait(READY_TIMEOUT) and thread.error is None, thread.error
        yield TlsManager(f'https://127.0.0.1:{thread.port}/api/', fingerprint, bodies)
    finally:
        thread.terminate()
        # A finished ``requests`` response keeps its keep-alive connection open until it is
        # freed; a spy's ``spy_return`` sits in reference cycles, so collect them first.
        gc.collect()
        request_threads = getattr(getattr(thread, 'httpd', None), 'request_threads', [])
        for request_thread in request_threads:
            request_thread.join(READY_TIMEOUT)
        for conn in shared.values():
            conn.dec_thread_sharing()
        alive = [request_thread.name for request_thread in request_threads if request_thread.is_alive()]
        assert not alive, f'request threads still running after {READY_TIMEOUT} s: {alive}'
