# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared HTTP helper functions for the worker ASGI application.

Extracted from ``asgi_app.py`` so that both the main app and the
setup wizard routes can send JSON/HTML responses and read request
bodies without duplicating code.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Maximum request body size for POST endpoints (4 KB).
MAX_REQUEST_BODY = 4096


async def send_json(send, data, status=200):
    """Send a JSON response with the given status code."""
    body = json.dumps(data).encode('utf-8')
    await send({
        'type': 'http.response.start',
        'status': status,
        'headers': [
            [b'content-type', b'application/json'],
            [b'content-length', str(len(body)).encode()],
        ],
    })
    await send({'type': 'http.response.body', 'body': body})


async def send_html_file(send, path):
    """Send an HTML file response, or 404 if missing."""
    try:
        with open(path, 'rb') as f:
            content = f.read()
    except FileNotFoundError:
        await send_json(send, {'error': 'Not Found'}, 404)
        return
    await send({
        'type': 'http.response.start',
        'status': 200,
        'headers': [
            [b'content-type', b'text/html; charset=utf-8'],
            [b'content-length', str(len(content)).encode()],
        ],
    })
    await send({'type': 'http.response.body', 'body': content})


async def read_body(receive):
    """Read the full request body from the ASGI receive channel."""
    body = b''
    while True:
        message = await receive()
        body += message.get('body', b'')
        if not message.get('more_body', False):
            break
    return body


async def parse_json_body(receive):
    """Read the request body, validate size, and parse JSON.

    Returns a ``(data, error_response)`` tuple.  On success,
    ``data`` is the parsed dict/list and ``error_response`` is
    ``None``.  On failure, ``data`` is ``None`` and
    ``error_response`` is a ``(dict, status_code)`` tuple suitable
    for passing to :func:`send_json`.
    """
    body_bytes = await read_body(receive)
    if len(body_bytes) > MAX_REQUEST_BODY:
        return None, ({'error': 'Request body too large'}, 413)
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return None, ({'error': 'Invalid JSON'}, 400)
    return data, None
