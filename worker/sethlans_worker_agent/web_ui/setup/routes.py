# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Route dispatcher for setup wizard endpoints.

Maps ``/api/setup/*`` API routes and ``/setup`` HTML to the
appropriate handler functions. Called from the main ASGI app when
the path matches setup prefixes.
"""

import logging
import os

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, send_html_file,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    handle_setup_status,
    handle_set_topology,
)
from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    handle_discover,
    handle_select_manager,
)
from sethlans_worker_agent.web_ui.setup.handlers_enrollment import (
    handle_enroll,
)
from sethlans_worker_agent.web_ui.setup.handlers_password import (
    handle_set_worker_password,
)
from sethlans_worker_agent.web_ui.setup.handlers_blender import (
    handle_start_blender_download,
    handle_blender_progress,
    handle_cancel_blender,
)
from sethlans_worker_agent.web_ui.setup.handlers_verify import (
    handle_verify,
)

logger = logging.getLogger(__name__)

# Path to the setup wizard HTML page
_SETUP_HTML = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'static',
    'setup.html',
)

# ---- GET route table ----
_GET_ROUTES = {
    '/api/setup/status/': handle_setup_status,
    '/api/setup/discover/': handle_discover,
    '/api/setup/blender/progress/': handle_blender_progress,
}

# ---- POST route table ----
_POST_ROUTES = {
    '/api/setup/topology/': handle_set_topology,
    '/api/setup/worker/select-manager/': handle_select_manager,
    '/api/setup/worker/enroll/': handle_enroll,
    '/api/setup/worker-password/': handle_set_worker_password,
    '/api/setup/blender/start-download/': handle_start_blender_download,
    '/api/setup/blender/cancel/': handle_cancel_blender,
    '/api/setup/verify/': handle_verify,
}


async def handle_setup_request(scope, receive, send):
    """Dispatch /api/setup/* and /setup routes."""
    path = scope['path']
    method = scope['method']

    # Serve wizard HTML
    if path in ('/setup', '/setup/'):
        await send_html_file(send, _SETUP_HTML)
        return

    if method == 'GET':
        handler = _GET_ROUTES.get(path)
        if handler is not None:
            await handler(scope, receive, send)
        else:
            await send_json(send, {"error": "Not Found"}, 404)
    elif method == 'POST':
        handler = _POST_ROUTES.get(path)
        if handler is not None:
            await handler(scope, receive, send)
        else:
            await send_json(send, {"error": "Not Found"}, 404)
    else:
        await send_json(send, {"error": "Method Not Allowed"}, 405)
