# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for sync WSGI payload-validation control endpoints.

Companion to ``test_wsgi_app.py`` and
``test_wsgi_app_control.py``: covers ``/api/control/set_password``
and ``/api/control/update`` payload validation plus the 4 KB body
cap shared by both.
"""

import json

from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module

from tests.unit.worker._wsgi_helpers import (
    StartResponseRecorder,
    drain,
    make_environ,
)

# Fixtures ``setup_complete``, ``fresh_shutdown_event``, and
# ``bearer_header`` are declared in ``conftest.py``.


class TestControlSetPassword:
    def test_valid_password_set(
        self, setup_complete, fresh_shutdown_event, bearer_header, mocker,
    ):
        set_pw = mocker.patch(
            'sethlans_worker_agent.web_ui.wsgi_app.set_password',
        )
        payload = json.dumps({'password': 'newpw123'}).encode()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
            'sethlans_worker_agent.web_ui.wsgi_app.set_password',
        )
        payload = json.dumps({'password': 'abc'}).encode()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
            wsgi_app_module.app(
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
            'sethlans_worker_agent.web_ui.wsgi_app.apply_config_change',
        )
        payload = json.dumps(
            {'key': 'POLLING_INTERVAL', 'value': 30},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
            'sethlans_worker_agent.web_ui.wsgi_app.apply_config_change',
        )
        payload = json.dumps(
            {'key': 'MANAGER_API_URL', 'value': 'http://evil'},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
            'sethlans_worker_agent.web_ui.wsgi_app.apply_config_change',
            side_effect=ValueError('bad'),
        )
        payload = json.dumps(
            {'key': 'POLLING_INTERVAL', 'value': -1},
        ).encode()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
            wsgi_app_module.app(
                make_environ(
                    '/api/control/set_password', method='POST',
                    body=payload, headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 413
        assert json.loads(body) == {'error': 'Request body too large'}
