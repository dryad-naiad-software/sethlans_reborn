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

Phase 4e of the Waitress migration: handlers are sync WSGI
``(environ, start_response) -> Iterable[bytes]`` callables.

Mutation endpoints (start-download, cancel) serialize through
:func:`setup_mutation_lock` for fail-fast 409 behaviour under
concurrent wizard POSTs.  The progress endpoint is a read-only
polling endpoint and deliberately does NOT take the mutation lock
-- the wizard polls it repeatedly and must never see a spinner
storm caused by an in-flight mutation elsewhere.
"""

import logging
import threading
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, parse_json_body_wsgi,
)
from sethlans_worker_agent.web_ui.setup.download_progress import (
    create_tagged_task,
    find_active_task,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup.lock import (
    setup_mutation_lock,
)

logger = logging.getLogger(__name__)

_BLENDER_TASK_PREFIX = "blender_"

_MUTATION_IN_PROGRESS = {
    "error": "Setup mutation in progress; retry after current "
             "operation completes.",
}


def handle_start_blender_download(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/blender/start-download/ -- Start download."""
    from sethlans_worker_agent.tool_manager import tool_manager_instance
    from sethlans_worker_agent.web_ui.setup.blender_download import (
        download_blender_with_progress,
    )

    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(
                start_response, _MUTATION_IN_PROGRESS, 409,
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

        version = data.get("version", "")
        if not version:
            # Default to the latest LTS Blender version when none
            # specified.
            from sethlans_worker_agent import config as worker_config
            version = getattr(
                worker_config, 'DEFAULT_BLENDER_VERSION', '4.2',
            )
            logger.info(
                "Setup wizard: no Blender version specified, "
                "defaulting to %s", version,
            )

        # Guard: already in progress?
        active = find_active_task(_BLENDER_TASK_PREFIX)
        if active is not None:
            tid, prog = active
            return send_json_wsgi(start_response, {
                "task_id": tid,
                "status": prog.status,
                "percent": prog.percent,
                "message": "Download already in progress.",
            })

        # Guard: already installed?
        full_version = tool_manager_instance._resolve_version(version)
        if full_version:
            exe = tool_manager_instance.get_blender_executable_path(
                full_version,
            )
            if exe:
                append_wizard_checkpoint("blender_installed")
                return send_json_wsgi(start_response, {
                    "status": "already_installed",
                    "version": full_version,
                })

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
        return send_json_wsgi(start_response, {
            "task_id": task_id,
            "status": "started",
        })


def handle_blender_progress(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """GET /api/setup/blender/progress/ -- Get download progress.

    Read-only polling endpoint.  Does NOT take the setup mutation
    lock -- the wizard polls this repeatedly during an in-flight
    download and must never 409 because another mutation is
    running.
    """
    active = find_active_task(_BLENDER_TASK_PREFIX)
    if active is not None:
        tid, prog = active
        return send_json_wsgi(start_response, {
            "task_id": tid,
            "status": prog.status,
            "percent": prog.percent,
            "error": prog.error,
        })

    # No active task.  Completed/failed tasks remain in the registry
    # until explicitly removed, but once the status is no longer in
    # the active set, the wizard treats progress as idle.
    return send_json_wsgi(start_response, {
        "task_id": None,
        "status": "idle",
        "percent": 0,
        "error": None,
    })


def handle_cancel_blender(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/blender/cancel/ -- Cancel active download."""
    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(
                start_response, _MUTATION_IN_PROGRESS, 409,
            )

        active = find_active_task(_BLENDER_TASK_PREFIX)
        if active is None:
            return send_json_wsgi(
                start_response,
                {"error": "No active download to cancel."},
                404,
            )

        tid, prog = active
        prog.cancel_event.set()
        logger.info(
            "Setup wizard: cancellation requested for task %s", tid,
        )
        return send_json_wsgi(
            start_response, {"status": "cancelling", "task_id": tid},
        )
