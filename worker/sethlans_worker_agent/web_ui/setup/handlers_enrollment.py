# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard enrollment handler.

POST /api/setup/worker/enroll/ -- enroll with the selected manager.

Enrollment makes HTTP calls to the manager, so it is offloaded to a
thread-pool executor. On success the credentials are persisted via
``config_store.set_many()`` in the same pattern as the TTY wizard.
"""

import asyncio
import logging
from typing import Optional

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, parse_json_body,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    get_selected_manager_url,
    get_selected_manager_id,
    get_selected_manager_meta,
)

logger = logging.getLogger(__name__)


def _do_enroll(
    manager_url: str,
    enrollment_key: str,
    manager_id: Optional[str],
    manager_meta: Optional[dict],
) -> dict:
    """Run enrollment synchronously (blocking HTTP call).

    Returns ``{"status": "ok", "manager_name": ...,
    "default_blender_version": ...}`` on success or raises an
    ``enrollment_client.EnrollmentError`` subclass.
    """
    from sethlans_worker_agent import (
        enrollment_client, hardware_detection, config_store,
    )

    result = enrollment_client.enroll(
        manager_url, enrollment_key, hardware_detection.HOSTNAME,
    )

    # Persist credentials atomically -- mirrors wizard._persist_config
    pairs = [
        ("manager.api_token", result["api_token"]),
        ("manager.cert_fingerprint", result["cert_fingerprint"]),
        (
            "manager.manager_id",
            result.get("manager_id") or manager_id or "",
        ),
    ]
    if manager_meta:
        host = manager_meta.get("ip") or manager_meta.get("host")
        if host:
            pairs.append(("manager.host", host))
        port = manager_meta.get("port")
        if port:
            pairs.append(("manager.port", int(port)))

    config_store.set_many(pairs)

    # Include manager name from discovery metadata for UX.
    manager_name = (
        (manager_meta or {}).get("name")
        or "Manager"
    )
    return {
        "status": "ok",
        "manager_name": manager_name,
    }


async def handle_enroll(scope, receive, send):
    """POST /api/setup/worker/enroll/ -- Enroll with manager."""
    from sethlans_worker_agent import enrollment_client

    data, err = await parse_json_body(receive)
    if err is not None:
        await send_json(send, err[0], err[1])
        return

    if not isinstance(data, dict):
        await send_json(
            send, {"error": "Request body must be a JSON object"}, 400,
        )
        return

    enrollment_key = data.get("enrollment_key", "").strip()
    if not enrollment_key:
        await send_json(
            send, {"error": "'enrollment_key' is required."}, 400,
        )
        return

    manager_url = get_selected_manager_url()
    if not manager_url:
        await send_json(
            send,
            {"error": "No manager selected. Call "
                      "/api/setup/worker/select-manager/ first."},
            400,
        )
        return

    manager_id = get_selected_manager_id()
    manager_meta = get_selected_manager_meta()

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _do_enroll,
            manager_url,
            enrollment_key,
            manager_id,
            manager_meta,
        )
    except enrollment_client.InvalidKeyError as e:
        await send_json(send, {"error": str(e)}, 403)
        return
    except enrollment_client.RateLimitedError as e:
        await send_json(send, {"error": str(e)}, 429)
        return
    except enrollment_client.EnrollmentError as e:
        await send_json(send, {"error": str(e)}, 502)
        return

    append_wizard_checkpoint("enrolled")
    logger.info("Setup wizard: enrollment successful.")
    await send_json(send, result)
