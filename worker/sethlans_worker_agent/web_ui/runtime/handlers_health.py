# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Health endpoint (``GET /api/health/``) for the worker web UI.

Returns the worker ``boot_id`` (fresh UUID per process start), the
persistent ``worker_id`` (or ``null`` pre-enrollment), and the worker
version string.  Anonymous and always reachable in both setup mode and
runtime mode so external probes (Docker HEALTHCHECK, the standalone
wizard's worker-only redirect probe) have a stable target.

Mirrors the manager's ``GET /api/health/`` shape, with one extra field
(``worker_id``) per the brainstorm Q1 resolution.  Pre-enrollment
behavior is intentional: the Docker HEALTHCHECK runs from container
start and the wizard probe runs before enrollment, so the response MUST
return 200 even when ``worker_id`` is unset (FR-H-7).
"""

from typing import Callable, Iterable

from sethlans_worker_agent import runtime_state, system_monitor
from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi


def handle_health_wsgi(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Return ``{"boot_id", "worker_id", "version"}`` as JSON.

    All three values are read at request time so the handler reflects
    the current process state (in particular, ``worker_id`` flips from
    ``None`` to a UUID string when ``register_with_manager()`` writes
    ``system_monitor.WORKER_ID`` -- an attribute assignment that is
    atomic on CPython under the GIL).
    """
    # Lazy import so tests can stub ``shared.version.get_version`` via
    # ``monkeypatch.setattr`` without rewiring module imports; mirrors
    # the manager's lazy-import pattern in
    # ``manager/workers/views/health.py``.
    from shared.version import get_version

    return send_json_wsgi(start_response, {
        "boot_id": runtime_state.worker_boot_id or "",
        "worker_id": system_monitor.WORKER_ID,
        "version": get_version(),
    })


__all__ = ["handle_health_wsgi"]
