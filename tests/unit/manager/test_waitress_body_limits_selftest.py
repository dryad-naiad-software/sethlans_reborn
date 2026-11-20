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
from sethlans_manager.body_limits import RESULT_BODY_LIMIT, SMALL_BODY_LIMIT, UPLOAD_BODY_LIMIT
from tests._body_limit_classes import FRAME_CLASS, RESULT_CLASS, SOUND_CLASS, use_d21_classes


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


class TestBodyClassSelfTest:
    """The start check also proves each class below the ceiling gets its own
    limit (spec D21), with the D21 table installed."""

    def test_live_table_checks_the_frame_and_result_classes(self):
        """The upload class is capped by the ceiling, so it is not probed;
        the live frame and result classes (chunk 3) are, both ways."""
        expected = []
        for path, limit in ((b'/api/jobs/1/frames/', FRAME_CLASS.limit), (b'/api/jobs/1/shots-result/', RESULT_BODY_LIMIT)):
            expected += [
                (b'POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n' % (path, limit + 1), True),
                (b'POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n' % (path, limit), False),
            ]
        assert wbl._class_checks(UPLOAD_BODY_LIMIT) == expected

    def test_d21_checks_each_class_both_ways(self, monkeypatch):
        use_d21_classes(monkeypatch)
        checks = wbl._class_checks(UPLOAD_BODY_LIMIT)
        expected = []
        for body_class in (FRAME_CLASS, SOUND_CLASS, RESULT_CLASS):
            path = wbl.class_probe_path(body_class).encode()
            expected += [
                (b'POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n' % (path, body_class.limit + 1), True),
                (b'POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n' % (path, body_class.limit), False),
            ]
        assert checks == expected

    def test_lowered_ceiling_skips_classes_above_it(self, monkeypatch):
        use_d21_classes(monkeypatch)
        checks = wbl._class_checks(2 * 1024 * 1024)
        assert {request.split(b' ')[1] for request, _refuse in checks} == {b'/api/jobs/1/shots-result/'}

    @pytest.mark.parametrize('d21', [False, True], ids=['live', 'd21'])
    def test_ceiling_above_the_upload_limit_starts(self, make_server, monkeypatch, d21):
        """Upload paths get the configured ceiling, so a 20 GiB ceiling is not
        a class fault (``waitress_max_request_body_size`` may be raised)."""
        if d21:
            use_d21_classes(monkeypatch)
        ceiling = 20 * 1024 ** 3
        assert all(b'/api/assets/' not in request for request, _ in wbl._class_checks(ceiling))
        server = make_server(max_request_body_size=ceiling)
        assert wbl.limit_request_bodies(server, allow_uploads=True) is server

    @pytest.mark.parametrize('allow_uploads', [True, False])
    @pytest.mark.parametrize('ceiling', [None, 2 * 1024 * 1024])
    def test_installs_with_the_d21_table(self, make_server, monkeypatch, allow_uploads, ceiling):
        use_d21_classes(monkeypatch)
        server = make_server(max_request_body_size=ceiling or UPLOAD_BODY_LIMIT)
        assert wbl.limit_request_bodies(server, allow_uploads=allow_uploads) is server

    def test_class_given_the_small_limit_refused(self, make_server, monkeypatch):
        """Simulate the hook ignoring the classes: a frame body of exactly
        its limit would be refused, so the start check fails."""
        use_d21_classes(monkeypatch)
        server = make_server()
        monkeypatch.setattr(
            wbl, 'body_limit_for_path',
            lambda path, ceiling: ceiling if path.startswith('/api/assets/') else SMALL_BODY_LIMIT,
        )
        with pytest.raises(RuntimeError, match='refused a body within the limit of a body class'):
            wbl.limit_request_bodies(server, allow_uploads=True)
        assert server.channel_class is HTTPChannel

    def test_class_given_the_ceiling_refused(self, make_server, monkeypatch):
        """Simulate a class path getting the server ceiling: one byte over
        the class's limit would be accepted, so the start check fails."""
        use_d21_classes(monkeypatch)
        server = make_server()
        monkeypatch.setattr(
            wbl, 'body_limit_for_path',
            lambda path, ceiling: SMALL_BODY_LIMIT if path == '/api/enroll/' else ceiling,
        )
        with pytest.raises(RuntimeError, match='did not refuse a body over the limit of a body class'):
            wbl.limit_request_bodies(server, allow_uploads=True)
