# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard password handler.

POST /api/setup/worker-password/ -- set the worker UI password.

The password is hashed and stored via ``web_ui.auth.set_password``.
Minimum length is 8 characters (stricter than the operational
control endpoint which allows 4, matching the setup wizard UX).
"""

import logging

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, parse_json_body,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)

logger = logging.getLogger(__name__)

_MIN_PASSWORD_LENGTH = 8


async def handle_set_worker_password(scope, receive, send):
    """POST /api/setup/worker-password/ -- Set UI password."""
    from sethlans_worker_agent.web_ui.auth import set_password

    data, err = await parse_json_body(receive)
    if err is not None:
        await send_json(send, err[0], err[1])
        return

    if not isinstance(data, dict):
        await send_json(
            send, {"error": "Request body must be a JSON object"}, 400,
        )
        return

    password = data.get("password", "")
    if not password or len(password) < _MIN_PASSWORD_LENGTH:
        await send_json(
            send,
            {"error": f"Password must be at least "
                      f"{_MIN_PASSWORD_LENGTH} characters."},
            400,
        )
        return

    set_password(password)
    append_wizard_checkpoint("password_set")
    logger.info("Setup wizard: UI password has been set.")

    await send_json(send, {"status": "ok"})
