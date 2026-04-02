# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker agent api_auth module.

Tests auth header construction, enrollment headers, auth failure
detection, and the retain_failed_upload helper.
"""
from sethlans_worker_agent import api_auth


# --- get_auth_headers ---

class TestGetAuthHeaders:

    def test_returns_token_header_when_configured(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.API_TOKEN', 'my-secret-token'
        )
        headers = api_auth.get_auth_headers()
        assert headers == {"Authorization": "Token my-secret-token"}

    def test_returns_empty_dict_when_no_token(self, mocker):
        mocker.patch('sethlans_worker_agent.config.API_TOKEN', '')
        assert api_auth.get_auth_headers() == {}

    def test_returns_empty_dict_when_token_is_none(self, mocker):
        mocker.patch('sethlans_worker_agent.config.API_TOKEN', None)
        assert api_auth.get_auth_headers() == {}


# --- get_enrollment_headers ---

class TestGetEnrollmentHeaders:

    def test_returns_enrollment_key_header(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.ENROLLMENT_KEY', 'enroll-abc'
        )
        headers = api_auth.get_enrollment_headers()
        assert headers == {"X-Enrollment-Key": "enroll-abc"}

    def test_returns_empty_when_no_key(self, mocker):
        mocker.patch('sethlans_worker_agent.config.ENROLLMENT_KEY', '')
        assert api_auth.get_enrollment_headers() == {}


# --- handle_auth_response ---

class TestHandleAuthResponse:

    def test_401_sets_auth_failed_flag(self, mocker):
        mock_resp = mocker.Mock(status_code=401)
        result = api_auth.handle_auth_response(mock_resp)
        assert result is True
        assert api_auth.is_auth_failed() is True

    def test_403_returns_true_without_setting_flag(self, mocker):
        mock_resp = mocker.Mock(status_code=403)
        result = api_auth.handle_auth_response(mock_resp)
        assert result is True
        assert api_auth.is_auth_failed() is False

    def test_200_returns_false(self, mocker):
        mock_resp = mocker.Mock(status_code=200)
        result = api_auth.handle_auth_response(mock_resp)
        assert result is False

    def test_500_returns_false(self, mocker):
        mock_resp = mocker.Mock(status_code=500)
        result = api_auth.handle_auth_response(mock_resp)
        assert result is False


# --- is_auth_failed ---

class TestIsAuthFailed:

    def test_initially_false(self):
        assert api_auth.is_auth_failed() is False

    def test_true_after_401(self, mocker):
        mock_resp = mocker.Mock(status_code=401)
        api_auth.handle_auth_response(mock_resp)
        assert api_auth.is_auth_failed() is True


# --- retain_failed_upload ---

class TestRetainFailedUpload:

    def test_copies_file_to_failed_dir(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.FAILED_UPLOADS_DIR',
            str(tmp_path / 'failed_uploads')
        )
        src = tmp_path / 'render_output.png'
        src.write_bytes(b'\x89PNG')

        api_auth.retain_failed_upload(42, str(src))

        dest = tmp_path / 'failed_uploads' / 'render_output.png'
        assert dest.exists()
        assert dest.read_bytes() == b'\x89PNG'

    def test_handles_missing_source_gracefully(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.FAILED_UPLOADS_DIR',
            str(tmp_path / 'failed_uploads')
        )
        # Should not raise, just log
        api_auth.retain_failed_upload(99, '/nonexistent/file.png')


# --- send_enrollment_heartbeat ---

class TestSendEnrollmentHeartbeat:

    def test_success_returns_json(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.ENROLLMENT_KEY', 'key123'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mock_response = mocker.Mock(
            status_code=200
        )
        mock_response.json.return_value = {
            'token': 'abc', 'id': 1
        }
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_enrollment_heartbeat(
            mock_retry, {'hostname': 'worker1'}
        )
        assert result == {'token': 'abc', 'id': 1}

    def test_no_enrollment_key_returns_none(self, mocker):
        mocker.patch('sethlans_worker_agent.config.ENROLLMENT_KEY', '')
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        result = api_auth.send_enrollment_heartbeat(
            mocker.Mock(), {}
        )
        assert result is None

    def test_403_returns_none(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.ENROLLMENT_KEY', 'key'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mock_response = mocker.Mock(status_code=403)
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_enrollment_heartbeat(
            mock_retry, {}
        )
        assert result is None

    def test_429_returns_none(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.ENROLLMENT_KEY', 'key'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mock_response = mocker.Mock(status_code=429)
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_enrollment_heartbeat(
            mock_retry, {}
        )
        assert result is None


# --- send_authenticated_heartbeat ---

class TestSendAuthenticatedHeartbeat:

    def test_success_returns_json(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.API_TOKEN', 'tok'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mock_response = mocker.Mock(status_code=200)
        mock_response.json.return_value = {'id': 5}
        mock_response.raise_for_status = mocker.Mock()
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_authenticated_heartbeat(
            mock_retry, {'hostname': 'w1'}
        )
        assert result == {'id': 5}

    def test_401_returns_none_and_sets_flag(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.API_TOKEN', 'tok'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mock_response = mocker.Mock(status_code=401)
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_authenticated_heartbeat(
            mock_retry, {}
        )
        assert result is None
        assert api_auth.is_auth_failed() is True
