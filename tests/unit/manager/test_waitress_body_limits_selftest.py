# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the fail-closed start check of
:func:`sethlans_manager.waitress_body_limits.limit_request_bodies`
(issue #260).

The hook relies on Waitress internals. Each test simulates one change
(a multi-socket server, replaced channel or parser classes, a parser
that no longer reads the per-request limit) and expects
``RuntimeError`` instead of a server that silently accepts large
bodies. The servers are created but never run.
"""

from __future__ import annotations

import pytest
import waitress
from waitress import channel as waitress_channel
from waitress import parser as waitress_parser
from waitress.channel import HTTPChannel

from sethlans_manager import waitress_body_limits as wbl
from sethlans_manager.body_limits import UPLOAD_BODY_LIMIT


def _app(environ, start_response):  # pragma: no cover - never served
    start_response('200 OK', [])
    return [b'']


@pytest.fixture
def make_server():
    servers = []

    def _make(**kwargs):
        if 'listen' not in kwargs:
            kwargs.update(host='127.0.0.1', port=0)
        server = waitress.create_server(_app, **kwargs)
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.close()


class TestHappyPath:
    @pytest.mark.parametrize('allow_uploads, expected', [
        (True, wbl.PathLimitedChannel),
        (False, wbl.SmallBodyChannel),
    ])
    def test_installs_channel(self, make_server, allow_uploads, expected):
        server = make_server()
        assert wbl.limit_request_bodies(server, allow_uploads=allow_uploads) is server
        assert server.channel_class is expected

    def test_self_test_request_is_refused_by_the_hook_only(self):
        """The self-test body is below the upload ceiling, so only the hook refuses it."""
        assert b'999999999' in wbl.SELF_TEST_REQUEST
        assert 999_999_999 < wbl.body_limit_for_path('/api/assets/')


class TestFailsClosed:
    def test_multi_socket_server_refused(self, make_server):
        server = make_server(listen='127.0.0.1:0 127.0.0.1:0')
        assert type(server).__name__ == 'MultiSocketServer'
        with pytest.raises(RuntimeError, match='single-socket'):
            wbl.limit_request_bodies(server, allow_uploads=True)

    def test_changed_server_channel_class_refused(self, make_server):
        server = make_server()

        class OtherChannel(HTTPChannel):
            pass

        server.channel_class = OtherChannel
        with pytest.raises(RuntimeError, match='channel or parser'):
            wbl.limit_request_bodies(server, allow_uploads=True)

    def test_changed_parser_class_refused(self, make_server, monkeypatch):
        server = make_server()

        class OtherParser(waitress_parser.HTTPRequestParser):
            pass

        monkeypatch.setattr(waitress_channel.HTTPChannel, 'parser_class', OtherParser)
        with pytest.raises(RuntimeError, match='channel or parser'):
            wbl.limit_request_bodies(server, allow_uploads=True)

    @pytest.mark.parametrize('allow_uploads', [True, False])
    def test_parser_ignoring_the_limit_refused(self, make_server, monkeypatch, allow_uploads):
        """Simulate Waitress no longer reading ``adj.max_request_body_size``
        per request: the parser then accepts the oversized self-test body."""
        server = make_server()

        def received_without_limit(self, data):
            self.parse_header(data.split(b'\r\n\r\n', 1)[0])
            self.completed = True
            return len(data)

        monkeypatch.setattr(waitress_parser.HTTPRequestParser, 'received', received_without_limit)
        with pytest.raises(RuntimeError, match='did not refuse'):
            wbl.limit_request_bodies(server, allow_uploads=allow_uploads)
        assert server.channel_class is HTTPChannel

    @pytest.mark.parametrize('ceiling', [None, 2 * 1024 * 1024])
    def test_hook_not_applied_refused(self, make_server, monkeypatch, ceiling):
        """Simulate the path hook having no effect (limit equals the ceiling),
        with the default 10 GiB ceiling and with a lowered one."""
        server = make_server(max_request_body_size=ceiling or UPLOAD_BODY_LIMIT)
        monkeypatch.setattr(wbl, 'body_limit_for_path', lambda path, ceiling: ceiling)
        with pytest.raises(RuntimeError, match='did not refuse'):
            wbl.limit_request_bodies(server, allow_uploads=True)
