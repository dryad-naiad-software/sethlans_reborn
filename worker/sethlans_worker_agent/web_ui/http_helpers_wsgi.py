# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Synchronous WSGI equivalents of the worker ASGI helpers.

These helpers are the WSGI counterparts of the async helpers in
:mod:`http_helpers`.  Both modules coexist during the Waitress
migration: legacy async handlers continue to use the async helpers;
handlers rewritten to WSGI (Phase 4+) import from this module.

Invariants preserved byte-equivalent to the async helpers:

* The 4 KB ``MAX_REQUEST_BODY`` cap is reused from the async module
  (single source of truth) and enforced on every JSON parse.
* Oversize bodies produce ``413`` with ``{'error': 'Request body too
  large'}``.
* Malformed JSON (including empty bodies) produces ``400`` with
  ``{'error': 'Invalid JSON'}``.
* Response bodies are UTF-8 JSON; ``Content-Type`` and
  ``Content-Length`` are set exactly like the async versions.

Safety: ``read_body_wsgi`` never reads more than
``MAX_REQUEST_BODY + 1`` bytes from ``wsgi.input``.  If
``CONTENT_LENGTH`` is present and already exceeds the cap, the read
is skipped entirely (short-circuit).  This prevents memory
exhaustion from hostile large POSTs even if Waitress's
``max_request_body_size`` is misconfigured.
"""

import json
import logging
from typing import Any, Callable, Iterable, Optional, Tuple

from sethlans_worker_agent.web_ui.http_helpers import MAX_REQUEST_BODY

logger = logging.getLogger(__name__)


# Status line mapping for the few codes this module emits.  Kept
# tiny on purpose -- add entries as new callers need them.
_STATUS_LINES = {
    200: '200 OK',
    400: '400 Bad Request',
    404: '404 Not Found',
    409: '409 Conflict',
    413: '413 Request Entity Too Large',
}


def _status_line(status: int) -> str:
    """Return a ``"<code> <reason>"`` status line for ``status``."""
    try:
        return _STATUS_LINES[status]
    except KeyError:
        # Fallback for unmapped codes: still valid HTTP, reason
        # phrase is informational only.
        return f'{status} Status'


def send_json_wsgi(
    start_response: Callable,
    data: Any,
    status: int = 200,
) -> Iterable[bytes]:
    """Send a JSON WSGI response.

    Calls ``start_response`` with the correct status line and
    ``Content-Type`` / ``Content-Length`` headers, then returns an
    iterable of body bytes.
    """
    body = json.dumps(data).encode('utf-8')
    headers = [
        ('Content-Type', 'application/json'),
        ('Content-Length', str(len(body))),
    ]
    start_response(_status_line(status), headers)
    return [body]


def send_html_file_wsgi(
    start_response: Callable,
    path: str,
) -> Iterable[bytes]:
    """Send an HTML file as a WSGI response, or 404 JSON if missing."""
    try:
        with open(path, 'rb') as f:
            content = f.read()
    except FileNotFoundError:
        return send_json_wsgi(
            start_response, {'error': 'Not Found'}, 404,
        )
    headers = [
        ('Content-Type', 'text/html; charset=utf-8'),
        ('Content-Length', str(len(content))),
    ]
    start_response(_status_line(200), headers)
    return [content]


def read_body_wsgi(environ: dict) -> bytes:
    """Read the request body from ``environ['wsgi.input']``.

    Respects ``CONTENT_LENGTH`` when present but never reads more
    than ``MAX_REQUEST_BODY + 1`` bytes in total.  Reading one byte
    beyond the cap lets the caller detect oversize bodies without
    pulling a potentially hostile multi-gigabyte payload into
    memory.

    If ``CONTENT_LENGTH`` parses and already exceeds the cap, the
    function short-circuits and returns that many bytes' worth of
    a sentinel (``MAX_REQUEST_BODY + 1`` empty-ish bytes) -- the
    caller only cares about the length exceeding the cap, not the
    bytes.  We do still issue a zero-byte read to keep
    ``wsgi.input`` semantics clean on some servers.
    """
    stream = environ.get('wsgi.input')
    if stream is None:
        return b''

    # Parse CONTENT_LENGTH defensively.  Missing or malformed ->
    # treat as unknown length (chunked or absent header).  A
    # negative value is nonsensical but would otherwise parse
    # successfully and (because ``min(-1, cap+1) == -1``) cause
    # ``stream.read(-1)`` to drain the entire buffered body,
    # bypassing our cap.  Treat it as malformed so we fall into
    # the read-one-beyond-cap branch below.
    raw_len = environ.get('CONTENT_LENGTH', '')
    content_length: Optional[int]
    try:
        content_length = int(raw_len) if raw_len else None
        if content_length is not None and content_length < 0:
            content_length = None
    except (TypeError, ValueError):
        content_length = None

    # Short-circuit when the declared length already blows the cap.
    # Return MAX_REQUEST_BODY + 1 bytes of filler so the caller's
    # len(body) > MAX_REQUEST_BODY check fires without ever pulling
    # the oversize payload off the socket.
    if content_length is not None and content_length > MAX_REQUEST_BODY:
        return b'\x00' * (MAX_REQUEST_BODY + 1)

    # Read at most cap+1 bytes so oversize bodies are detectable
    # without buffering the whole payload.
    read_limit = MAX_REQUEST_BODY + 1
    if content_length is not None:
        # Honour the shorter of the declared length and our cap.
        read_limit = min(content_length, MAX_REQUEST_BODY + 1)

    # Relies on Waitress's fully-buffered wsgi.input: a single
    # read(n) returns exactly min(n, body_len).  A streaming WSGI
    # adapter (e.g., gunicorn sync worker) would need a read loop
    # to enforce the cap correctly.
    try:
        data = stream.read(read_limit)
    except (TypeError, ValueError):
        return b''
    return data or b''


def parse_json_body_wsgi(
    environ: dict,
) -> Tuple[Optional[Any], Optional[Tuple[dict, int]]]:
    """Read the WSGI body, validate size, and parse JSON.

    Returns ``(data, error_response)`` mirroring the async
    :func:`http_helpers.parse_json_body` contract:

    * On success, ``data`` is the parsed JSON and
      ``error_response`` is ``None``.
    * On oversize, returns
      ``(None, ({'error': 'Request body too large'}, 413))``.
    * On parse failure (including empty body), returns
      ``(None, ({'error': 'Invalid JSON'}, 400))``.
    """
    body_bytes = read_body_wsgi(environ)
    if len(body_bytes) > MAX_REQUEST_BODY:
        return None, ({'error': 'Request body too large'}, 413)
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return None, ({'error': 'Invalid JSON'}, 400)
    return data, None
