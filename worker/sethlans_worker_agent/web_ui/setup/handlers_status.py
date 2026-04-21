# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard status and topology handlers.

GET  /api/setup/status/    -- current wizard state
POST /api/setup/topology/  -- set topology to ``worker_only``

Module-level state is guarded by ``_state_lock`` to support threaded
WSGI servers (Waitress).  All reads and writes of ``_current_topology``
and ``_current_checkpoints`` -- including sibling-handler accessors
``get_current_topology``, ``get_current_checkpoints``, and
``append_wizard_checkpoint`` -- go through the lock, so concurrent
request threads cannot observe torn state or double-append a
checkpoint via a TOCTOU race.

Mutation endpoints additionally serialize through
:func:`setup_mutation_lock` for fail-fast 409 behavior under
concurrent wizard POSTs (Phase 4a pattern, matching
``handlers_password`` / ``handlers_verify``).  Reads do NOT take the
mutation lock -- they only take ``_state_lock`` for a brief snapshot.
"""

import logging
import threading
from typing import Callable, Iterable

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, parse_json_body_wsgi,
)
from sethlans_worker_agent.web_ui.setup.lock import (
    setup_mutation_lock,
)
from sethlans_worker_agent.web_ui.setup.sentinel import read_sentinel

logger = logging.getLogger(__name__)

# ---- In-progress wizard state (guarded by _state_lock) ----
_state_lock = threading.Lock()
_current_topology: str | None = None
_current_checkpoints: list[str] = []


def get_current_topology() -> str | None:
    """Return the topology selected during the current wizard run."""
    with _state_lock:
        return _current_topology


def get_current_checkpoints() -> list[str]:
    """Return a defensive copy of the current wizard checkpoints."""
    with _state_lock:
        return list(_current_checkpoints)


def append_wizard_checkpoint(name: str) -> None:
    """Append a checkpoint to the in-progress wizard state.

    The membership check and append happen inside a single critical
    section so two concurrent callers with the same ``name`` cannot
    both pass the ``not in`` check and double-append (TOCTOU fix).
    """
    with _state_lock:
        if name not in _current_checkpoints:
            _current_checkpoints.append(name)


def handle_setup_status(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """GET /api/setup/status/ -- Return current setup state."""
    data_dir = config_store.get_data_dir()
    sentinel = read_sentinel(data_dir)

    if sentinel is not None:
        return send_json_wsgi(start_response, {
            "complete": True,
            "topology": sentinel.get("topology"),
            "checkpoints": sentinel.get("checkpoints", []),
        })

    # No sentinel: snapshot in-progress state under the lock, then
    # release before serializing the response.  Never call
    # send_json_wsgi while holding _state_lock.
    with _state_lock:
        topology_snapshot = _current_topology
        checkpoints_snapshot = list(_current_checkpoints)

    return send_json_wsgi(start_response, {
        "complete": False,
        "topology": topology_snapshot,
        "checkpoints": checkpoints_snapshot,
    })


def handle_set_topology(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/topology/ -- Set topology to worker_only."""
    global _current_topology

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

        topology = data.get("topology", "")
        if topology != "worker_only":
            return send_json_wsgi(
                start_response,
                {"error": "Invalid topology. Worker wizard supports "
                          "'worker_only' only."},
                400,
            )

        with _state_lock:
            _current_topology = topology

        # append_wizard_checkpoint / get_current_checkpoints each
        # acquire _state_lock themselves -- call them after releasing
        # the inner critical section to avoid nested acquisition.
        # Still inside setup_mutation_lock so the overall mutation is
        # atomic vs. other mutating endpoints.
        append_wizard_checkpoint("topology_chosen")
        logger.info("Setup wizard: topology set to %s", topology)

        return send_json_wsgi(start_response, {
            "status": "ok",
            "topology": topology,
            "checkpoints": get_current_checkpoints(),
        })
