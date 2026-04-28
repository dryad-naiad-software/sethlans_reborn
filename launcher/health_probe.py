# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cold-boot health-poll helper for the launcher splash dismissal path.

This module owns the unified ``wait_for_health`` helper used by every
cold-boot orchestration path (wizard, manager, manager+worker, worker).
It generalizes the older ``orchestration.wait_for_manager_ready`` so a
single helper drives the splash-dismissal contract for all paths.

Contract (FR-3 / FR-5 / NFR-6):

* HTTP 200 + JSON body containing both ``boot_id`` and ``version``
  (FR-W14 envelope intersection) is the ONLY positive return.
* Returns ``False`` on (a) wall-clock timeout, (b) ``proc`` exited
  before health, (c) ``KeyboardInterrupt`` raised inside the poll
  loop. KI MUST NOT propagate past the helper.
* Rejects non-loopback URLs (``NonLoopbackHealthURL``) BEFORE issuing
  any network call, so the ``CERT_NONE`` / ``check_hostname=False``
  posture is structurally bounded to localhost (NFR-6).

The per-poll HTTPS GET is implemented via :func:`probe_health_once`,
which is the single-source-of-truth for the FR-W14 envelope check.
``launcher.wizard_runtime._probe_runtime_health`` delegates here so
the launcher and wizard cannot drift on what "healthy" means
(OQ-2 consolidation).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# FR-3 / D5 — 30 s wall-clock cold-boot health budget shared by every
# orchestration path. The 250 ms cadence balances UX latency
# (sub-perceptual jitter on dismissal) against TLS handshake cost.
HEALTH_TIMEOUT = 30.0
HEALTH_POLL_INTERVAL = 0.25
# Per-call HTTPS GET timeout. Small so a hung handshake on one poll
# iteration does not dominate the overall wall-clock budget.
HEALTH_PROBE_TIMEOUT = 2.0

# FR-5 / FR-W14 envelope intersection. Worker also returns
# ``worker_id``; manager doesn't. Both always return these two.
_HEALTH_REQUIRED_KEYS = ("boot_id", "version")

# NFR-6 — only loopback hosts may reach the helper. The literal string
# ``localhost`` is accepted alongside numeric IPs because some callers
# build URLs from human-readable hostnames; everything else MUST be
# rejected before we issue a request with verification disabled.
_LOOPBACK_LITERAL = "localhost"


class NonLoopbackHealthURL(ValueError):
    """Raised when ``wait_for_health`` is handed a non-loopback URL.

    NFR-6: the cold-boot probe deliberately disables certificate
    verification because the manager's self-signed cert is unverifiable
    until pinned. That posture is safe ONLY against localhost — any
    remote target would expose unverified TLS as a real attack surface.
    """


def _validate_loopback_url(url: str) -> None:
    """Reject non-loopback URLs (NFR-6).

    Accepts ``127.0.0.1``, ``::1``, and the literal hostname
    ``localhost``. Anything else raises :class:`NonLoopbackHealthURL`.
    """
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise NonLoopbackHealthURL(
            f"wait_for_health requires a loopback URL; got {url!r}",
        )
    if host == _LOOPBACK_LITERAL:
        return
    try:
        addr = ipaddress.ip_address(host)
    except ValueError as exc:
        raise NonLoopbackHealthURL(
            f"wait_for_health requires a loopback URL; got host {host!r}",
        ) from exc
    if not addr.is_loopback:
        raise NonLoopbackHealthURL(
            f"wait_for_health requires a loopback URL; got host {host!r}",
        )


def _make_ssl_context() -> ssl.SSLContext:
    """Build a fresh single-use unverified SSLContext.

    Mirrors ``wizard_runtime._probe_runtime_health``: the manager's
    self-signed cert cannot be verified until enrollment pins it.
    The context MUST NOT be cached — fresh per call so internal state
    does not leak across probes.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def probe_health_once(
    url: str, timeout: float = HEALTH_PROBE_TIMEOUT,
) -> bool:
    """Single HTTPS GET against ``url``; True iff 200 + FR-W14 envelope.

    Used both by :func:`wait_for_health` (per-poll) and by
    ``launcher.wizard_runtime._probe_runtime_health`` (post-handoff
    port-bind watch). Single-sourcing the envelope check keeps the
    cold-boot and post-handoff paths from drifting (OQ-2).
    """
    ctx = _make_ssl_context()
    try:
        with urllib.request.urlopen(  # noqa: S310 — self-signed by design
            url, context=ctx, timeout=timeout,
        ) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return False
            raw = resp.read()
    except (
        urllib.error.URLError,
        socket.timeout,
        ConnectionRefusedError,
        ssl.SSLError,
        OSError,
    ):
        # OSError covers WinError 10061 etc. before the socket is up.
        return False
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(body, dict):
        return False
    return all(key in body for key in _HEALTH_REQUIRED_KEYS)


def wait_for_health(
    url: str,
    proc: Optional[subprocess.Popen] = None,
    timeout: float = HEALTH_TIMEOUT,
    poll_interval: float = HEALTH_POLL_INTERVAL,
) -> bool:
    """Block until ``url`` returns 200 + FR-W14 envelope, or fail.

    Returns ``True`` on first 200 with both ``boot_id`` and ``version``
    in the JSON body.

    Returns ``False`` on:
      * wall-clock timeout (``timeout`` seconds elapse),
      * ``proc`` exited before health (caller spawned it; we see the
        corpse),
      * ``KeyboardInterrupt`` raised inside the poll loop (FR-3 — KI
        MUST NOT propagate past this helper).

    Raises :class:`NonLoopbackHealthURL` if ``url`` does not resolve to
    a loopback host (NFR-6). The validation runs ONCE at entry, before
    any network call.
    """
    _validate_loopback_url(url)
    # Negative or zero timeout: caller has already burned the shared
    # deadline. Don't issue any probe; treat as timeout immediately.
    if timeout <= 0:
        logger.warning(
            "wait_for_health invoked with non-positive timeout=%.3fs at %s",
            timeout, url,
        )
        return False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if proc is not None and proc.poll() is not None:
                logger.warning(
                    "subprocess exited (code %s) before %s became healthy",
                    proc.returncode, url,
                )
                return False
            if probe_health_once(url):
                return True
            if time.monotonic() >= deadline:
                logger.warning(
                    "%s did not become healthy within %.1fs",
                    url, timeout,
                )
                return False
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        # FR-3 — KI from any phase (urlopen handshake, proc.poll(),
        # time.sleep) MUST collapse to False so the launcher's normal
        # cleanup path runs instead of an exception unwind.
        logger.warning("health wait interrupted by user at %s", url)
        return False


__all__ = [
    "HEALTH_POLL_INTERVAL",
    "HEALTH_PROBE_TIMEOUT",
    "HEALTH_TIMEOUT",
    "NonLoopbackHealthURL",
    "probe_health_once",
    "wait_for_health",
]
