# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared WSGI helpers for the ``handlers_status`` test suite.

Phase 4d of the Waitress migration: ``handle_setup_status`` /
``handle_set_topology`` are sync WSGI callables driven directly
via an ``environ`` + ``start_response`` pair.  Extracted so the
suite can be split across multiple files without duplicating
boilerplate.
"""

import io

from sethlans_worker_agent.web_ui.setup.handlers_status import (
    handle_setup_status,
    handle_set_topology,
)

from tests.unit.worker._wsgi_helpers import StartResponseCapture


def environ_get() -> dict:
    """Minimal WSGI environ for a GET request."""
    return {
        'REQUEST_METHOD': 'GET',
        'PATH_INFO': '/api/setup/status/',
        'wsgi.input': io.BytesIO(b''),
        'CONTENT_LENGTH': '0',
    }


def environ_post(body: bytes) -> dict:
    """Minimal WSGI environ for a POST with ``body``."""
    return {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/setup/topology/',
        'CONTENT_LENGTH': str(len(body)),
        'wsgi.input': io.BytesIO(body),
    }


def call_status(cap: StartResponseCapture) -> bytes:
    return b''.join(handle_setup_status(environ_get(), cap))


def call_topology(body: bytes, cap: StartResponseCapture) -> bytes:
    return b''.join(handle_set_topology(environ_post(body), cap))


def status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])
