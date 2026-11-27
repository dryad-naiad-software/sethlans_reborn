# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""WSGI unit tests for ``worker/sethlans_worker_agent/web_ui/wsgi_app.py``.

Covers static routes, the status snapshot route, and the absence of
setup routes (setup-rebuild FR-WK-7). Control-plane endpoints
(``/api/control/*``) are exercised in the sibling
``test_wsgi_app_control.py`` and ``test_wsgi_app_config.py`` files.
"""

import json

import pytest

from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module

from tests.unit.worker._wsgi_helpers import (
    StartResponseRecorder,
    drain,
    make_environ,
)

# Fixtures ``fresh_shutdown_event`` and ``bearer_header`` are declared
# in ``conftest.py``.


# --- GET routes ---------------------------------------------------

class TestStaticRoutes:
    def test_root_serves_index_html(
        self, tmp_path, mocker,
    ):
        index = tmp_path / 'index.html'
        index.write_bytes(b'<html>dash</html>')
        mocker.patch.object(wsgi_app_module, '_INDEX_PATH', str(index))
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(make_environ('/'), rec),
        )
        assert rec.status_code == 200
        assert body == b'<html>dash</html>'
        assert rec.header('Content-Type').startswith('text/html')

    def test_index_html_serves_index_html(
        self, tmp_path, mocker,
    ):
        index = tmp_path / 'index.html'
        index.write_bytes(b'<html>dash</html>')
        mocker.patch.object(wsgi_app_module, '_INDEX_PATH', str(index))
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(make_environ('/index.html'), rec),
        )
        assert rec.status_code == 200
        assert body == b'<html>dash</html>'

    @pytest.mark.parametrize('path', ['/nope', '/setup', '/api/setup/status/'])
    def test_unknown_get_returns_404(self, path):
        """FR-WK-7: the worker serves no setup routes; they are unknown paths like any other."""
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(make_environ(path), rec),
        )
        assert rec.status_code == 404
        assert json.loads(body) == {'error': 'Not Found'}

    def test_unsupported_method_returns_405(self):
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(make_environ('/', method='PUT'), rec),
        )
        assert rec.status_code == 405
        assert json.loads(body) == {'error': 'Method Not Allowed'}


class TestStatusRoute:
    def test_status_returns_snapshot(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.web_ui.wsgi_app.get_status_snapshot',
            return_value={'state': 'idle', 'ok': True},
        )
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(make_environ('/api/status'), rec),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'state': 'idle', 'ok': True}
        assert rec.header('Content-Type') == 'application/json'
