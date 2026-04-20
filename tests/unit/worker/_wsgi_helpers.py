# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared WSGI test helpers for the worker setup wizard unit tests.

Provides a minimal ``start_response`` capture used by the
``http_helpers_wsgi`` tests.  Extracted so the test suite can be
split across multiple files without duplicating the helper.
"""

import io


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
