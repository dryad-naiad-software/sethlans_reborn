# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one enrollment path of the worker (setup-rebuild FR-WK-1, FR-WK-5, FR-WK-6).

``enroll_pairs`` is used by ``--setup-apply`` and by the agent's start with
``SETHLANS_WORKER_ENROLLMENT_KEY`` (``enrollment_start``): it runs
``enrollment_client.enroll`` (key proof, signed and encrypted answer,
certificate check) and returns the answer with the ``config.json`` pairs to
write. It writes nothing itself, so ``--setup-apply`` can put everything into
one ``config_store.set_many``.

``parse_manager_url`` reads the ``https://host[:port]`` the setup form gives
(port 8080 when none is given). ``error_code`` names an enrollment failure
for the command's ``{"error", "message"}`` answer. Nothing here logs the key
or the token.
"""
import ipaddress
import urllib.parse
from typing import List, Optional, Tuple

import requests

from sethlans_worker_agent import enrollment_client

DEFAULT_PORT = 8080
NAME_KEY = 'manager.name'  # the manager's name from the setup form; shown by --setup-cards only
_PATHS = ('', '/', '/api', '/api/')


def api_url(host: str, port: int) -> str:
    """``https://host:port/api/``, with an IPv6 address in brackets."""
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f'[{host}]'
    except ValueError:
        pass
    return f'https://{host}:{port}/api/'


def parse_manager_url(text) -> Tuple[str, int]:
    """``(host, port)`` from ``https://host[:port]``; ``ValueError`` with one plain sentence otherwise."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        raise ValueError("The manager URL must be text such as https://manager.example:8080.")
    parts = urllib.parse.urlsplit(text.strip())
    try:
        port = parts.port
    except ValueError as invalid:
        raise ValueError("The manager URL has a port that is not a number from 1 to 65535.") from invalid
    if port == 0:
        raise ValueError("The manager URL has a port that is not a number from 1 to 65535.")
    if parts.scheme != 'https' or not parts.hostname:
        raise ValueError("The manager URL must start with https:// and name a host.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in _PATHS:
        raise ValueError("The manager URL must be https://host or https://host:port, with nothing after it.")
    return parts.hostname, port or DEFAULT_PORT


def enroll_pairs(host: str, port: int, enrollment_key: str, hostname: str,
                 manager_name: Optional[str] = None) -> Tuple[dict, List[tuple]]:
    """Enroll with the manager at ``host:port``; ``(answer, config pairs)``. Raises ``EnrollmentError``.

    The answer is ``enrollment_client.enroll``'s (``api_token``,
    ``cert_fingerprint``, ``manager_id`` and, when the manager sent one,
    ``status``). ``manager_name`` (already checked, or ``None`` when not
    known) is saved as ``manager.name``, so a new enrollment never keeps the
    name of an earlier manager.
    """
    answer = enrollment_client.enroll(api_url(host, port), enrollment_key, hostname)
    pairs = [
        ('manager.api_token', answer['api_token']),
        ('manager.cert_fingerprint', answer['cert_fingerprint']),
        ('manager.manager_id', answer['manager_id']),
        ('manager.host', host),
        ('manager.port', int(port)),
        (NAME_KEY, manager_name),
    ]
    return answer, pairs


def error_code(error: enrollment_client.EnrollmentError) -> str:
    """The ``error`` code for an enrollment failure."""
    if isinstance(error, enrollment_client.InvalidKeyError):
        return 'invalid_key'
    if isinstance(error, enrollment_client.CertificateMismatchError):
        return 'certificate_mismatch'
    if isinstance(error, enrollment_client.RateLimitedError):
        return 'rate_limited'
    if isinstance(error, enrollment_client.ServiceUnavailableError):
        return 'manager_not_ready'
    if isinstance(error, enrollment_client.InvalidHostnameError):
        return 'invalid_hostname'
    if isinstance(error.__cause__, requests.exceptions.RequestException):
        return 'unreachable'
    return 'enrollment_failed'
