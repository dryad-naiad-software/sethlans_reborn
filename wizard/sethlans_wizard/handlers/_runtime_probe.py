# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Internal probe-execution helpers for ``runtime_ready.py``.

Split from :mod:`wizard.sethlans_wizard.handlers.runtime_ready` to keep
each module under the 300-line limit. Owns the FR-W14 cache + lock
plumbing and the ``.runtime_failed`` short-circuit; the public handler
module composes these primitives with the FR-W17(a) close()-hook
wrapper.

Lock-coupling rule (CONC-v23-MED-1): the handoff-state lock from
:func:`wizard.sethlans_wizard.auth_state.get_handoff_lock` MUST NOT
be held when :data:`_in_flight_lock` is acquired. Once the in-flight
lock is held, the handoff lock may be acquired and released in short
critical sections — never the reverse.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from wizard.sethlans_wizard import auth_state, ipc
from wizard.sethlans_wizard.handlers.topology import TOPOLOGY_FILENAME

logger = logging.getLogger(__name__)

# FR-W14a — hardcoded probe URLs per topology. NEVER read from env,
# request body, query string, header, cookie, or marker file.
TOPOLOGY_PROBE_URL: dict[str, str] = {
    "manager": "https://localhost:8080/api/health/",
    "manager_worker": "https://localhost:8080/api/health/",
    "worker_only": "https://localhost:8081/api/health/",
}

# FR-W14 — 1-second cache TTL. ``time.monotonic`` per CONC-v23-LOW-1.
CACHE_TTL_SECONDS = 1.0

# Module-local lock (FR-W14, second of two locks). Lock-coupling rule
# (CONC-v23-MED-1): handoff_lock MUST NOT be held when in_flight_lock
# is acquired; never hold handoff_lock and then try to acquire
# in_flight_lock.
_in_flight_lock: threading.Lock = threading.Lock()

# Probe-result cache. Tuple of (timestamp_monotonic, result_dict).
# Guarded by the singleton handoff-state lock from ``auth_state``.
_cache: Optional[tuple[float, dict]] = None


def reset_state_for_tests() -> None:
    """Clear the in-flight lock + cache. Production code MUST NOT call."""
    global _cache
    try:  # pragma: no cover - defensive
        if _in_flight_lock.locked():
            _in_flight_lock.release()
    except RuntimeError:  # pragma: no cover - lock owned by another thread
        pass
    _cache = None


def _cache_fresh_locked(now: float) -> Optional[dict]:
    """Return the cached result if still within the TTL window."""
    if _cache is None:
        return None
    ts, result = _cache
    if (now - ts) < CACHE_TTL_SECONDS:
        return result
    return None


def _publish_cache_locked(result: dict) -> None:
    """Update the cache. Caller MUST hold the handoff-state lock."""
    global _cache
    _cache = (time.monotonic(), result)


def _resolve_probe_url(data_dir: Path) -> Optional[str]:
    """Return the hardcoded probe URL for the persisted topology."""
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
    return TOPOLOGY_PROBE_URL.get(topology)


def booting() -> dict:
    return {"status": "booting", "url": None}


def ready(url: str) -> dict:
    return {"status": "ready", "url": url}


def failed(log_path: str) -> dict:
    return {"status": "failed", "url": None, "log_path": log_path}


def _read_launcher_log_path(data_dir: Path) -> str:
    """Return the launcher-log path (FR-W-FE6); empty string if missing."""
    candidate = data_dir / "wizard" / ".launcher_log_path"
    try:
        raw = candidate.read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def execute_probe(
    data_dir: Path,
    ipc_secret: bytes,
    probe_runtime_health: Callable[[str], Optional[dict]],
) -> dict:
    """Resolve probe URL, run the probe (with locks), return status.

    The FR-W17(a) ack-flag transition + grace-timer arming are NOT
    performed here — they happen ONLY from the WSGI response
    iterable's PEP 3333 ``close()`` hook in :mod:`runtime_ready`,
    after Waitress has flushed the body to the wire.
    """
    handoff_lock = auth_state.get_handoff_lock()
    now = time.monotonic()

    # 1. Cheap cache hit — handoff lock held momentarily.
    with handoff_lock:
        cached = _cache_fresh_locked(now)
    if cached is not None:
        return cached

    # 2. Failed-marker short-circuit (no probe). Read outside the lock.
    failed_marker_path = data_dir / "wizard" / ipc.MARKER_RUNTIME_FAILED
    failed_payload = ipc.read_marker(
        failed_marker_path,
        ipc_secret,
        expected_type="runtime_failed",
        data_dir=data_dir,
    )
    if failed_payload is not None:
        log_path = _read_launcher_log_path(data_dir)
        result = failed(log_path)
        with handoff_lock:
            _publish_cache_locked(result)
        return result

    # 3. Slow path. Lock-coupling rule (CONC-v23-MED-1): handoff_lock
    # is RELEASED before in_flight_lock is acquired; once in_flight_lock
    # is held, handoff_lock may be acquired and released in short
    # critical sections — never the reverse.
    with _in_flight_lock:
        with handoff_lock:
            cached = _cache_fresh_locked(time.monotonic())
        if cached is not None:
            return cached

        url = _resolve_probe_url(data_dir)
        if url is None:
            result = booting()
            with handoff_lock:
                _publish_cache_locked(result)
            return result

        # Outbound HTTPS GET — NO locks held.
        body = probe_runtime_health(url)
        if body is None:
            result = booting()
        else:
            result = ready(url)

        with handoff_lock:
            _publish_cache_locked(result)

    return result


__all__ = [
    "CACHE_TTL_SECONDS",
    "TOPOLOGY_PROBE_URL",
    "booting",
    "execute_probe",
    "failed",
    "ready",
    "reset_state_for_tests",
]
