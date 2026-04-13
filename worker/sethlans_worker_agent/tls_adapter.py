# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
TLS certificate fingerprint pinning adapter for the worker agent.

Provides a custom ``requests.HTTPAdapter`` that verifies the manager's
TLS certificate SHA-256 fingerprint during the handshake, before any
request data (including auth tokens) is transmitted.  Uses urllib3's
built-in ``assert_fingerprint`` parameter for pre-request verification.

Thread-local session management is co-located here so that every
worker thread gets its own ``requests.Session`` with the correct
pinning adapter mounted.
"""

import logging
import threading

import requests
import requests.adapters

from sethlans_worker_agent import config_store

logger = logging.getLogger(__name__)

_thread_local = threading.local()


class CertificatePinningError(Exception):
    """Raised when the server certificate fingerprint does not match."""


class PinningHTTPAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that verifies the server cert's SHA-256 fingerprint
    during the TLS handshake, before any request data is sent."""

    def __init__(self, expected_fingerprint: str, **kwargs):
        self.expected_fingerprint = expected_fingerprint
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['assert_fingerprint'] = self.expected_fingerprint
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs['assert_fingerprint'] = self.expected_fingerprint
        return super().proxy_manager_for(proxy, **kwargs)


def get_session() -> requests.Session:
    """Return a thread-local ``requests.Session`` with pinning configured.

    The certificate fingerprint is read from the persistent JSON config
    store (``manager.cert_fingerprint``) rather than from the cached
    ``config`` module constant.  This ensures that render threads spawned
    after enrollment always read the current fingerprint from disk,
    regardless of ``importlib.reload`` timing.

    If a fingerprint is found, a :class:`PinningHTTPAdapter` is mounted
    on ``https://`` so that every request verifies the manager's
    certificate fingerprint during the TLS handshake.

    If no fingerprint is configured (pre-enrollment state), the session
    uses ``verify=False`` to accept the self-signed certificate.
    """
    if not hasattr(_thread_local, 'session'):
        s = requests.Session()
        fingerprint = config_store.get(
            "manager.cert_fingerprint", ""
        )
        if fingerprint:
            adapter = PinningHTTPAdapter(
                expected_fingerprint=fingerprint
            )
            s.mount('https://', adapter)
        else:
            s.verify = False
        _thread_local.session = s
    return _thread_local.session


def reset_sessions() -> None:
    """Clear the calling thread's session so the next :func:`get_session`
    call creates a fresh one with the current fingerprint from the
    config store.

    Note: ``threading.local()`` is per-thread — this only affects the
    thread that calls it.  Other threads that create sessions after this
    point will automatically read the current fingerprint from the
    persistent config store on disk.
    """
    if hasattr(_thread_local, 'session'):
        try:
            _thread_local.session.close()
        except Exception:
            pass
        del _thread_local.session
    logger.info("Thread-local TLS sessions cleared for re-enrollment.")
