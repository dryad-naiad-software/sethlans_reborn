# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Outbound HTTPS probe to the runtime's ``/api/health/`` endpoint.

Implements FR-W14's outbound HTTPS client per Spec 1 of the wizard.

Hard rules pulled from the spec:

* **Stdlib only.** ``urllib.request`` + ``ssl``. NO ``httpx``, NO
  ``requests``, NO ``certifi`` (DEVOPS-v22-MED-1, NF-9, NF-4 25 MB
  bundle ceiling).
* **TLS verification disabled.** ``check_hostname=False`` +
  ``verify_mode=ssl.CERT_NONE``. Matches the existing Docker
  ``HEALTHCHECK curl -fsk`` posture — the runtime's self-signed cert
  is trusted by deployment context.
* **SSLContext lifetime (SEC-v2.3-LOW-2).** The ``SSLContext`` MUST be
  constructed inline inside this function. It MUST NOT be cached at
  module level, MUST NOT be reused across invocations, MUST NOT be
  installed as ``ssl._create_default_https_context``, and MUST NOT be
  assigned to any object that outlives the single ``urlopen()`` call.
* **Booting-state exception envelope (DEVOPS-v2.3-LOW-1).** The
  following exceptions translate to "booting" (return ``None``):
  ``urllib.error.URLError``, ``socket.timeout``, ``ConnectionRefusedError``,
  ``ssl.SSLError``. Any other exception propagates — that's a programming
  error, not a transient runtime-not-yet-up condition.
* **JSON shape contract.** The handler returns the parsed body only when
  the response is HTTP 200 AND the body parses as a JSON object AND it
  contains the three keys ``boot_id``, ``worker_id``, ``version`` per
  FR-W14 ``ready`` status. Any other shape returns ``None``.
* **Synchronous.** The function blocks. The A4 wizard handler is
  responsible for offloading this onto a worker thread and rate-limiting
  via the FR-W14 1-second cache TTL.

The probe target URL itself is the caller's responsibility — FR-W14a
mandates the URL be a deterministic-per-topology constant defined in
the wizard handler module, not in this probe primitive.
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2.0

_REQUIRED_KEYS = ("boot_id", "worker_id", "version")


def _build_unverified_context() -> ssl.SSLContext:
    """Construct a fresh, single-use ``SSLContext`` with TLS verification
    disabled (FR-W14 + SEC-v2.3-LOW-2). MUST be called inline at probe
    time and never cached.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _shape_ok(body: object) -> bool:
    """Return True iff *body* is a dict containing the three FR-W14 keys."""
    if not isinstance(body, dict):
        return False
    return all(key in body for key in _REQUIRED_KEYS)


def probe_runtime_health(
    url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """Perform a single outbound HTTPS GET against the runtime's
    ``/api/health/`` endpoint.

    Args:
        url: Fully-qualified runtime health URL. Caller (the wizard
            handler) MUST source this from FR-W14a's hardcoded
            per-topology constant — never from operator input.
        timeout_seconds: Per-call timeout passed to ``urlopen``.
            Defaults to 2.0 seconds.

    Returns:
        The parsed response body (a dict containing ``boot_id``,
        ``worker_id``, ``version``) on a successful 200 with the
        expected JSON shape. Returns ``None`` on any of:

        * Transient connect/handshake/read failure (the four exception
          classes enumerated above) — runtime is "booting".
        * HTTP status != 200.
        * Response body that does not parse as a JSON object.
        * Response body that is missing any of the three required keys.

    Raises:
        Anything outside the booting-state exception envelope. Those
        are programming errors (e.g. ``ValueError`` from a malformed
        URL passed by the caller).
    """
    ctx = _build_unverified_context()
    try:
        # context is passed inline; not stored. Falls out of scope when
        # this function returns, per SEC-v2.3-LOW-2.
        with urllib.request.urlopen(url, context=ctx, timeout=timeout_seconds) as resp:
            status = getattr(resp, "status", None)
            if status is None:
                # Older interface name on some platforms.
                status = resp.getcode()
            if status != 200:
                logger.info("Runtime probe got HTTP %s from %s", status, url)
                return None
            raw = resp.read()
    except (
        urllib.error.URLError,
        socket.timeout,
        ConnectionRefusedError,
        ssl.SSLError,
    ) as exc:
        # Booting envelope per DEVOPS-v2.3-LOW-1.
        logger.debug("Runtime probe transient failure (%s): %s", type(exc).__name__, exc)
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.info("Runtime probe non-JSON response from %s: %s", url, exc)
        return None
    if not _shape_ok(body):
        logger.info(
            "Runtime probe missing required keys from %s; got %r",
            url, sorted(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
        return None
    return body
