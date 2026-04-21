# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared ASGI test helpers for transitional phases 3-6.

Provides mock scope/receive/send/ResponseCollector callables used by:
- The legacy async ASGI tests (test_http_helpers_legacy.py, test_setup_gate_legacy.py)
- The gate adapter tests (test_setup_gate_wsgi*.py, test_asgi_app_wsgi.py) that verify setup_gate_wrapper_wsgi
  correctly drives async inner apps via asyncio.run until Phase 7 deletes gate_async_adapter.

`async def` usage in this module is intentional (the fixtures ARE the async API being driven). This file is
deleted in Phase 7 alongside gate_async_adapter.py when the async-inner-app code path goes away.
"""

import json


def make_scope(method='GET', path='/', headers=None):
    """Create a mock ASGI HTTP scope."""
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'headers': headers or [],
    }


def make_receive(body=b''):
    """Create a mock ASGI receive callable.

    For bodies larger than a single chunk, pass the full body;
    only a single chunk with ``more_body=False`` is returned.
    """
    async def receive():
        return {'body': body, 'more_body': False}
    return receive


def make_receive_chunked(chunks):
    """Create a multi-chunk receive callable.

    *chunks* is a list of ``bytes`` objects.  The last chunk sets
    ``more_body=False``; all earlier ones set ``more_body=True``.
    """
    queue = list(chunks)
    idx = [0]

    async def receive():
        i = idx[0]
        idx[0] += 1
        if i < len(queue):
            more = i < len(queue) - 1
            return {'body': queue[i], 'more_body': more}
        return {'body': b'', 'more_body': False}

    return receive


class ResponseCollector:
    """Collects ASGI send() calls for assertion."""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b''

    async def __call__(self, message):
        if message['type'] == 'http.response.start':
            self.status = message['status']
            for k, v in message.get('headers', []):
                if isinstance(k, bytes):
                    k = k.decode()
                if isinstance(v, bytes):
                    v = v.decode()
                self.headers[k] = v
        elif message['type'] == 'http.response.body':
            self.body += message.get('body', b'')

    @property
    def json(self):
        """Parse the response body as JSON."""
        return json.loads(self.body)

    @property
    def text(self):
        """Decode the response body as UTF-8 text."""
        return self.body.decode('utf-8')
