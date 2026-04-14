# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard status and topology handlers.

GET  /api/setup/status/    — current wizard state
POST /api/setup/topology/  — set topology to ``worker_only``

Module-level variables track in-progress wizard state. This is safe
because uvicorn runs with ``--workers 1`` (single process).
"""

import logging

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, parse_json_body,
)
from sethlans_worker_agent.web_ui.setup.sentinel import read_sentinel

logger = logging.getLogger(__name__)

# ---- In-progress wizard state (module-level, single-process) ----
_current_topology: str | None = None
_current_checkpoints: list[str] = []


def get_current_topology() -> str | None:
    """Return the topology selected during the current wizard run."""
    return _current_topology


def get_current_checkpoints() -> list[str]:
    """Return a copy of the current wizard checkpoints."""
    return list(_current_checkpoints)


def append_wizard_checkpoint(name: str) -> None:
    """Append a checkpoint to the in-progress wizard state."""
    if name not in _current_checkpoints:
        _current_checkpoints.append(name)


async def handle_setup_status(scope, receive, send):
    """GET /api/setup/status/ -- Return current setup state."""
    data_dir = config_store.get_data_dir()
    sentinel = read_sentinel(data_dir)

    if sentinel is not None:
        await send_json(send, {
            "complete": True,
            "topology": sentinel.get("topology"),
            "checkpoints": sentinel.get("checkpoints", []),
        })
    else:
        await send_json(send, {
            "complete": False,
            "topology": _current_topology,
            "checkpoints": list(_current_checkpoints),
        })


async def handle_set_topology(scope, receive, send):
    """POST /api/setup/topology/ -- Set topology to worker_only."""
    global _current_topology

    data, err = await parse_json_body(receive)
    if err is not None:
        await send_json(send, err[0], err[1])
        return

    if not isinstance(data, dict):
        await send_json(
            send, {"error": "Request body must be a JSON object"}, 400,
        )
        return

    topology = data.get("topology", "")
    if topology != "worker_only":
        await send_json(
            send,
            {"error": "Invalid topology. Worker wizard supports "
                      "'worker_only' only."},
            400,
        )
        return

    _current_topology = topology
    append_wizard_checkpoint("topology_chosen")
    logger.info("Setup wizard: topology set to %s", topology)

    await send_json(send, {
        "status": "ok",
        "topology": topology,
        "checkpoints": list(_current_checkpoints),
    })
