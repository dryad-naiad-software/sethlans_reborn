# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The TLS live server of the enrollment tests (``_tls_manager``) never waits without a deadline (#343).

Real parts: ``serve`` with Django's live server thread on the test database,
and a raw TCP peer on 127.0.0.1 that connects and never starts TLS. The name
lookup the stdlib server would do is replaced by a stub, so no test depends
on real DNS.
"""
import http.client
import socket
import ssl
import time
import urllib.parse

import pytest

from ._tls_manager import CONNECTION_TIMEOUT, serve
from .conftest import TEST_ENROLLMENT_KEY_CANONICAL

pytestmark = pytest.mark.timeout(60, method="thread")


def _get(port: int, path: str) -> int:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the test certificate is self-signed; only the answer matters here
    conn = http.client.HTTPSConnection('127.0.0.1', port, timeout=10, context=context)
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def test_a_silent_peer_blocks_neither_other_clients_nor_the_stop(transactional_db, monkeypatch, tmp_path):
    """#343: the TLS handshake ran inside ``accept()`` in the serve loop with no timeout, so one peer
    that connected and never spoke blocked every other client and ``shutdown()`` for ever. Now
    another client is answered meanwhile, and leaving ``serve`` with the peer still connected
    returns without waiting out its ``CONNECTION_TIMEOUT``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as silent:
        with serve(tmp_path, monkeypatch, TEST_ENROLLMENT_KEY_CANONICAL) as manager:
            port = urllib.parse.urlsplit(manager.base_url).port
            silent.settimeout(5)
            silent.connect(('127.0.0.1', port))

            assert _get(port, '/api/health/') == 200
            started = time.monotonic()
        assert time.monotonic() - started < CONNECTION_TIMEOUT


def test_the_start_does_no_reverse_name_lookup(transactional_db, monkeypatch, tmp_path):
    """#343: ``HTTPServer.server_bind`` looked up ``socket.getfqdn('127.0.0.1')``, which held the first
    start on a macOS runner past ``READY_TIMEOUT``; the server now starts and answers without it."""
    looked_up = []

    def no_lookup(name=''):
        looked_up.append(name)
        raise AssertionError('socket.getfqdn must not be called')

    monkeypatch.setattr(socket, 'getfqdn', no_lookup)
    with serve(tmp_path, monkeypatch, TEST_ENROLLMENT_KEY_CANONICAL) as manager:
        assert _get(urllib.parse.urlsplit(manager.base_url).port, '/api/health/') == 200
    assert looked_up == []
