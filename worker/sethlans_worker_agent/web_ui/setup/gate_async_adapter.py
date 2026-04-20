# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 3 async-ASGI-inner -> sync-WSGI-out adapter.

Temporary scaffolding extracted from ``gate.py`` so the eventual
Phase 4 removal of this bridge is a single-file deletion.  Once
every inner handler is sync WSGI, :mod:`gate` will stop importing
from this module and this file can be ``rm``'d.

The public entry point is :func:`drive_async_inner_app_as_wsgi`;
the remaining helpers are module-private and kept here for
co-location with the single caller.
"""

import asyncio
from typing import Awaitable, Callable, Iterable, List


_REASON_PHRASES = {
    200: 'OK', 201: 'Created', 204: 'No Content', 400: 'Bad Request',
    401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
    409: 'Conflict', 413: 'Request Entity Too Large',
    500: 'Internal Server Error', 503: 'Service Unavailable',
}


def drive_async_inner_app_as_wsgi(
    async_inner_app: Callable[..., Awaitable[None]],
    environ: dict,
    start_response: Callable,
) -> Iterable[bytes]:
    """Drive an async ASGI ``inner_app`` from a sync WSGI context.

    Temporary Phase 3 scaffolding.  Runs the coroutine via
    :func:`asyncio.run` (fresh loop per request) and replays the
    captured ASGI response into WSGI.  Deleted in Phase 4.

    ``asyncio.run`` is safe under Waitress's threadpool: fresh
    loop per call, no signal handlers from non-main threads.
    Exceptions from the inner propagate unchanged.
    """
    scope = _environ_to_scope(environ)
    body = _read_environ_body(environ)
    receive = _make_single_chunk_receive(body)

    status_out: List[int] = []
    headers_out: List[tuple] = []
    body_out: List[bytes] = []
    send = _make_capture_send(status_out, headers_out, body_out)

    asyncio.run(async_inner_app(scope, receive, send))

    if not status_out:
        # Inner never sent a response -- treat as 500 rather than
        # silently returning an empty 200.
        start_response(
            '500 Internal Server Error',
            [('Content-Type', 'text/plain'), ('Content-Length', '0')],
        )
        return [b'']

    status = status_out[0]
    reason = _REASON_PHRASES.get(status, 'Status')
    headers = [
        (k.decode() if isinstance(k, bytes) else k,
         v.decode() if isinstance(v, bytes) else v)
        for k, v in headers_out
    ]
    start_response(f"{status} {reason}", headers)
    return body_out or [b'']


def _environ_to_scope(environ: dict) -> dict:
    """Convert a WSGI ``environ`` into a minimal ASGI HTTP scope."""
    headers = []
    for key, value in environ.items():
        if key.startswith('HTTP_'):
            name = key[5:].replace('_', '-').lower()
            headers.append(
                (name.encode('latin-1'),
                 str(value).encode('latin-1')),
            )
    for env_key, hdr_name in (
        ('CONTENT_TYPE', b'content-type'),
        ('CONTENT_LENGTH', b'content-length'),
    ):
        if environ.get(env_key):
            headers.append(
                (hdr_name, str(environ[env_key]).encode('latin-1')),
            )
    path = environ.get('PATH_INFO', '') or ''
    qs = (environ.get('QUERY_STRING', '') or '').encode('latin-1')
    return {
        'type': 'http',
        'http_version': '1.1',
        'method': environ.get('REQUEST_METHOD', 'GET'),
        'scheme': environ.get('wsgi.url_scheme', 'http'),
        'path': path,
        'raw_path': path.encode('latin-1'),
        'query_string': qs,
        'headers': headers,
    }


def _read_environ_body(environ: dict) -> bytes:
    """Read the full request body bounded by ``CONTENT_LENGTH``.

    Phase 3 adapter only; the 4 KB cap is enforced by the Phase 2
    sync helpers at the handler level, not here.
    """
    stream = environ.get('wsgi.input')
    if stream is None:
        return b''
    raw_len = environ.get('CONTENT_LENGTH', '')
    try:
        length = int(raw_len) if raw_len else 0
        if length < 0:
            length = 0
    except (TypeError, ValueError):
        length = 0
    if length == 0:
        return b''
    try:
        return stream.read(length) or b''
    except (TypeError, ValueError):
        return b''


def _make_single_chunk_receive(
    body: bytes,
) -> Callable[[], Awaitable[dict]]:
    """ASGI ``receive`` that yields *body* once, then disconnects."""
    sent: List[bool] = [False]

    async def receive() -> dict:
        if sent[0]:
            return {'type': 'http.disconnect'}
        sent[0] = True
        return {
            'type': 'http.request', 'body': body, 'more_body': False,
        }

    return receive


def _make_capture_send(
    status_out: List[int],
    headers_out: List[tuple],
    body_out: List[bytes],
) -> Callable[[dict], Awaitable[None]]:
    """ASGI ``send`` that records start + body messages.

    Ordering contract (documents the current, intentionally
    permissive behavior; the caller enforces the outward WSGI
    response shape):

    * ``http.response.body`` received before any
      ``http.response.start``: nothing is captured for that body
      message.  The caller sees ``status_out == []`` and emits a
      500 fallback -- this is how "inner never started a
      response" surfaces to WSGI.
    * Duplicate ``http.response.start``: the *first* status wins
      (the caller reads ``status_out[0]``).  Headers from every
      start event are appended in arrival order via
      ``list.extend``; WSGI sees the concatenation.  Well-behaved
      ASGI apps send at most one start, so this only matters for
      buggy inners -- documented rather than enforced because a
      stricter check would require an extra branch on every
      request in Phase-3 scaffolding that is deleted in Phase 4.
    """

    async def send(message: dict) -> None:
        msg_type = message.get('type')
        if msg_type == 'http.response.start':
            status_out.append(message.get('status', 200))
            headers_out.extend(message.get('headers', []) or [])
        elif msg_type == 'http.response.body':
            chunk = message.get('body', b'')
            if chunk:
                body_out.append(chunk)

    return send


__all__ = ['drive_async_inner_app_as_wsgi']
