# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the request size classes (Unreal sequence spec
D21, FR-BL-2, FR-BL-4) on real Waitress listeners.

The sequence endpoints do not exist yet, so each test installs the D21
class table (``tests._body_limit_classes``) and starts a manager-style
listener with a 2 MiB ceiling, as the issue #260 tests do. Covers:

- A JSON result of 1 MiB + 1 on each result path gets 413 from the
  headers, and the app never runs; exactly 1 MiB is accepted.
- A chunked result body over 1 MiB gets 413.
- A frame or sound body above the small limit is accepted; the server
  ceiling still caps both classes.
- Near-miss paths keep the small limit, and the internal-origin
  listener gives the class paths the small limit too.
"""

from __future__ import annotations

import threading

import pytest
import waitress

from sethlans_manager.body_limits import RESULT_BODY_LIMIT
from sethlans_manager.waitress_body_limits import limit_request_bodies
from tests._body_limit_classes import use_d21_classes
from tests.integration.manager import test_waitress_body_limits as base

LARGE = base.LARGE  # 1 MiB: above the small limit, below the ceiling
CEILING = base.CEILING  # 2 MiB

RESULT_PATHS = ['/api/jobs/7/shots-result/', '/api/jobs/7/piece-result/', '/api/sequence-submissions/']
FILE_PATHS = ['/api/jobs/7/frames/', '/api/jobs/7/sound-result/']


@pytest.fixture
def serve_d21(monkeypatch):
    """Factory: start a listener with the D21 table installed, return (port, app)."""
    use_d21_classes(monkeypatch)
    started = []

    def _start(*, allow_uploads=True):
        app = base._Recorder()
        server = waitress.create_server(
            app, host='127.0.0.1', port=0, threads=2,
            max_request_body_size=CEILING, ident='body-class-test',
        )
        limit_request_bodies(server, allow_uploads=allow_uploads)
        thread = threading.Thread(target=server.run, name='body-class-waitress', daemon=True)
        thread.start()
        started.append((server, thread))
        return server.effective_port, app

    yield _start
    for server, thread in started:
        base._stop(server, thread)


class TestResultClass:
    @pytest.mark.parametrize('path', RESULT_PATHS)
    def test_one_byte_over_refused_before_the_body(self, serve_d21, path):
        port, app = serve_d21()
        assert base._post_with_length(port, path, RESULT_BODY_LIMIT + 1, 1024) == 413
        assert app.calls == []

    def test_exactly_the_limit_accepted(self, serve_d21):
        port, app = serve_d21()
        path = '/api/jobs/7/shots-result/'
        assert base._post_with_length(port, path, RESULT_BODY_LIMIT, RESULT_BODY_LIMIT) == 200
        assert app.calls == [(path, RESULT_BODY_LIMIT)]

    def test_chunked_over_the_limit_refused(self, serve_d21):
        port, app = serve_d21()
        assert base._post_chunked(port, '/api/jobs/7/piece-result/', RESULT_BODY_LIMIT + 64 * 1024) == 413
        assert app.calls == []


class TestFileClasses:
    @pytest.mark.parametrize('path', FILE_PATHS)
    def test_body_above_the_small_limit_accepted(self, serve_d21, path):
        port, app = serve_d21()
        assert base._post_with_length(port, path, LARGE, LARGE) == 200
        assert app.calls == [(path, LARGE)]

    @pytest.mark.parametrize('path', FILE_PATHS)
    def test_server_ceiling_still_applies(self, serve_d21, path):
        port, app = serve_d21()
        assert base._post_with_length(port, path, CEILING + 1, 0) == 413
        assert app.calls == []


class TestSmallStill:
    @pytest.mark.parametrize('path', [
        '/api/jobs/7/frames/extra/',
        '/api/jobs/7/sound-result',
        '/api/sequence-submissions/5/',
        '/API/JOBS/7/FRAMES/',
    ])
    def test_near_miss_gets_small_limit(self, serve_d21, path):
        port, app = serve_d21()
        assert base._post_with_length(port, path, LARGE, 1024) == 413
        assert app.calls == []

    @pytest.mark.parametrize('path', FILE_PATHS + RESULT_PATHS[:1])
    def test_internal_listener_is_small(self, serve_d21, path):
        port, app = serve_d21(allow_uploads=False)
        assert base._post_with_length(port, path, LARGE, 1024) == 413
        assert app.calls == []
