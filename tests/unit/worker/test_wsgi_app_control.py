# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for sync WSGI control endpoints in ``wsgi_app.py``.

Companion to ``test_wsgi_app.py``: covers authentication,
pause/resume, shutdown, and unknown-action handling for
``/api/control/*`` POST routes.  Payload-validation routes
(``set_password``, ``update``, oversize body) live in
``test_wsgi_app_config.py``.
"""

import json

import pytest

from sethlans_worker_agent.web_ui import auth
from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module

from tests.unit.worker._wsgi_helpers import (
    StartResponseRecorder,
    drain,
    make_environ,
)

# Fixtures ``fresh_shutdown_event``, ``bearer_header`` and
# ``no_page_password`` are declared in ``conftest.py``.


class TestControlAuth:
    def test_no_password_set_returns_403(
        self, fresh_shutdown_event, no_page_password,
    ):
        """FR-WK-4: no default password; with none stored the control actions are refused."""
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers={'Authorization': 'Bearer anything-at-all'},
                ),
                rec,
            ),
        )
        assert rec.status_code == 403
        assert json.loads(body) == {'error': 'No worker page password is set; run Reset setup'}

    def test_wrong_auth_returns_401(
        self, fresh_shutdown_event, bearer_header,
    ):
        """FR-SEC-8: a wrong password is refused."""
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
        self, fresh_shutdown_event, bearer_header,
    ):
        """FR-SEC-8: the password counts only in an ``Authorization: Bearer`` header."""
        rec = StartResponseRecorder()
        drain(
            wsgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers={'Authorization': 'test-token'},
                ),
                rec,
            ),
        )
        assert rec.status_code == 401

    @pytest.mark.parametrize('outcome, status', [
        (auth.LIMITED, 429), (auth.BUSY, 429), (auth.UNREADABLE, 503),
    ])
    def test_other_refusals_map_to_their_status(
        self, fresh_shutdown_event, mocker, outcome, status,
    ):
        """FR-SEC-8: the limit and the busy PBKDF2 slots give 429; an unreadable settings file 503."""
        mocker.patch.object(auth, 'check_bearer', return_value=outcome)
        pause = mocker.patch('sethlans_worker_agent.web_ui.wsgi_app.job_processor.pause')
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
                make_environ('/api/control/pause', method='POST'), rec,
            ),
        )
        assert rec.status_code == status
        assert isinstance(json.loads(body)['error'], str)
        pause.assert_not_called()


class TestControlPauseResume:
    def test_pause_calls_job_processor(
        self, fresh_shutdown_event, bearer_header, mocker,
    ):
        pause_mock = mocker.patch(
            'sethlans_worker_agent.web_ui.wsgi_app.job_processor.pause',
        )
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
        self, fresh_shutdown_event, bearer_header, mocker,
    ):
        resume_mock = mocker.patch(
            'sethlans_worker_agent.web_ui.wsgi_app.job_processor.resume',
        )
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
        self, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
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
        self, fresh_shutdown_event, bearer_header,
    ):
        fresh_shutdown_event.set()
        rec = StartResponseRecorder()
        body = drain(
            wsgi_app_module.app(
                make_environ(
                    '/api/control/pause', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 503
        assert json.loads(body) == {'error': 'Server is shutting down'}


class TestUnknownControlAction:
    def test_unknown_action_returns_404(
        self, fresh_shutdown_event, bearer_header,
    ):
        rec = StartResponseRecorder()
        drain(
            wsgi_app_module.app(
                make_environ(
                    '/api/control/unknown', method='POST',
                    headers=bearer_header,
                ),
                rec,
            ),
        )
        assert rec.status_code == 404
