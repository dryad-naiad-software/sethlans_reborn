# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``peer_cert`` (setup-rebuild FR-WK-8 certificate check).

The TLS handshake is faked: urllib3's ``HTTPSConnection.connect`` is
replaced by one that sets a fake socket. A request against a real TLS server
belongs to the integration tests.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import requests.adapters
import urllib3.poolmanager
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

from sethlans_worker_agent import peer_cert

DER = b"0\x82fake-der-certificate"


@pytest.mark.parametrize("sock_der, expected", [(DER, DER), (None, None)], ids=["socket", "no-socket"])
def test_connection_keeps_the_certificate_seen_at_connect_time(mocker, sock_der, expected):
    """FR-WK-8: the DER is read right after the handshake; a reconnect without a socket clears an old one."""
    sock = None
    if sock_der is not None:
        sock = MagicMock()
        sock.getpeercert.return_value = sock_der
    mocker.patch.object(HTTPSConnection, "connect", autospec=True, side_effect=lambda self: setattr(self, "sock", sock))
    conn = peer_cert._PeerCertHTTPSConnection("manager.test", 8080)
    conn.peer_cert_der = b"stale"
    conn.connect()
    assert conn.peer_cert_der == expected
    if sock is not None:
        sock.getpeercert.assert_called_once_with(binary_form=True)


@pytest.mark.parametrize("connection, expected", [
    (SimpleNamespace(peer_cert_der=DER), DER),
    (SimpleNamespace(peer_cert_der=b""), None),
    (None, None),
], ids=["captured", "empty", "no-connection"])
def test_response_carries_the_certificate_of_its_own_connection(mocker, connection, expected):
    """FR-WK-8: ``build_response`` copies the DER of the connection that carried the answer, else None."""
    mocker.patch.object(requests.adapters.HTTPAdapter, "build_response", return_value=requests.Response())
    response = peer_cert.PeerCertAdapter().build_response(MagicMock(), SimpleNamespace(connection=connection))
    assert response.peer_cert_der == expected


def test_https_pools_and_proxy_pools_use_the_capturing_connection():
    """FR-WK-8: direct and proxied HTTPS pools capture; the process-wide urllib3 default is untouched."""
    adapter = peer_cert.PeerCertAdapter()
    proxied = adapter.proxy_manager_for("http://proxy.test:3128")
    assert adapter.poolmanager.pool_classes_by_scheme["https"] is peer_cert._PeerCertHTTPSConnectionPool
    assert proxied.pool_classes_by_scheme["https"] is peer_cert._PeerCertHTTPSConnectionPool
    assert peer_cert._PeerCertHTTPSConnectionPool.ConnectionCls is peer_cert._PeerCertHTTPSConnection
    assert urllib3.poolmanager.pool_classes_by_scheme["https"] is HTTPSConnectionPool


def test_other_pool_classes_are_left_alone():
    """FR-WK-8: an unknown pool class (e.g. SOCKS) is not replaced, so it carries no certificate and enroll fails."""
    other = type("OtherPool", (), {})
    manager = SimpleNamespace(pool_classes_by_scheme={"https": other})
    assert peer_cert._use_capturing_pools(manager).pool_classes_by_scheme["https"] is other


@pytest.mark.parametrize("der, expected", [
    (DER, hashlib.sha256(DER).hexdigest()),
    (b"", None),
    (None, None),
], ids=["der", "empty", "missing"])
def test_peer_cert_fingerprint(der, expected):
    """FR-WK-8: lowercase hex SHA-256 of the DER, the manager's fingerprint format; None when nothing was seen."""
    response = SimpleNamespace() if der is None else SimpleNamespace(peer_cert_der=der)
    assert peer_cert.peer_cert_fingerprint(response) == expected
