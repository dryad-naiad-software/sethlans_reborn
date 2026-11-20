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

from sethlans_manager import body_limits
from sethlans_manager.body_limits import body_limit_for_path, SMALL_BODY_LIMIT

# Fed to the parser at start: a Content-Length above the small limit
# (and below the default upload ceiling) on a small path must end in 413.
SELF_TEST_REQUEST = b"POST /api/enroll/ HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n"


def _post(path: str, length: int) -> bytes:
    return b"POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (path.encode('ascii'), length)


def _self_test_requests(ceiling: int) -> list:
    """The start-check requests; the second is refused only by the hook
    whatever the configured ceiling (when the ceiling is above the small
    limit at all)."""
    requests = [SELF_TEST_REQUEST]
    if ceiling > SMALL_BODY_LIMIT + 1:
        requests.append(_post('/api/enroll/', SMALL_BODY_LIMIT + 1))
    return requests


def class_probe_path(body_class) -> str:
    """A request path in *body_class*: its first pattern, each ``*`` as ``1``."""
    return body_class.paths[0].replace('*', '1')


def _class_checks(ceiling: int) -> list:
    """``(request, refused)`` pairs for each class whose limit is below *ceiling*.

    One byte over the class's limit must be refused (by the hook, since
    the ceiling is higher), and a body of exactly the limit must not be,
    so the class's own limit applies rather than the small one.
    """
    checks = []
    for body_class in body_limits.BODY_CLASSES:
        # Upload paths get the listener ceiling itself, whatever it is.
        if body_class is body_limits.UPLOAD_CLASS or body_class.limit + 1 >= ceiling:
            continue
        path = class_probe_path(body_class)
        checks.append((_post(path, body_class.limit + 1), True))
        checks.append((_post(path, body_class.limit), False))
    return checks


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


def _refused(channel_class, adj, request) -> bool:
    parser = channel_class.parser_class(adj)
    parser.received(request)
    return bool(parser.completed and isinstance(parser.error, RequestEntityTooLarge))


def _self_test(channel_class, adj) -> None:
    """Raise ``RuntimeError`` unless the parser refuses an oversized small-path
    body and, on a listener that serves uploads, applies each class's limit."""
    ceiling = adj.max_request_body_size
    for request in _self_test_requests(ceiling):
        if not _refused(channel_class, adj, request):
            raise RuntimeError(
                "Waitress parser did not refuse an oversized body on a small "
                "path; per-path body limits are not working."
            )
    if not channel_class.parser_class.allow_uploads:
        return
    for request, refuse in _class_checks(ceiling):
        if _refused(channel_class, adj, request) != refuse:
            outcome = "did not refuse a body over" if refuse else "refused a body within"
            raise RuntimeError(
                f"Waitress parser {outcome} the limit of a body class "
                f"({request.split(b' ')[1].decode('ascii')}); per-path body limits are not working."
            )


def limit_request_bodies(server, *, allow_uploads: bool):
    """Make *server* (from ``waitress.create_server``) apply the path limits.

    ``allow_uploads=False`` gives every path the small limit (the
    internal-origin listener, which routes only ``urls_loopback``).
    Must be called before the server starts accepting connections.
    Fails closed: raises ``RuntimeError`` when the Waitress internals
    this relies on changed, the parser does not refuse an oversized
    body, or (with ``allow_uploads``) a body class's limit does not apply.
    """
    _check_internals(server)
    channel_class = PathLimitedChannel if allow_uploads else SmallBodyChannel
    _self_test(channel_class, server.adj)
    server.channel_class = channel_class
    return server
