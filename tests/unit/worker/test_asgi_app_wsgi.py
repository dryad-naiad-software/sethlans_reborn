# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""WSGI unit tests for worker/sethlans_worker_agent/web_ui/asgi_app.py.

Also contains a small number of transitional async inner-app fixtures (async def) used to verify the WSGI
wrapper's handling of async handlers via the Phase 3 asyncio.run adapter. Those fixtures go away in Phase 7
along with gate_async_adapter.py.

Phase 4b of the Waitress migration: ``asgi_app.app`` is now a sync
WSGI callable rather than an async ASGI callable. These tests
exercise the WSGI interface directly -- no ASGI scope/receive/send
plumbing.

This file covers static routes, the status snapshot route, and
setup-gate pass-through / 503 behaviour.  Control-plane endpoints
(``/api/control/*``) are exercised in the sibling
``test_asgi_app_wsgi_control.py`` and
``test_asgi_app_wsgi_config.py`` files.

The setup wizard handlers are still async in Phase 4b; they are
driven transparently via the ``gate_async_adapter`` bridge and are
not re-tested here beyond the round-trip verification in
``TestSetupGate.test_setup_status_allowed_during_setup``.
"""

import json

from sethlans_worker_agent.web_ui import asgi_app as asgi_app_module

from tests.unit.worker._wsgi_helpers import (
    StartResponseRecorder,
    drain,
    make_environ,
)

# Fixtures ``setup_complete``, ``fresh_shutdown_event``, and
# ``bearer_header`` are declared in ``conftest.py``.


# --- GET routes ---------------------------------------------------

class TestStaticRoutes:
    def test_root_serves_index_html(
        self, setup_complete, tmp_path, mocker,
    ):
        index = tmp_path / 'index.html'
        index.write_bytes(b'<html>dash</html>')
        mocker.patch.object(asgi_app_module, '_INDEX_PATH', str(index))
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/'), rec),
        )
        assert rec.status_code == 200
        assert body == b'<html>dash</html>'
        assert rec.header('Content-Type').startswith('text/html')

    def test_index_html_serves_index_html(
        self, setup_complete, tmp_path, mocker,
    ):
        index = tmp_path / 'index.html'
        index.write_bytes(b'<html>dash</html>')
        mocker.patch.object(asgi_app_module, '_INDEX_PATH', str(index))
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/index.html'), rec),
        )
        assert rec.status_code == 200
        assert body == b'<html>dash</html>'

    def test_unknown_get_returns_404(self, setup_complete):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/nope'), rec),
        )
        assert rec.status_code == 404
        assert json.loads(body) == {'error': 'Not Found'}

    def test_unsupported_method_returns_405(self, setup_complete):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/', method='PUT'), rec),
        )
        assert rec.status_code == 405
        assert json.loads(body) == {'error': 'Method Not Allowed'}


class TestStatusRoute:
    def test_status_returns_snapshot(self, setup_complete, mocker):
        mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.get_status_snapshot',
            return_value={'state': 'idle', 'ok': True},
        )
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/api/status'), rec),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'state': 'idle', 'ok': True}
        assert rec.header('Content-Type') == 'application/json'


# --- Setup gate integration --------------------------------------

class TestSetupGate:
    def test_dashboard_blocked_during_setup(self):
        """Non-setup paths 503 while the gate is closed."""
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(make_environ('/api/status'), rec),
        )
        assert rec.status_code == 503
        assert json.loads(body) == {'detail': 'Setup not complete.'}

    def test_setup_status_allowed_during_setup(self, mocker):
        """``/api/setup/status/`` passes through the gate.

        The async handler is driven via the Phase-3 adapter. We
        stub ``handle_setup_status`` to keep the test hermetic --
        we only assert the dispatcher delivered the request and
        relayed the response.
        """

        async def fake_handler(scope, receive, send):
            await send({
                'type': 'http.response.start', 'status': 200,
                'headers': [(b'content-type', b'application/json')],
            })
            await send({
                'type': 'http.response.body',
                'body': b'{"gate":"open"}', 'more_body': False,
            })

        mocker.patch(
            'sethlans_worker_agent.web_ui.setup.routes.'
            '_GET_ROUTES',
            {'/api/setup/status/': fake_handler},
        )
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ('/api/setup/status/'), rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'gate': 'open'}
