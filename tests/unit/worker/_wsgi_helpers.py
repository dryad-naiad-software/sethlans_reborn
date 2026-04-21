# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared WSGI test helpers for the worker setup wizard unit tests.

Provides minimal ``start_response`` captures and environ builders
used across the sync WSGI worker unit tests.  Extracted so test
suites can be split across multiple files without duplicating the
helpers.
"""

import io
from typing import Any, Dict, List, Tuple


def environ(body: bytes, include_length: bool = True) -> dict:
    """Build a minimal WSGI environ around ``body``."""
    env = {'wsgi.input': io.BytesIO(body)}
    if include_length:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


class StartResponseCapture:
    """Minimal ``start_response`` callable that records its args."""

    def __init__(self):
        self.status = None
        self.headers = None

    def __call__(self, status, headers, exc_info=None):
        self.status = status
        self.headers = headers

    def header_dict(self):
        return {k.lower(): v for k, v in (self.headers or [])}


class ReadSpyStream:
    """Minimal BytesIO-like stream that records every ``read`` call.

    Used to verify that ``read_body_wsgi`` never issues a negative
    read size -- a negative size on a fully-buffered WSGI input
    would drain the entire buffered body and bypass the cap.
    """

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.read_calls: list = []

    def read(self, size=-1):
        self.read_calls.append(size)
        return self._buf.read(size)


# --- Helpers for the asgi_app sync WSGI tests --------------------

def make_environ(
    path: str = '/',
    method: str = 'GET',
    body: bytes = b'',
    headers: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Build a richer WSGI environ for the top-level worker app.

    Supports arbitrary paths, methods, and HTTP headers.  Used by
    the split ``test_asgi_app_wsgi*`` files.
    """
    env: Dict[str, Any] = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'QUERY_STRING': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8080',
        'wsgi.url_scheme': 'https',
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    for name, value in (headers or {}).items():
        if name.lower() in ('content-type', 'content-length'):
            env[name.upper().replace('-', '_')] = value
        else:
            env['HTTP_' + name.upper().replace('-', '_')] = value
    return env


class StartResponseRecorder:
    """Captures WSGI ``start_response`` invocations for assertion.

    Richer than ``StartResponseCapture`` -- exposes an int
    ``status_code`` and a case-insensitive ``header()`` lookup.
    """

    def __init__(self) -> None:
        self.status: str = ''
        self.headers: List[Tuple[str, str]] = []

    def __call__(
        self, status: str, headers: List[Tuple[str, str]], exc_info=None,
    ) -> None:
        self.status = status
        self.headers = list(headers)

    @property
    def status_code(self) -> int:
        return int(self.status.split(' ', 1)[0])

    def header(self, name: str) -> str:
        lower = name.lower()
        for k, v in self.headers:
            if k.lower() == lower:
                return v
        return ''


def drain(iterable) -> bytes:
    """Concatenate a WSGI response body iterable into one bytes blob."""
    return b''.join(iterable)
