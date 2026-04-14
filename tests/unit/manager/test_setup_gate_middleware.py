# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/sethlans_manager/middleware/setup_gate.py``.

Covers request routing during setup-incomplete and setup-complete
states: 503 for API, redirect for browsers, allowed prefixes,
setup token validation, defense-in-depth superuser check, and
the module-level boolean latch.
"""

import json
from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from sethlans_manager.middleware import setup_gate
from sethlans_manager.middleware.setup_gate import SetupGateMiddleware


@pytest.fixture(autouse=True)
def _reset_setup_complete():
    """Reset the module-level boolean before/after each test."""
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    yield
    setup_gate._setup_complete = prev


@pytest.fixture
def ok_response():
    """A simple 200 OK response for the inner get_response."""
    return HttpResponse("OK", status=200)


@pytest.fixture
def get_response(ok_response):
    """Middleware get_response callable returning 200."""
    return lambda request: ok_response


@pytest.fixture
def rf():
    return RequestFactory()


# ---- Setup incomplete: API requests get 503 ------------------------------

class TestSetupIncomplete:

    def test_api_request_gets_503(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.get('/api/projects/')
        response = mw(request)
        assert response.status_code == 503
        body = json.loads(response.content)
        assert body["detail"] == "Setup not complete."

    def test_browser_request_redirects_to_setup(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.get('/dashboard/')
        response = mw(request)
        assert response.status_code == 302
        assert response.url == '/setup/'


# ---- Setup complete: requests pass through -------------------------------

class TestSetupComplete:

    def test_all_requests_pass_through(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=True,
        )
        mw = SetupGateMiddleware(get_response)
        for path in ['/api/projects/', '/dashboard/', '/setup/']:
            request = rf.get(path)
            response = mw(request)
            assert response.status_code == 200


# ---- Allowed paths during setup ------------------------------------------

class TestAllowedPaths:

    @pytest.mark.parametrize("path", [
        '/setup/',
        '/setup/step/2/',
        '/api/setup/status/',
        '/api/setup/topology/',
        '/static/css/main.css',
        '/media/images/logo.png',
    ])
    def test_allowed_paths_pass_through(
        self, rf, get_response, mocker, path,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mocker.patch.object(
            setup_gate, '_read_setup_token', return_value=None,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.get(path)
        response = mw(request)
        assert response.status_code == 200


# ---- Setup token validation ----------------------------------------------

class TestSetupToken:

    def test_post_without_token_gets_403(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mocker.patch.object(
            setup_gate, '_read_setup_token',
            return_value='secret-token-123',
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.post(
            '/api/setup/topology/',
            content_type='application/json',
        )
        response = mw(request)
        assert response.status_code == 403

    def test_post_with_valid_token_passes(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mocker.patch.object(
            setup_gate, '_read_setup_token',
            return_value='secret-token-123',
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.post(
            '/api/setup/topology/',
            content_type='application/json',
            HTTP_X_SETUP_TOKEN='secret-token-123',
        )
        response = mw(request)
        assert response.status_code == 200

    def test_get_passes_without_token(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        mocker.patch.object(
            setup_gate, '_read_setup_token',
            return_value='secret-token-123',
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        request = rf.get('/api/setup/status/')
        response = mw(request)
        assert response.status_code == 200


# ---- Defense-in-depth: superuser exists but no sentinel ------------------

class TestDefenseInDepth:

    def test_superuser_exists_no_sentinel_treated_complete(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_get_data_dir',
            return_value='/fake/path',
        )
        mocker.patch(
            'sethlans_manager.middleware.setup_gate.read_sentinel',
            return_value=None,
        )
        mock_user_model = MagicMock()
        mock_qs = MagicMock()
        mock_qs.exists.return_value = True
        mock_user_model.objects.filter.return_value = mock_qs
        mocker.patch(
            'django.contrib.auth.get_user_model',
            return_value=mock_user_model,
        )
        setup_gate._setup_complete = False
        result = setup_gate._check_sentinel()
        assert result is True


# ---- Module-level boolean: once True, stays True -------------------------

class TestModuleLevelBoolean:

    def test_once_true_stays_true(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=True,
        )
        mw = SetupGateMiddleware(get_response)
        assert setup_gate._setup_complete is True
        # Even if sentinel check would return False now,
        # the cached boolean keeps it True
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=False,
        )
        request = rf.get('/api/projects/')
        response = mw(request)
        assert response.status_code == 200

    def test_init_sets_boolean_from_sentinel(
        self, mocker, get_response,
    ):
        mocker.patch.object(
            setup_gate, '_check_sentinel', return_value=True,
        )
        setup_gate._setup_complete = False
        SetupGateMiddleware(get_response)
        assert setup_gate._setup_complete is True
