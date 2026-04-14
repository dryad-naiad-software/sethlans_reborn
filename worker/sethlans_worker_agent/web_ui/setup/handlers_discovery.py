# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard manager discovery and selection handlers.

GET  /api/setup/discover/              — multicast discovery scan
POST /api/setup/worker/select-manager/ — store selected manager

The multicast listener blocks for several seconds, so it is
offloaded to a thread-pool executor via ``run_in_executor``.
"""

import asyncio
import logging

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, parse_json_body,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)

logger = logging.getLogger(__name__)

# ---- Selected manager state (module-level, single-process) ----
_selected_manager_url: str | None = None
_selected_manager_id: str | None = None
_selected_manager_meta: dict | None = None


def get_selected_manager_url() -> str | None:
    """Return the manager URL chosen during the wizard."""
    return _selected_manager_url


def get_selected_manager_id() -> str | None:
    """Return the manager_id chosen during the wizard."""
    return _selected_manager_id


def get_selected_manager_meta() -> dict | None:
    """Return the full manager metadata dict chosen during wizard."""
    return _selected_manager_meta


def _build_manager_url(manager: dict) -> str:
    """Build ``https://host:port/api/`` from a discovery record."""
    host = manager.get("ip") or manager.get("host") or "127.0.0.1"
    port = manager.get("port") or 8080
    return f"https://{host}:{port}/api/"


def _run_discovery() -> list[dict]:
    """Run multicast discovery synchronously (blocking).

    Returns a list of manager dicts suitable for JSON response.
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


async def handle_discover(scope, receive, send):
    """GET /api/setup/discover/ -- Run multicast discovery scan."""
    loop = asyncio.get_event_loop()
    managers = await loop.run_in_executor(None, _run_discovery)
    logger.info(
        "Setup wizard: discovery found %d manager(s)", len(managers),
    )
    await send_json(send, {"managers": managers})


async def handle_select_manager(scope, receive, send):
    """POST /api/setup/worker/select-manager/ -- Store selection.

    Accepts either a full ``manager_url`` string (from manual entry
    or the HTML frontend) OR a discovery record with ``ip``/``host``
    and ``port`` fields.
    """
    global _selected_manager_url, _selected_manager_id
    global _selected_manager_meta

    data, err = await parse_json_body(receive)
    if err is not None:
        await send_json(send, err[0], err[1])
        return

    if not isinstance(data, dict):
        await send_json(
            send, {"error": "Request body must be a JSON object"}, 400,
        )
        return

    # Accept manager_url directly (spec FR-WA2), or build from
    # ip/host + port fields (discovery record).
    explicit_url = data.get("manager_url", "").strip()
    host = data.get("ip") or data.get("host")

    if explicit_url:
        _selected_manager_url = explicit_url
    elif host:
        _selected_manager_url = _build_manager_url(data)
    else:
        await send_json(
            send,
            {"error": "'manager_url' or 'ip'/'host' is required."},
            400,
        )
        return

    _selected_manager_meta = data
    _selected_manager_id = data.get("manager_id")

    append_wizard_checkpoint("manager_selected")
    logger.info(
        "Setup wizard: selected manager at %s (id=%s)",
        _selected_manager_url, _selected_manager_id,
    )

    await send_json(send, {
        "status": "ok",
        "manager_url": _selected_manager_url,
        "manager_id": _selected_manager_id,
    })
