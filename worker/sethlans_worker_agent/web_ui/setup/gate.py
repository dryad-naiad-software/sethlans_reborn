# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setup gate middleware.

Blocks non-setup HTTP paths with 503 during setup.  Once setup is
complete, fast-paths every request with a single bool check.

During the Waitress migration this module exposes two dispatcher
entry points backed by shared module-level state:

* :func:`setup_gate_wrapper` -- legacy async ASGI wrapper.  Still
  called by ``asgi_app.py`` in Phase 3.  Dead code after Phase 4;
  deleted in Phase 7 per spec.
* :func:`setup_gate_wrapper_wsgi` -- new sync WSGI dispatcher.
  Wired up at the ``server.py`` boundary in Phase 4.

The sync dispatcher accepts either a sync WSGI or async ASGI
``inner_app``.  Async inners are driven via a temporary
``asyncio.run`` adapter imported from
:mod:`.gate_async_adapter` -- Phase-3-only scaffolding, removed
in Phase 4 by deleting that sibling module.
"""

import functools
import inspect
import logging
from pathlib import Path
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers import send_json
from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi
from sethlans_worker_agent.web_ui.setup.gate_async_adapter import (
    drive_async_inner_app_as_wsgi,
)
from sethlans_worker_agent.web_ui.setup.sentinel import is_setup_complete

logger = logging.getLogger(__name__)

_setup_complete: bool = False

_SETUP_ALLOWED_PREFIXES = ("/api/setup/", "/setup")


# --- Shared state helpers (used by async + sync dispatchers) ---

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


def _path_allowed(path: str) -> bool:
    """Return True if *path* is under an allowed setup prefix."""
    return any(path.startswith(p) for p in _SETUP_ALLOWED_PREFIXES)


# --- Legacy async ASGI wrapper (deleted in Phase 7) ---

async def setup_gate_wrapper(scope, receive, send, inner_app):
    """ASGI wrapper that blocks non-setup paths during setup.

    Superseded by :func:`setup_gate_wrapper_wsgi`.  Retained for
    Phase 3 because ``asgi_app.py`` still calls it.  Scheduled
    for deletion in Phase 7.
    """
    if _setup_complete:
        await inner_app(scope, receive, send)
        return

    # Non-HTTP scopes (lifespan, websocket) pass through.
    if scope['type'] != 'http':
        await inner_app(scope, receive, send)
        return

    path = scope.get('path', '')
    if _path_allowed(path):
        await inner_app(scope, receive, send)
        return

    await send_json(send, {"detail": "Setup not complete."}, 503)


# --- Sync WSGI dispatcher (wired up at server.py in Phase 4) ---

def setup_gate_wrapper_wsgi(
    environ: dict,
    start_response: Callable,
    inner_app: Callable,
) -> Iterable[bytes]:
    """Sync WSGI dispatcher: blocks non-setup paths during setup.

    *inner_app* may be a sync WSGI callable or an async ASGI
    callable.  Async inners are driven via
    :func:`.gate_async_adapter.drive_async_inner_app_as_wsgi`
    (Phase-3-only ``asyncio.run``-based scaffolding).
    """
    if _setup_complete:
        return _call_inner(inner_app, environ, start_response)

    path = environ.get('PATH_INFO', '') or ''
    if _path_allowed(path):
        return _call_inner(inner_app, environ, start_response)

    return send_json_wsgi(
        start_response, {"detail": "Setup not complete."}, 503,
    )


def _call_inner(
    inner_app: Callable,
    environ: dict,
    start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch to sync WSGI or async ASGI *inner_app*."""
    if _is_async_callable(inner_app):
        return drive_async_inner_app_as_wsgi(
            inner_app, environ, start_response,
        )
    return inner_app(environ, start_response)


def _is_async_callable(fn: Callable) -> bool:
    """True if *fn* (or its ``__call__``) is a coroutine function.

    Unwraps :class:`functools.partial` before checking so that
    ``partial(async_fn, arg)`` is still recognized as async --
    :func:`inspect.iscoroutinefunction` does not look through
    ``partial`` itself.
    """
    while isinstance(fn, functools.partial):
        fn = fn.func
    if inspect.iscoroutinefunction(fn):
        return True
    call = getattr(fn, '__call__', None)
    return call is not None and inspect.iscoroutinefunction(call)


__all__ = [
    'init_gate', 'is_in_setup_mode', 'mark_setup_complete',
    'setup_gate_wrapper', 'setup_gate_wrapper_wsgi',
]
