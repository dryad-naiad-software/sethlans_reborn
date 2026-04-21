# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard password handler.

POST /api/setup/worker-password/ -- set the worker UI password.

The password is hashed and stored via ``web_ui.auth.set_password``.
Minimum length is 8 characters (stricter than the operational
control endpoint which allows 4, matching the setup wizard UX).

Phase 4c of the Waitress migration: this handler is sync WSGI.
Its mutation path is guarded by :func:`setup_mutation_lock` so
concurrent wizard POSTs return ``409 Conflict`` rather than
racing the module-level state in ``handlers_status``.
"""

import logging
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

_MIN_PASSWORD_LENGTH = 8


def handle_set_worker_password(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/worker-password/ -- Set UI password."""
    from sethlans_worker_agent.web_ui.auth import set_password

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

        password = data.get("password", "")
        if not password or len(password) < _MIN_PASSWORD_LENGTH:
            return send_json_wsgi(
                start_response,
                {"error": f"Password must be at least "
                          f"{_MIN_PASSWORD_LENGTH} characters."},
                400,
            )

        set_password(password)
        append_wizard_checkpoint("password_set")
        logger.info("Setup wizard: UI password has been set.")

        return send_json_wsgi(start_response, {"status": "ok"})
