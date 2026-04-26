# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/wizard/runtime-ready/`` — runtime self-probe (FR-W14).

Spec 1 / A4. Browser polls this; the wizard in turn probes the
runtime's ``/api/health/`` and reports ``booting`` / ``ready`` /
``failed``.

Hard rules:

* Probe URL is hardcoded per topology (FR-W14a / SEC-MED-12). Probe
  cache, failed-marker short-circuit, and lock plumbing live in
  :mod:`._runtime_probe` (split for the 300-line cap).
* Lock-coupling rule (CONC-v23-MED-1): handoff_lock MUST NOT be held
  when in_flight_lock is acquired. Once in_flight_lock is held,
  handoff_lock may be acquired and released in short critical sections.
  Never hold handoff_lock and then try to acquire in_flight_lock.
* ``.runtime_failed`` marker short-circuits the probe (FR-IPC8).
* Query-string ``?url=`` and other token-shaped keys → HTTP 400
  (SEC-MED-12 defense in depth).
* FR-W17(a) ack-flag transition + 3-second grace-timer arming happen
  ONLY from the WSGI response iterable's PEP 3333 ``close()`` method
  (the :class:`_ReadyResponseWrapper`). They MUST NOT happen at
  handler return, MUST NOT happen at the in-process flag set inside
  the request handler body. This guarantees the browser has actually
  received the redirect URL before the 3-second countdown begins.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import auth_state, probe, shutdown
from wizard.sethlans_wizard.handlers import _runtime_probe, _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)


def reset_state_for_tests() -> None:
    """Clear cache + in-flight lock. Production code MUST NOT call."""
    _runtime_probe.reset_state_for_tests()


class _ReadyResponseWrapper:
    """PEP 3333 WSGI response wrapper that arms the FR-W17(a) grace timer.

    Waitress invokes ``close()`` after the response body has been
    fully written to the wire. That is the canonical post-flush hook
    for WSGI (CONC-v23-MED-2); ASGI-specific constructs (Starlette
    ``BackgroundTask``, ``asyncio.create_task``) are explicitly banned
    by the spec — the wizard runs WSGI under Waitress (FR-W12).

    On the FIRST ``close()`` of a ready-status response:
    1. Sets ``_browser_redirect_acknowledged = True`` under the
       handoff-state lock (via
       ``auth_state.mark_browser_redirect_acknowledged``).
    2. Schedules the 3-second :class:`threading.Timer` whose callback
       calls ``server.shutdown_server()`` then ``os._exit(0)``.

    Subsequent ``close()`` calls (re-iteration, transport quirks) are
    no-ops thanks to the wrapper's ``_closed`` guard AND
    :func:`shutdown.schedule_grace_timer`'s own idempotent flag.
    """

    def __init__(
        self,
        body_chunks: Iterable[bytes],
        on_close: Callable[[], None],
    ) -> None:
        # Materialise so the close()-hook fires on the SAME wrapper
        # instance the iterator drained from. ``send_json`` returns a
        # tiny single-chunk list, so this is effectively zero-cost.
        self._chunks: list[bytes] = list(body_chunks)
        self._on_close = on_close
        self._closed: bool = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._on_close()
        except Exception:  # noqa: BLE001 — defensive; close() must not raise
            logger.exception("FR-W17(a) close-hook callback failed")


def _arm_grace_timer_on_close() -> None:
    """Default close-hook callback: ack the redirect + arm the timer.

    Both calls are idempotent at the auth_state / shutdown layer, so a
    second close() (e.g., re-iteration) cannot double-arm. Subsequent
    ready responses (e.g., a second browser tab polling) MUST NOT
    re-arm the grace timer; the first one already did. FR-W17(a) is
    strictly first-time only.
    """
    first = auth_state.mark_browser_redirect_acknowledged()
    if not first:
        return
    shutdown.schedule_grace_timer()


def make_runtime_ready_handler(
    data_dir: Path,
    ipc_secret: bytes,
    probe_runtime_health: Optional[Callable[[str], Optional[dict]]] = None,
    on_first_ready_close: Optional[Callable[[], None]] = None,
) -> Callable:
    """Return a WSGI handler bound to *data_dir* / *ipc_secret*.

    *probe_runtime_health* is injected for tests; defaults to
    :func:`wizard.sethlans_wizard.probe.probe_runtime_health`.

    *on_first_ready_close* is the action triggered from the response
    iterable's PEP 3333 ``close()`` hook on the first ready response.
    Defaults to :func:`_arm_grace_timer_on_close` (production path).
    Tests inject a capture-only callback to assert the hook fired
    exactly once.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")
    secret = bytes(ipc_secret)
    probe_fn = probe_runtime_health or probe.probe_runtime_health
    on_close = on_first_ready_close or _arm_grace_timer_on_close

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir, secret, probe_fn, on_close)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
    ipc_secret: bytes,
    probe_fn: Callable[[str], Optional[dict]],
    on_first_ready_close: Callable[[], None],
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "GET")],
        )

    if _wsgi.query_string_has_forbidden_key(environ):
        # SEC-MED-12 defense-in-depth.
        logger.warning(
            "Refused runtime-ready request with forbidden query key from %s",
            _wsgi.client_ip(environ),
        )
        return _wsgi.send_json(
            start_response,
            {"error": "session token / url must not appear in URL"},
            status=400,
        )

    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    result = _runtime_probe.execute_probe(data_dir, ipc_secret, probe_fn)
    body_iter = _wsgi.send_json(start_response, result, status=200)
    if result.get("status") == "ready":
        # FR-W17(a) close()-hook wrapper. Ack flag + grace timer are
        # armed ONLY when Waitress has flushed the body to the wire.
        return _ReadyResponseWrapper(body_iter, on_first_ready_close)
    return body_iter


__all__ = [
    "make_runtime_ready_handler",
    "reset_state_for_tests",
    "_ReadyResponseWrapper",
]
