# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard Blender download handlers.

POST /api/setup/blender/start-download/ -- start background download
GET  /api/setup/blender/progress/       -- poll download progress
POST /api/setup/blender/cancel/         -- cancel active download

Downloads run in a background thread managed via the download
progress registry. The actual download delegates to
:mod:`blender_download` which wraps the existing ``tool_manager``.
"""

import logging
import threading

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, parse_json_body,
)
from sethlans_worker_agent.web_ui.setup.download_progress import (
    create_tagged_task,
    find_active_task,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)

logger = logging.getLogger(__name__)

_BLENDER_TASK_PREFIX = "blender_"


async def handle_start_blender_download(scope, receive, send):
    """POST /api/setup/blender/start-download/ -- Start download."""
    from sethlans_worker_agent.tool_manager import tool_manager_instance
    from sethlans_worker_agent.web_ui.setup.blender_download import (
        download_blender_with_progress,
    )

    data, err = await parse_json_body(receive)
    if err is not None:
        await send_json(send, err[0], err[1])
        return

    if not isinstance(data, dict):
        await send_json(
            send, {"error": "Request body must be a JSON object"}, 400,
        )
        return

    version = data.get("version", "")
    if not version:
        # Default to the latest LTS Blender version when none specified.
        from sethlans_worker_agent import config as worker_config
        version = getattr(worker_config, 'DEFAULT_BLENDER_VERSION', '4.2')
        logger.info(
            "Setup wizard: no Blender version specified, "
            "defaulting to %s", version,
        )

    # Guard: already in progress?
    active = find_active_task(_BLENDER_TASK_PREFIX)
    if active is not None:
        tid, prog = active
        await send_json(send, {
            "task_id": tid,
            "status": prog.status,
            "percent": prog.percent,
            "message": "Download already in progress.",
        })
        return

    # Guard: already installed?
    full_version = tool_manager_instance._resolve_version(version)
    if full_version:
        exe = tool_manager_instance.get_blender_executable_path(
            full_version,
        )
        if exe:
            append_wizard_checkpoint("blender_installed")
            await send_json(send, {
                "status": "already_installed",
                "version": full_version,
            })
            return

    task_id, _progress = create_tagged_task(_BLENDER_TASK_PREFIX)
    thread = threading.Thread(
        target=download_blender_with_progress,
        args=(task_id, version),
        name=f"blender-download-{task_id[:8]}",
        daemon=True,
    )
    thread.start()

    logger.info(
        "Setup wizard: started Blender download task %s for "
        "version %s",
        task_id, version,
    )
    await send_json(send, {
        "task_id": task_id,
        "status": "started",
    })


async def handle_blender_progress(scope, receive, send):
    """GET /api/setup/blender/progress/ -- Get download progress."""
    active = find_active_task(_BLENDER_TASK_PREFIX)
    if active is not None:
        tid, prog = active
        await send_json(send, {
            "task_id": tid,
            "status": prog.status,
            "percent": prog.percent,
            "error": prog.error,
        })
        return

    # No active task -- check for a recently completed/failed one
    # by looking up any blender_ task still in the registry.
    # (The registry retains completed tasks until explicitly removed.)
    await send_json(send, {
        "task_id": None,
        "status": "idle",
        "percent": 0,
        "error": None,
    })


async def handle_cancel_blender(scope, receive, send):
    """POST /api/setup/blender/cancel/ -- Cancel active download."""
    active = find_active_task(_BLENDER_TASK_PREFIX)
    if active is None:
        await send_json(
            send, {"error": "No active download to cancel."}, 404,
        )
        return

    tid, prog = active
    prog.cancel_event.set()
    logger.info("Setup wizard: cancellation requested for task %s", tid)
    await send_json(send, {"status": "cancelling", "task_id": tid})
