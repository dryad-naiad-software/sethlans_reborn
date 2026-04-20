# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the sync WSGI worker ``app`` in ``asgi_app.py``.

Phase 4b of the Waitress migration: ``asgi_app.app`` is now a sync
WSGI callable rather than an async ASGI callable. These tests
exercise the WSGI interface directly -- no ASGI scope/receive/send
plumbing. Setup gate behaviour is exercised by the pass-through
and 503-block tests at the bottom of the file.

The setup wizard handlers are still async in Phase 4b; they are
driven transparently via the ``gate_async_adapter`` bridge and are
not re-tested here beyond the round-trip verification in
``TestSetupPassThrough``.
"""

import io
import json
import threading
from typing import Any, Dict, List, Tuple

import pytest

from sethlans_worker_agent.web_ui import asgi_app as asgi_app_module


# --- Minimal WSGI driver helpers ---------------------------------

def make_environ(
    path: str = '/',
    method: str = 'GET',
    body: bytes = b'',
    headers: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'QUERY_STRING': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8080',
        'wsgi.url_scheme': 'https',
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    for name, value in (headers or {}).items():
        if name.lower() in ('content-type', 'content-length'):
            env[name.upper().replace('-', '_')] = value
        else:
            env['HTTP_' + name.upper().replace('-', '_')] = value
    return env


class StartResponseRecorder:
    """Captures WSGI ``start_response`` invocations for assertion."""

    def __init__(self) -> None:
        self.status: str = ''
        self.headers: List[Tuple[str, str]] = []

    def __call__(
        self, status: str, headers: List[Tuple[str, str]], exc_info=None,
    ) -> None:
        self.status = status
        self.headers = list(headers)

    @property
    def status_code(self) -> int:
        return int(self.status.split(' ', 1)[0])

    def header(self, name: str) -> str:
        lower = name.lower()
        for k, v in self.headers:
            if k.lower() == lower:
                return v
        return ''


def drain(iterable) -> bytes:
    return b''.join(iterable)


@pytest.fixture
def setup_complete():
    """Flip the setup gate open so dashboard routes are reachable."""
    from sethlans_worker_agent.web_ui.setup import gate
    gate.mark_setup_complete()
    yield
    gate._setup_complete = False


@pytest.fixture
def fresh_shutdown_event():
    """Replace the lazily-bound shutdown event with a fresh one.

    The app resolves the agent's real ``_shutdown_event`` on first
    call; tests replace it with a clean ``threading.Event`` so
    ``is_set()`` is deterministic and cannot leak between tests.
    """
    ev = threading.Event()
    asgi_app_module._shutdown_event_ref = ev
    yield ev
    asgi_app_module._shutdown_event_ref = None


@pytest.fixture
def bearer_header(mocker):
    """Patch password validation to accept the literal ``test-token``."""
    mocker.patch(
        'sethlans_worker_agent.web_ui.asgi_app.validate_password',
        side_effect=lambda pw: pw == 'test-token',
    )
    return {'Authorization': 'Bearer test-token'}


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


# --- Control endpoints (POST) -------------------------------------

class TestControlAuth:
    def test_missing_auth_returns_401(
        self, setup_complete, fresh_shutdown_event,
    ):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ('/api/control/pause', method='POST'), rec,
            ),
        )
        assert rec.status_code == 401
        assert json.loads(body) == {'error': 'Unauthorized'}

    def test_wrong_auth_returns_401(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers={'Authorization': 'Bearer nope'},
                ),
                rec,
            ),
        )
        assert rec.status_code == 401
        assert json.loads(body) == {'error': 'Unauthorized'}

    def test_missing_bearer_prefix_returns_401(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers={'Authorization': 'test-token'},
                ),
                rec,
            ),
        )
        assert rec.status_code == 401


class TestControlPauseResume:
    def test_pause_calls_job_processor(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        pause_mock = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.job_processor.pause',
        )
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'status': 'paused'}
        pause_mock.assert_called_once()

    def test_resume_calls_job_processor(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        resume_mock = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.job_processor.resume',
        )
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/resume', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'status': 'resumed'}
        resume_mock.assert_called_once()


class TestControlShutdown:
    def test_shutdown_sets_event_and_returns_ok(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/shutdown', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'status': 'shutting_down'}
        assert fresh_shutdown_event.is_set()

    def test_shutdown_event_set_returns_503(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        fresh_shutdown_event.set()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 503
        assert json.loads(body) == {'error': 'Server is shutting down'}


class TestControlSetPassword:
    def test_valid_password_set(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        set_pw = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.set_password',
        )
        payload = json.dumps({'password': 'newpw123'}).encode()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/set_password', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {'status': 'password_set'}
        set_pw.assert_called_once_with('newpw123')

    def test_short_password_rejected(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        set_pw = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.set_password',
        )
        payload = json.dumps({'password': 'abc'}).encode()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/set_password', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 400
        assert 'at least 4' in json.loads(body)['error']
        set_pw.assert_not_called()

    def test_invalid_json_rejected(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/set_password', method='POST',
                    body=b'not-json', headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 400
        assert json.loads(body) == {'error': 'Invalid JSON'}


class TestControlConfigUpdate:
    def test_allowed_key_updates(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        apply = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.apply_config_change',
        )
        payload = json.dumps(
            {'key': 'POLLING_INTERVAL', 'value': 30},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/update', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 200
        assert json.loads(body) == {
            'status': 'updated', 'key': 'POLLING_INTERVAL', 'value': 30,
        }
        apply.assert_called_once_with('POLLING_INTERVAL', 30)

    def test_disallowed_key_rejected(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        apply = mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.apply_config_change',
        )
        payload = json.dumps(
            {'key': 'MANAGER_API_URL', 'value': 'http://evil'},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/update', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 403
        assert 'not modifiable' in json.loads(body)['error']
        apply.assert_not_called()

    def test_invalid_value_returns_400(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        mocker.patch(
            'sethlans_worker_agent.web_ui.asgi_app.apply_config_change',
            side_effect=ValueError('bad'),
        )
        payload = json.dumps(
            {'key': 'POLLING_INTERVAL', 'value': -1},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/update', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 400
        assert json.loads(body) == {'error': 'bad'}


class TestControlBodyCap:
    def test_body_over_4k_returns_413(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        # Send CONTENT_LENGTH over the 4 KB cap; read_body_wsgi
        # short-circuits and returns the oversize sentinel.
        payload = b'x' * (4096 + 1)
        rec = StartResponseRecorder()
        body = drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/set_password', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 413
        assert json.loads(body) == {'error': 'Request body too large'}


class TestUnknownControlAction:
    def test_unknown_action_returns_404(
        self, setup_complete, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        drain(
            asgi_app_module.app(
                make_environ(
                    '/api/control/unknown', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 404


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
