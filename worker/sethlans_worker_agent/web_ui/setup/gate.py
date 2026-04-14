# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup gate ASGI wrapper.

Blocks non-setup HTTP paths with 503 during setup.  Once setup is
complete (sentinel exists), the gate fast-paths every request with
a single bool check and zero overhead.

The gate runs as middleware wrapping the main ASGI app.  During
setup, only ``/api/setup/*`` and ``/setup`` paths are allowed
through; all others receive a 503 JSON response.
"""

import logging
from pathlib import Path

from sethlans_worker_agent.web_ui.http_helpers import send_json
from sethlans_worker_agent.web_ui.setup.sentinel import is_setup_complete

logger = logging.getLogger(__name__)

_setup_complete: bool = False

_SETUP_ALLOWED_PREFIXES = ("/api/setup/", "/setup")


def init_gate(data_dir: Path) -> None:
    """Read sentinel to set initial state. Called at startup."""
    global _setup_complete
    _setup_complete = is_setup_complete(data_dir)
    if _setup_complete:
        logger.debug("Setup gate: setup already complete.")
    else:
        logger.info(
            "Setup gate: setup not complete, "
            "blocking non-setup requests."
        )


def is_in_setup_mode() -> bool:
    """Return ``True`` when the wizard has not finished yet."""
    return not _setup_complete


def mark_setup_complete() -> None:
    """Flip the gate open. Called after successful verification."""
    global _setup_complete
    _setup_complete = True
    logger.info("Setup gate: setup complete, all routes enabled.")


async def setup_gate_wrapper(scope, receive, send, inner_app):
    """ASGI wrapper that blocks non-setup paths during setup.

    Fast path when complete: a single bool check, then delegate
    to the inner app immediately.
    """
    if _setup_complete:
        await inner_app(scope, receive, send)
        return

    # Non-HTTP scopes (lifespan, websocket) pass through.
    if scope['type'] != 'http':
        await inner_app(scope, receive, send)
        return

    path = scope.get('path', '')
    if any(path.startswith(p) for p in _SETUP_ALLOWED_PREFIXES):
        await inner_app(scope, receive, send)
        return

    await send_json(send, {"detail": "Setup not complete."}, 503)
