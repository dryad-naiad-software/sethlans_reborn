# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/wizard/runtime-ready/`` — runtime self-probe (FR-W14).

Spec 1 / A4. Browser polls this; the wizard in turn probes the
runtime's ``/api/health/`` and reports ``booting`` / ``ready`` /
``failed``.

Hard rules:

* Probe URL is hardcoded per topology (FR-W14a / SEC-MED-12), sourced
  from :data:`_TOPOLOGY_PROBE_URL`. NOT operator-controllable.
* 1-second TTL probe cache uses :func:`time.monotonic`
  (CONC-v23-LOW-1).
* Two locks: the singleton handoff-state lock (owned by
  ``auth_state``) and a module-local :data:`_in_flight_lock`.
  Acquisition order (CONC-v23-MED-1): handoff-state lock ALWAYS
  acquired BEFORE in-flight lock, never the reverse. Outbound HTTPS
  GET runs with NO locks held.
* ``.runtime_failed`` marker short-circuits the probe (FR-IPC8).
* Query-string ``?url=`` and other token-shaped keys → HTTP 400
  (SEC-MED-12 defense in depth).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import auth_state, ipc, probe
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.handlers.topology import TOPOLOGY_FILENAME

logger = logging.getLogger(__name__)

# FR-W14a — hardcoded probe URLs per topology. NEVER read from env,
# request body, query string, header, cookie, or marker file.
_TOPOLOGY_PROBE_URL: dict[str, str] = {
    "manager": "https://localhost:8080/api/health/",
    "manager_worker": "https://localhost:8080/api/health/",
    "worker_only": "https://localhost:8081/api/health/",
}

# FR-W14 — 1-second cache TTL. ``time.monotonic`` per CONC-v23-LOW-1.
_CACHE_TTL_SECONDS = 1.0

# Module-local lock (FR-W14, second of two locks). The handoff-state
# lock lives in ``auth_state``. Acquisition order: handoff-state lock
# BEFORE in-flight probe lock, never the reverse (CONC-v23-MED-1).
_in_flight_lock: threading.Lock = threading.Lock()

# Probe-result cache. Tuple of (timestamp_monotonic, result_dict).
# Guarded by the singleton handoff-state lock from ``auth_state``.
# ``result_dict`` is the JSON envelope already shaped for the response
# body so the cache short-circuit can return it without re-formatting.
_cache: Optional[tuple[float, dict]] = None


def reset_state_for_tests() -> None:
    """Clear the in-flight lock + cache. Production code MUST NOT call."""
    global _cache
    # Try to release any in-flight lock left dangling by a crashed test.
    try:  # pragma: no cover - defensive
        if _in_flight_lock.locked():
            _in_flight_lock.release()
    except RuntimeError:  # pragma: no cover - lock owned by another thread
        pass
    _cache = None


def _cache_fresh_locked(now: float) -> Optional[dict]:
    """Return the cached result if still within the TTL window.

    Caller MUST hold the handoff-state lock from
    ``auth_state.get_handoff_lock()``.
    """
    if _cache is None:
        return None
    ts, result = _cache
    if (now - ts) < _CACHE_TTL_SECONDS:
        return result
    return None


def _publish_cache_locked(result: dict) -> None:
    """Update the cache. Caller MUST hold the handoff-state lock.

    Ack-flag transition is the caller's responsibility, performed AFTER
    releasing the handoff lock (``auth_state``'s ack helper re-acquires
    the same non-reentrant lock).
    """
    global _cache
    _cache = (time.monotonic(), result)


def _resolve_probe_url(data_dir: Path) -> Optional[str]:
    """Return the hardcoded probe URL for the persisted topology, or None."""
    topology_path = data_dir / TOPOLOGY_FILENAME
    try:
        raw = topology_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read topology %s: %s", topology_path, exc)
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    topology = payload.get("topology")
    if not isinstance(topology, str):
        return None
    return _TOPOLOGY_PROBE_URL.get(topology)


def _booting() -> dict:
    return {"status": "booting", "url": None}


def _ready(url: str) -> dict:
    return {"status": "ready", "url": url}


def _failed(log_path: str) -> dict:
    return {"status": "failed", "url": None, "log_path": log_path}


def _read_launcher_log_path(data_dir: Path) -> str:
    """Return the launcher-log path (FR-W-FE6); empty string if missing."""
    candidate = data_dir / "wizard" / ".launcher_log_path"
    try:
        raw = candidate.read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def _execute_probe(
    data_dir: Path,
    ipc_secret: bytes,
    probe_runtime_health: Callable[[str], Optional[dict]],
) -> dict:
    """Resolve probe URL, run the probe (with locks), and return status.

    *probe_runtime_health* is injected so the test suite can replace
    the real network call without monkeypatching the ``probe`` module.
    """
    handoff_lock = auth_state.get_handoff_lock()
    now = time.monotonic()

    # 1. Cheap cache hit — handoff lock held momentarily.
    with handoff_lock:
        cached = _cache_fresh_locked(now)
    if cached is not None:
        if cached.get("status") == "ready":
            auth_state.mark_browser_redirect_acknowledged()
        return cached

    # 2. Failed-marker short-circuit (no probe). Read outside the lock —
    # the marker validation is pure I/O.
    failed_marker_path = data_dir / "wizard" / ipc.MARKER_RUNTIME_FAILED
    failed_payload = ipc.read_marker(
        failed_marker_path,
        ipc_secret,
        expected_type="runtime_failed",
        data_dir=data_dir,
    )
    if failed_payload is not None:
        log_path = _read_launcher_log_path(data_dir)
        result = _failed(log_path)
        with handoff_lock:
            _publish_cache_locked(result)
        return result

    # 3. Slow path. CONC-v23-MED-1: handoff lock RELEASED before
    # in-flight lock acquired. Handoff lock re-acquired below in short,
    # momentary critical sections only — never AB-BA against in-flight.
    with _in_flight_lock:
        with handoff_lock:
            cached = _cache_fresh_locked(time.monotonic())
        if cached is not None:
            if cached.get("status") == "ready":
                auth_state.mark_browser_redirect_acknowledged()
            return cached

        url = _resolve_probe_url(data_dir)
        if url is None:
            # No topology yet — nothing to probe, nothing to redirect to.
            result = _booting()
            with handoff_lock:
                _publish_cache_locked(result)
            return result

        # Outbound HTTPS GET — NO locks held. The in-flight lock
        # serialises probes; the handoff lock is dropped here.
        body = probe_runtime_health(url)
        if body is None:
            result = _booting()
        else:
            result = _ready(url)

        with handoff_lock:
            _publish_cache_locked(result)

    if result.get("status") == "ready":
        # FR-W14 — first-ready ack flag. auth_state helper takes the
        # handoff lock itself; MUST NOT be held here.
        auth_state.mark_browser_redirect_acknowledged()
    return result


def make_runtime_ready_handler(
    data_dir: Path,
    ipc_secret: bytes,
    probe_runtime_health: Optional[Callable[[str], Optional[dict]]] = None,
) -> Callable:
    """Return a WSGI handler bound to *data_dir* / *ipc_secret*.

    *probe_runtime_health* is injected for tests; defaults to
    :func:`wizard.sethlans_wizard.probe.probe_runtime_health`.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")
    secret = bytes(ipc_secret)
    probe_fn = probe_runtime_health or probe.probe_runtime_health

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir, secret, probe_fn)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
    ipc_secret: bytes,
    probe_fn: Callable[[str], Optional[dict]],
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

    result = _execute_probe(data_dir, ipc_secret, probe_fn)
    return _wsgi.send_json(start_response, result, status=200)


__all__ = [
    "make_runtime_ready_handler",
    "reset_state_for_tests",
]
