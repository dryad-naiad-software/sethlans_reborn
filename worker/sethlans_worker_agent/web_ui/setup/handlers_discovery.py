# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard manager discovery and selection handlers.

GET  /api/setup/discover/              -- multicast discovery scan
POST /api/setup/worker/select-manager/ -- store selected manager

Phase 4f of the Waitress migration: these handlers are sync WSGI.
The multicast listener is a blocking sync call; it runs directly on
the Waitress request thread (bounded by ``MulticastListener(timeout
=5.0)``, preserved byte-equivalent to the ASGI implementation).

Module-level selection state (``_selected_manager_url``,
``_selected_manager_id``, ``_selected_manager_meta``) is guarded by
``_state_lock`` so concurrent reads/writes from Waitress request
threads cannot observe torn state (e.g., URL from one selection
paired with metadata from another).  The mutation endpoint
``handle_select_manager`` additionally serializes through
:func:`setup_mutation_lock` for fail-fast 409 behavior under
concurrent wizard POSTs (FR-18).  The read endpoint
``handle_discover`` is lock-free by design so the wizard's poll loop
does not spinner-storm during an in-flight mutation.
"""

import logging
import threading
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, parse_json_body_wsgi,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup.lock import (
    setup_mutation_lock,
)

logger = logging.getLogger(__name__)

# ---- Selected manager state (guarded by _state_lock) ----
_state_lock = threading.Lock()
_selected_manager_url: str | None = None
_selected_manager_id: str | None = None
_selected_manager_meta: dict | None = None


def get_selected_manager_url() -> str | None:
    """Return the manager URL chosen during the wizard."""
    with _state_lock:
        return _selected_manager_url


def get_selected_manager_id() -> str | None:
    """Return the manager_id chosen during the wizard."""
    with _state_lock:
        return _selected_manager_id


def get_selected_manager_meta() -> dict | None:
    """Return a defensive copy of the selected manager metadata.

    Callers must not mutate the dict -- we return a shallow copy so
    concurrent readers and a future writer cannot observe torn state.
    """
    with _state_lock:
        if _selected_manager_meta is None:
            return None
        return dict(_selected_manager_meta)


def _build_manager_url(manager: dict) -> str:
    """Build ``https://host:port/api/`` from a discovery record."""
    host = manager.get("ip") or manager.get("host") or "127.0.0.1"
    port = manager.get("port") or 8080
    return f"https://{host}:{port}/api/"


def _run_discovery() -> list[dict]:
    """Run multicast discovery synchronously (blocking).

    Returns a list of manager dicts suitable for JSON response.
    Bounded by the MulticastListener timeout (set to 5 seconds
    below) so the Waitress request thread is held at most ~5s per
    call (FR-7).
    """
    from sethlans_worker_agent.multicast_listener import (
        MulticastListener,
    )
    listener = MulticastListener(timeout=5.0)
    announcements = listener.discover()
    return [
        {
            "name": m.get("name", ""),
            "host": m.get("host", ""),
            "ip": m.get("ip", ""),
            "port": m.get("port", 8080),
            "manager_id": m.get("manager_id", ""),
            "version": m.get("version", ""),
        }
        for m in announcements.values()
    ]


def handle_discover(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """GET /api/setup/discover/ -- Run multicast discovery scan.

    Lock-free endpoint: the wizard polls discover repeatedly and must
    not 409 while a sibling mutation (``select-manager``, ``enroll``)
    is in flight.  The discovery call itself is synchronous and may
    block the Waitress thread for up to ~5s; that is acceptable per
    FR-7 since discovery is a wizard-time operation and Waitress has
    multiple threads.
    """
    managers = _run_discovery()
    logger.info(
        "Setup wizard: discovery found %d manager(s)", len(managers),
    )
    return send_json_wsgi(start_response, {"managers": managers})


def handle_select_manager(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/worker/select-manager/ -- Store selection.

    Accepts either a full ``manager_url`` string (from manual entry
    or the HTML frontend) OR a discovery record with ``ip``/``host``
    and ``port`` fields.

    Mutation: wrapped in :func:`setup_mutation_lock` (409 on
    contention).  The three ``_selected_manager_*`` assignments are
    performed atomically under ``_state_lock`` so concurrent readers
    cannot observe a half-applied selection.  The checkpoint append
    runs outside ``_state_lock`` (checkpoint state is guarded by
    ``handlers_status``'s own lock -- no nested acquisition).
    """
    global _selected_manager_url, _selected_manager_id
    global _selected_manager_meta

    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(
                start_response,
                {"error": "Setup mutation in progress; retry after "
                          "current operation completes."},
                409,
            )

        data, err = parse_json_body_wsgi(environ)
        if err is not None:
            return send_json_wsgi(start_response, err[0], err[1])

        if not isinstance(data, dict):
            return send_json_wsgi(
                start_response,
                {"error": "Request body must be a JSON object"},
                400,
            )

        # Accept manager_url directly (spec FR-WA2), or build from
        # ip/host + port fields (discovery record).
        explicit_url = data.get("manager_url", "").strip()
        host = data.get("ip") or data.get("host")

        if explicit_url:
            new_url = explicit_url
        elif host:
            new_url = _build_manager_url(data)
        else:
            return send_json_wsgi(
                start_response,
                {"error": "'manager_url' or 'ip'/'host' is required."},
                400,
            )

        new_id = data.get("manager_id")
        # Atomic write of the (url, id, meta) triple.  Concurrent
        # readers either see all-old or all-new, never a mix.
        with _state_lock:
            _selected_manager_url = new_url
            _selected_manager_id = new_id
            _selected_manager_meta = data

        # Checkpoint append acquires handlers_status._state_lock;
        # keep it out of our own _state_lock to avoid nested
        # acquisition ordering concerns.  Still inside
        # setup_mutation_lock, so the overall mutation remains atomic
        # relative to other mutating endpoints.
        append_wizard_checkpoint("manager_selected")
        logger.info(
            "Setup wizard: selected manager at %s (id=%s)",
            new_url, new_id,
        )

        return send_json_wsgi(start_response, {
            "status": "ok",
            "manager_url": new_url,
            "manager_id": new_id,
        })
