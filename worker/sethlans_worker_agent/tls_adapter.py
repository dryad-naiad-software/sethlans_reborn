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
from typing import Tuple

import requests
import requests.adapters

from sethlans_worker_agent import config_store
from sethlans_worker_agent.peer_cert import PeerCertAdapter

logger = logging.getLogger(__name__)

_thread_local = threading.local()
_pin_lock = threading.Lock()  # a leaf: guards _process_pin only
_process_pin = ''             # the last non-empty fingerprint any thread read from the store


class CertificatePinningError(Exception):
    """Raised when the server certificate fingerprint does not match."""


class PinUnavailableError(requests.exceptions.ConnectionError):
    """The worker is enrolled (or its config cannot be read) but no certificate fingerprint is known.

    A ``ConnectionError``, so callers retry it like a network error; no request is sent unpinned.
    """


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


def _new_session(fingerprint: str) -> requests.Session:
    """A session for ``fingerprint`` (pinned) or for no fingerprint yet (unpinned)."""
    s = requests.Session()
    # Always disable CA chain validation — the manager uses a
    # self-signed cert. When a fingerprint is available, the
    # PinningHTTPAdapter verifies the cert via assert_fingerprint
    # during the TLS handshake (stronger than CA chain validation),
    # and urllib3 counts such a connection as verified, so it raises
    # no InsecureRequestWarning.
    s.verify = False
    if fingerprint:
        s.mount('https://', PinningHTTPAdapter(expected_fingerprint=fingerprint))
    return s


def unpinned_session() -> requests.Session:
    """A new unpinned session for one enrollment call; the caller closes it.

    Enrollment never uses the thread-local session: that one may be pinned
    to another manager. The HMAC-signed enrollment response proves the
    manager's identity instead, and urllib3's ``InsecureRequestWarning`` for
    that one request is accurate, so it is not suppressed.

    A :class:`~sethlans_worker_agent.peer_cert.PeerCertAdapter` is mounted
    on ``https://``: each response carries the certificate the server
    presented on the connection that carried it, which enrollment compares
    with the signed fingerprint (setup-rebuild FR-WK-8).
    """
    s = _new_session('')
    s.mount('https://', PeerCertAdapter())
    return s


def _stored_pin() -> Tuple[str, bool, bool]:
    """``(fingerprint, has_token, read_ok)`` from one strict read of the config store."""
    try:
        data = config_store.load_strict()
    except config_store.ConfigReadError:
        return '', False, False
    manager = data.get('manager')
    manager = manager if isinstance(manager, dict) else {}
    fingerprint = manager.get('cert_fingerprint')
    return (fingerprint if isinstance(fingerprint, str) else ''), bool(manager.get('api_token')), True


def _current_pin() -> str:
    """The fingerprint to pin to: the store's, else the process pin; ``''`` only before enrollment.

    Raises :class:`PinUnavailableError` when the worker has an API token, or
    the store cannot be read, and no fingerprint is known: no unpinned
    request may carry a token.
    """
    global _process_pin
    fingerprint, has_token, read_ok = _stored_pin()
    with _pin_lock:
        if fingerprint:
            _process_pin = fingerprint
            return fingerprint
        if _process_pin:
            return _process_pin
    if has_token or not read_ok:
        raise PinUnavailableError("The manager's certificate fingerprint could not be read; no request is sent "
                                  "until it can be.")
    return ''


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

    Only before enrollment (no API token saved, no fingerprint known) is the
    session unpinned, with ``verify=False`` for the self-signed certificate.

    The store is read lock-free, so a read can fail or come back empty while
    the file is replaced. The last fingerprint any thread read is kept for
    the process (``_process_pin``, under ``_pin_lock``) and used when a read
    finds none. With a token saved and no fingerprint known at all,
    :class:`PinUnavailableError` is raised instead of sending the token
    unpinned (#306).

    An unpinned session is not kept once a fingerprint exists: while a
    thread's session is unpinned, every call reads the store again, and the
    first call that finds a fingerprint replaces the session with a pinned
    one. Without this, the thread that ran the enrollment (the main thread
    in unattended enrollment, which then runs the agent loop) kept its
    pre-enrollment session, so its heartbeats and polls were never pinned
    and each logged ``InsecureRequestWarning``. A pinned session keeps its
    fingerprint for the life of its thread; nothing here turns it back into
    an unpinned one or moves it to another fingerprint.
    """
    session = getattr(_thread_local, 'session', None)
    if session is not None and getattr(_thread_local, 'pinned', False):
        return session
    fingerprint = _current_pin()
    if session is not None and not fingerprint:
        return session  # still no pin: keep the pre-enrollment session
    if session is not None:
        logger.info("A manager certificate fingerprint is now saved; this thread's requests are pinned to it.")
        try:
            session.close()
        except Exception:
            pass
    _thread_local.session = _new_session(fingerprint)
    _thread_local.pinned = bool(fingerprint)
    return _thread_local.session


def clear_pin() -> None:
    """Forget the process pin; the next read of the store sets it again."""
    global _process_pin
    with _pin_lock:
        _process_pin = ''


def reset_sessions() -> None:
    """Clear the calling thread's session and the process pin, so the next
    :func:`get_session` call creates a fresh one with the current
    fingerprint from the config store.

    Note: ``threading.local()`` is per-thread — this only affects the
    session of the thread that calls it; other threads keep theirs. No
    product code calls it today: enrollment uses its own unpinned session
    (:func:`unpinned_session`), and a worker enrolled with another manager
    is restarted to pin every thread to the new certificate.
    """
    if hasattr(_thread_local, 'session'):
        try:
            _thread_local.session.close()
        except Exception:
            pass
        del _thread_local.session
    _thread_local.pinned = False
    clear_pin()
    logger.info("Thread-local TLS session and the process pin cleared.")
