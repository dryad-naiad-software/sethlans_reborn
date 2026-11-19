# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-path request body limits for the Waitress listeners (issue #260).

Waitress has one ``max_request_body_size`` per server, and it reads the
whole body before the WSGI app runs, so a check inside Django comes
too late to stop a large body from being received. This module narrows
the limit per request inside Waitress's own parser, right after the
request line and headers are parsed:

* ``Content-Length`` above the path's limit: Waitress answers 413 from
  the headers and never reads the body.
* ``Expect: 100-continue`` with such a ``Content-Length``: Waitress
  sends ``100 Continue`` as soon as the headers are complete (doing so
  re-opens the refused request), so the client starts sending; Waitress
  then reads the body only up to the path's limit and answers 413.
  Bounded, but the body is read up to the limit.
* ``Transfer-Encoding: chunked``: Waitress stops with 413 once the
  received body passes the path's limit, so it reads at most about
  the small limit.

The server's ``max_request_body_size`` (``waitress_config``) stays the
ceiling for upload paths. Path classes come from :mod:`body_limits`.

This relies on Waitress internals of the pinned 3.0.x
(``HTTPChannel.parser_class``, ``HTTPRequestParser.parse_header`` and
``.adj.max_request_body_size``, ``server.channel_class``).
:func:`limit_request_bodies` checks them at start and raises
``RuntimeError`` when they changed, so a Waitress upgrade cannot
silently drop the limits.
"""

from __future__ import annotations

from waitress.channel import HTTPChannel
from waitress.parser import HTTPRequestParser
from waitress.server import BaseWSGIServer
from waitress.utilities import RequestEntityTooLarge

from sethlans_manager.body_limits import body_limit_for_path, SMALL_BODY_LIMIT

# Fed to the parser at start: a Content-Length above the small limit
# (and below the default upload ceiling) on a small path must end in 413.
SELF_TEST_REQUEST = b"POST /api/enroll/ HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n"


def _self_test_requests(ceiling: int) -> list:
    """The start-check requests; the second is refused only by the hook
    whatever the configured ceiling (when the ceiling is above the small
    limit at all)."""
    requests = [SELF_TEST_REQUEST]
    if ceiling > SMALL_BODY_LIMIT + 1:
        requests.append(
            b"POST /api/enroll/ HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (SMALL_BODY_LIMIT + 1)
        )
    return requests


class _BodyLimitAdjustments:
    """The server's ``Adjustments`` with this request's body limit."""

    def __init__(self, adj, max_request_body_size: int):
        self._adj = adj
        self.max_request_body_size = max_request_body_size

    def __getattr__(self, name):
        return getattr(self._adj, name)


def _waitress_limit(accepted: int) -> int:
    """Waitress refuses a body whose size is ``>=`` its limit."""
    return accepted + 1


class _PathLimitedParser(HTTPRequestParser):
    """Request parser that applies the path's body limit."""

    allow_uploads = True

    def parse_header(self, header_plus):
        super().parse_header(header_plus)
        ceiling = self.adj.max_request_body_size
        if self.allow_uploads:
            accepted = body_limit_for_path(self.path, ceiling)
        else:
            accepted = min(SMALL_BODY_LIMIT, ceiling)
        if accepted < ceiling:
            self.adj = _BodyLimitAdjustments(self.adj, _waitress_limit(accepted))


class _SmallOnlyParser(_PathLimitedParser):
    """Parser for a listener that serves no upload path."""

    allow_uploads = False


class PathLimitedChannel(HTTPChannel):
    parser_class = _PathLimitedParser


class SmallBodyChannel(HTTPChannel):
    parser_class = _SmallOnlyParser


def _check_internals(server) -> None:
    """Raise ``RuntimeError`` unless *server* uses the classes subclassed here."""
    if not isinstance(server, BaseWSGIServer):
        # e.g. a ``MultiSocketServer`` (several listen addresses): setting
        # ``channel_class`` on it would do nothing.
        raise RuntimeError(
            f"Waitress returned {type(server).__name__}, not a single-socket "
            "server; per-path body limits cannot be installed."
        )
    if server.channel_class is not HTTPChannel or HTTPChannel.parser_class is not HTTPRequestParser:
        raise RuntimeError(
            "Waitress channel or parser classes changed; per-path body "
            "limits cannot be installed."
        )


def _self_test(channel_class, adj) -> None:
    """Raise ``RuntimeError`` unless the parser refuses an oversized small-path body."""
    for request in _self_test_requests(adj.max_request_body_size):
        parser = channel_class.parser_class(adj)
        parser.received(request)
        if not (parser.completed and isinstance(parser.error, RequestEntityTooLarge)):
            raise RuntimeError(
                "Waitress parser did not refuse an oversized body on a small "
                "path; per-path body limits are not working."
            )


def limit_request_bodies(server, *, allow_uploads: bool):
    """Make *server* (from ``waitress.create_server``) apply the path limits.

    ``allow_uploads=False`` gives every path the small limit (the
    internal-origin listener, which routes only ``urls_loopback``).
    Must be called before the server starts accepting connections.
    Fails closed: raises ``RuntimeError`` when the Waitress internals
    this relies on changed or the parser does not refuse an oversized
    body.
    """
    _check_internals(server)
    channel_class = PathLimitedChannel if allow_uploads else SmallBodyChannel
    _self_test(channel_class, server.adj)
    server.channel_class = channel_class
    return server
