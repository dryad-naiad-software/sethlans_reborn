# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sync WSGI route dispatcher for setup wizard endpoints.

Maps ``/api/setup/*`` API routes and ``/setup`` HTML to the
appropriate handler functions. All handlers are sync WSGI callables
with the ``(environ, start_response)`` signature.
"""

import logging
import os
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, send_html_file_wsgi,
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


def handle_setup_request_wsgi(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch ``/api/setup/*`` and ``/setup`` routes as WSGI."""
    path = environ.get('PATH_INFO', '') or ''
    method = environ.get('REQUEST_METHOD', 'GET')

    # Serve wizard HTML
    if path in ('/setup', '/setup/'):
        return send_html_file_wsgi(start_response, _SETUP_HTML)

    if method == 'GET':
        handler = _GET_ROUTES.get(path)
        if handler is not None:
            return handler(environ, start_response)
        return send_json_wsgi(start_response, {"error": "Not Found"}, 404)

    if method == 'POST':
        handler = _POST_ROUTES.get(path)
        if handler is not None:
            return handler(environ, start_response)
        return send_json_wsgi(start_response, {"error": "Not Found"}, 404)

    return send_json_wsgi(
        start_response, {"error": "Method Not Allowed"}, 405,
    )


__all__ = ['handle_setup_request_wsgi']
