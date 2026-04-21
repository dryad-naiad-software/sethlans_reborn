# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Byte-equivalence cross-checks between the sync WSGI helpers (http_helpers_wsgi.py, current) and the legacy
async ASGI helpers (http_helpers.py, to be deleted in Phase 7). asyncio.run usage is the reference harness
driving the async side; intentional, not a scrub miss. This entire file is deleted alongside http_helpers.py
in Phase 7."""

import asyncio
import io
import json

from sethlans_worker_agent.web_ui.http_helpers import (
    MAX_REQUEST_BODY,
    parse_json_body,
    send_json,
)
from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    parse_json_body_wsgi,
    send_json_wsgi,
)

from tests.unit.worker._asgi_helpers import (
    ResponseCollector,
    make_receive,
)
from tests.unit.worker._wsgi_helpers import (
    ReadSpyStream,
    StartResponseCapture,
    environ as _environ,
)


# -------------------------------------------------------------------
# Byte-equivalence with the async helpers (FR-4 lock-in)
# -------------------------------------------------------------------

class TestAsyncWsgiByteEquivalence:
    """Confirm the WSGI helpers produce the same outputs as the async
    helpers for representative inputs, including error responses.
    """

    def test_send_json_bytes_match_async_version(self):
        data = {'hello': 'world', 'n': 1}

        # WSGI side
        sr = StartResponseCapture()
        wsgi_body = b''.join(send_json_wsgi(sr, data, 200))
        wsgi_headers = sr.header_dict()

        # Async side
        collector = ResponseCollector()
        asyncio.run(send_json(collector, data, 200))

        assert wsgi_body == collector.body
        assert wsgi_headers['content-type'] == (
            collector.headers['content-type']
        )
        assert wsgi_headers['content-length'] == (
            collector.headers['content-length']
        )
        assert sr.status.startswith(str(collector.status))

    def test_parse_json_body_413_matches_async(self):
        big = b'x' * (MAX_REQUEST_BODY + 100)

        _, wsgi_err = parse_json_body_wsgi(
            {'wsgi.input': io.BytesIO(big)},
        )
        _, async_err = asyncio.run(
            parse_json_body(make_receive(big)),
        )

        assert wsgi_err == async_err

    def test_parse_json_body_400_matches_async(self):
        bad = b'{not-json}'

        _, wsgi_err = parse_json_body_wsgi(_environ(bad))
        _, async_err = asyncio.run(parse_json_body(make_receive(bad)))

        assert wsgi_err == async_err

    def test_parse_json_body_success_matches_async(self):
        body = json.dumps({'a': 1, 'b': [2, 3]}).encode()

        wsgi_data, wsgi_err = parse_json_body_wsgi(_environ(body))
        async_data, async_err = asyncio.run(
            parse_json_body(make_receive(body)),
        )

        assert wsgi_err is None and async_err is None
        assert wsgi_data == async_data

    def test_send_json_413_bytes_match_async_version(self):
        """Wire bytes of a 413 response must be byte-identical to the
        async helper output (FR-4 lock-in for error responses).
        """
        data = {'error': 'Request body too large'}

        # WSGI side
        sr = StartResponseCapture()
        wsgi_body = b''.join(send_json_wsgi(sr, data, 413))
        wsgi_headers = sr.header_dict()

        # Async side
        collector = ResponseCollector()
        asyncio.run(send_json(collector, data, 413))

        assert wsgi_body == collector.body
        assert wsgi_headers['content-type'] == (
            collector.headers['content-type']
        )
        assert wsgi_headers['content-length'] == (
            collector.headers['content-length']
        )
        assert sr.status.startswith(str(collector.status))

    def test_send_json_400_bytes_match_async_version(self):
        """Wire bytes of a 400 response must be byte-identical to the
        async helper output.
        """
        data = {'error': 'Invalid JSON'}

        # WSGI side
        sr = StartResponseCapture()
        wsgi_body = b''.join(send_json_wsgi(sr, data, 400))
        wsgi_headers = sr.header_dict()

        # Async side
        collector = ResponseCollector()
        asyncio.run(send_json(collector, data, 400))

        assert wsgi_body == collector.body
        assert wsgi_headers['content-type'] == (
            collector.headers['content-type']
        )
        assert wsgi_headers['content-length'] == (
            collector.headers['content-length']
        )
        assert sr.status.startswith(str(collector.status))


# -------------------------------------------------------------------
# Negative CONTENT_LENGTH regression tests (M1 security fix)
# -------------------------------------------------------------------

class TestNegativeContentLength:
    """A negative ``CONTENT_LENGTH`` must not bypass the cap.

    Prior to M1, ``int('-1')`` parsed successfully, then
    ``min(-1, cap+1) == -1`` meant ``stream.read(-1)`` drained the
    entire buffered body.  The fix treats any negative declared
    length as malformed, falling through to the bounded
    ``read(cap + 1)`` path.
    """

    def test_negative_content_length_treated_as_malformed(self):
        """CL: -1 + 5 KB body -> 413 via read-beyond-cap path."""
        big = b'x' * 5000
        env = {
            'wsgi.input': io.BytesIO(big),
            'CONTENT_LENGTH': '-1',
        }
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Request body too large'}, 413)

    def test_negative_content_length_not_short_circuited_to_giant_read(
        self,
    ):
        """Spy on stream.read to confirm no negative-size read."""
        big = b'x' * 5000
        spy = ReadSpyStream(big)
        env = {
            'wsgi.input': spy,
            'CONTENT_LENGTH': '-1',
        }
        data, err = parse_json_body_wsgi(env)
        assert err == ({'error': 'Request body too large'}, 413)
        assert data is None
        # The read must be bounded at MAX_REQUEST_BODY + 1.  It must
        # never be called with a negative size (which would drain
        # the buffered body on a fully-buffered WSGI input).
        assert spy.read_calls, 'stream.read was never called'
        for size in spy.read_calls:
            assert size == MAX_REQUEST_BODY + 1, (
                f'unexpected read size {size!r}; expected only '
                f'{MAX_REQUEST_BODY + 1}'
            )
            assert size >= 0, (
                'stream.read must never be invoked with a negative '
                'size -- that would bypass the cap'
            )

    def test_large_negative_content_length_also_malformed(self):
        """CL: -999999 must not short-circuit to filler either."""
        big = b'x' * 5000
        spy = ReadSpyStream(big)
        env = {
            'wsgi.input': spy,
            'CONTENT_LENGTH': '-999999',
        }
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Request body too large'}, 413)
        for size in spy.read_calls:
            assert size == MAX_REQUEST_BODY + 1
