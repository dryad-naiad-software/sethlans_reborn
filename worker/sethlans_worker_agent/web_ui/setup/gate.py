# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setup gate middleware.

Blocks non-setup HTTP paths with 503 during setup.  Once setup is
complete, fast-paths every request with a single bool check.

Exposes :func:`setup_gate_wrapper_wsgi`, a sync WSGI dispatcher
wired up at the ``server.py`` boundary, plus the shared state
helpers (:func:`init_gate`, :func:`is_in_setup_mode`,
:func:`mark_setup_complete`).
"""

import logging
from pathlib import Path
from typing import Callable, Iterable

from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi
from sethlans_worker_agent.web_ui.setup.sentinel import is_setup_complete

logger = logging.getLogger(__name__)

_setup_complete: bool = False

# Prefixes whose paths bypass the setup gate (returned 200/handler
# responses instead of 503).  ``/api/setup/`` and ``/setup`` host the
# wizard itself; ``/api/health/`` is the always-on anonymous probe that
# Docker HEALTHCHECK and the standalone wizard's worker-only redirect
# probe both need to reach in setup mode (see worker-health-endpoint
# spec FR-W-2).  Per the spec's Caddyfile invariant, ``/api/health/``
# also stays outside the worker Caddyfile's ``@setup_paths`` matcher
# so the route is reverse-proxied to Waitress as-is.
_SETUP_ALLOWED_PREFIXES = ("/api/setup/", "/setup", "/api/health/")


# --- Shared state helpers ---

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


# --- Sync WSGI dispatcher ---

def setup_gate_wrapper_wsgi(
    environ: dict,
    start_response: Callable,
    inner_app: Callable,
) -> Iterable[bytes]:
    """Sync WSGI dispatcher: blocks non-setup paths during setup.

    *inner_app* is a sync WSGI callable.
    """
    if _setup_complete:
        return inner_app(environ, start_response)

    path = environ.get('PATH_INFO', '') or ''
    if _path_allowed(path):
        return inner_app(environ, start_response)

    return send_json_wsgi(
        start_response, {"detail": "Setup not complete."}, 503,
    )


__all__ = [
    'init_gate', 'is_in_setup_mode', 'mark_setup_complete',
    'setup_gate_wrapper_wsgi',
]
