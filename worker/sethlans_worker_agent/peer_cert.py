# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Capture the certificate the server presented on the connection that
carried a request (setup-rebuild FR-WK-8, certificate check).

Enrollment has no pinned fingerprint yet, so it runs with ``verify=False``.
The signed enroll answer names the manager's certificate fingerprint; the
worker compares it with the certificate it actually saw, so a relay that
forwards the request to the real manager over its own TLS connection is
caught.

How the certificate is captured:
  * :class:`_PeerCertHTTPSConnection` reads the peer certificate (DER,
    ``getpeercert(binary_form=True)``) right after its TLS handshake in
    ``connect()``. It is read at connect time, not from the response,
    because ``http.client`` closes the connection's socket inside
    ``getresponse()`` when the server answers ``Connection: close``.
  * :class:`PeerCertAdapter` makes the pool manager build its HTTPS pools
    with that connection class, and in ``build_response`` copies the
    certificate of the urllib3 connection that carried this response
    (``HTTPResponse.connection``; requests reads the body later, so the
    connection is still attached) onto the ``requests.Response`` as
    ``peer_cert_der``.

The certificate therefore always belongs to the same TLS connection that
sent the request and received the answer, never to a second connection.
When it cannot be read (an unusual proxy type, a closed socket),
``peer_cert_der`` is ``None`` and the caller fails loudly.
"""

import hashlib
from typing import Optional

import requests
import requests.adapters
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

PEER_CERT_ATTR = "peer_cert_der"


class _PeerCertHTTPSConnection(HTTPSConnection):
    """An HTTPS connection that keeps the DER certificate of its peer."""

    peer_cert_der: Optional[bytes] = None

    def connect(self) -> None:
        self.peer_cert_der = None
        super().connect()
        sock = self.sock
        if sock is not None:
            self.peer_cert_der = sock.getpeercert(binary_form=True)


class _PeerCertHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PeerCertHTTPSConnection


def _use_capturing_pools(manager):
    """Build ``manager``'s HTTPS pools with the capturing connection class.

    Only the stock HTTPS pool class is replaced. Any other (for example a
    SOCKS proxy manager's) is left alone, so its responses carry no
    certificate and the enrollment fails loudly instead of skipping the
    check.
    """
    classes = dict(manager.pool_classes_by_scheme)
    if classes.get("https") is HTTPSConnectionPool:
        classes["https"] = _PeerCertHTTPSConnectionPool
        manager.pool_classes_by_scheme = classes
    return manager


class PeerCertAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter whose responses carry the peer certificate of their connection."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        _use_capturing_pools(self.poolmanager)

    def proxy_manager_for(self, proxy, **kwargs):
        return _use_capturing_pools(super().proxy_manager_for(proxy, **kwargs))

    def build_response(self, req, resp):
        response = super().build_response(req, resp)
        connection = getattr(resp, "connection", None)
        der = getattr(connection, PEER_CERT_ATTR, None)
        setattr(response, PEER_CERT_ATTR, der if isinstance(der, bytes) and der else None)
        return response


def peer_cert_fingerprint(response: requests.Response) -> Optional[str]:
    """Lowercase hex SHA-256 of the DER certificate seen on ``response``'s connection, or ``None``.

    The same format as the manager's ``get_cert_fingerprint`` (SHA-256 over
    the DER bytes, lowercase hex, no separators).
    """
    der = getattr(response, PEER_CERT_ATTR, None)
    if not isinstance(der, bytes) or not der:
        return None
    return hashlib.sha256(der).hexdigest()
