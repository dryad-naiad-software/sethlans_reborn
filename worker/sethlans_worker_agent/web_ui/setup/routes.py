# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sync WSGI route dispatcher for setup wizard endpoints.

Phase 4b of the Waitress migration: this module was historically an
async ASGI dispatcher. It is now a sync WSGI dispatcher. Individual
handler functions are still async during Phase 4b; the dispatcher
detects async handlers via :func:`inspect.iscoroutinefunction` and
bridges them via
:func:`gate_async_adapter.drive_async_inner_app_as_wsgi` (the same
``asyncio.run``-based scaffolding used by the setup gate). As each
handler is flipped to sync WSGI in Phases 4c-4g, the route table
entry is unchanged -- the auto-detect dispatch calls it directly.

Maps ``/api/setup/*`` API routes and ``/setup`` HTML to the
appropriate handler functions.
"""

import functools
import inspect
import logging
import os
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, send_html_file_wsgi,
)
from sethlans_worker_agent.web_ui.setup.gate_async_adapter import (
    drive_async_inner_app_as_wsgi,
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


def _is_async_callable(fn: Callable) -> bool:
    """True if *fn* (or its ``__call__``) is a coroutine function.

    Unwraps :class:`functools.partial` before checking so that
    ``partial(async_fn, arg)`` is still recognised. Mirrors the
    gate's helper of the same name; kept local so routes.py can be
    deleted or further refactored without cross-module churn.
    """
    while isinstance(fn, functools.partial):
        fn = fn.func
    if inspect.iscoroutinefunction(fn):
        return True
    call = getattr(fn, '__call__', None)
    return call is not None and inspect.iscoroutinefunction(call)


def _dispatch_handler(
    handler: Callable,
    environ: dict,
    start_response: Callable,
) -> Iterable[bytes]:
    """Call *handler* either directly (sync WSGI) or via adapter.

    During Phase 4b every handler in the route tables is still
    ``async def`` with the ASGI ``(scope, receive, send)`` signature,
    so every call goes through the async-to-sync adapter. As Phases
    4c-4g flip handlers to sync WSGI ``(environ, start_response)``,
    those handlers are dispatched directly without touching this
    file.
    """
    if _is_async_callable(handler):
        return drive_async_inner_app_as_wsgi(
            handler, environ, start_response,
        )
    return handler(environ, start_response)


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
            return _dispatch_handler(handler, environ, start_response)
        return send_json_wsgi(start_response, {"error": "Not Found"}, 404)

    if method == 'POST':
        handler = _POST_ROUTES.get(path)
        if handler is not None:
            return _dispatch_handler(handler, environ, start_response)
        return send_json_wsgi(start_response, {"error": "Not Found"}, 404)

    return send_json_wsgi(
        start_response, {"error": "Method Not Allowed"}, 405,
    )


__all__ = ['handle_setup_request_wsgi']
