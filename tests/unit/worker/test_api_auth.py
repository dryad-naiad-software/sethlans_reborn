# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker agent api_auth module.

Tests auth header construction, auth failure detection, and the
retain_failed_upload helper. Enrollment header/heartbeat tests were
removed after the legacy ``X-Enrollment-Key`` path was deleted in
worker-enrollment.md.
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


# --- send_authenticated_heartbeat ---

class TestSendAuthenticatedHeartbeat:

    def test_success_returns_json(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.API_TOKEN', 'tok'
        )
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
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
            'https://localhost:8080/api/'
        )
        mock_response = mocker.Mock(status_code=401)
        mock_retry = mocker.Mock(return_value=mock_response)

        result = api_auth.send_authenticated_heartbeat(
            mock_retry, {}
        )
        assert result is None
        assert api_auth.is_auth_failed() is True

    def test_403_logs_manager_detail_and_returns_none(self, mocker):
        """A 403 (e.g. hostname no longer matches the enrolled worker,
        issue #215) surfaces the manager's re-enroll message, returns
        None, and does not set the auth-failed flag.
        """
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mock_response = mocker.Mock(status_code=403)
        mock_response.json.return_value = {
            'detail': 'If this machine was renamed, re-enroll it.',
        }
        mock_retry = mocker.Mock(return_value=mock_response)
        log_error = mocker.patch.object(api_auth.logger, 'error')
        log_warning = mocker.patch.object(api_auth.logger, 'warning')

        result = api_auth.send_authenticated_heartbeat(mock_retry, {})

        assert result is None
        assert api_auth.is_auth_failed() is False
        assert mock_retry.call_count == 1
        # Exactly one line: the manager's reason, not also the generic
        # "Permission denied" warning from handle_auth_response().
        assert log_error.call_count == 1
        log_warning.assert_not_called()
        logged = log_error.call_args.args[0] % log_error.call_args.args[1:]
        assert 're-enroll' in logged

    def test_403_detail_is_logged_with_repr(self, mocker):
        """Control characters in the server's detail cannot forge log lines."""
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mock_response = mocker.Mock(status_code=403)
        mock_response.json.return_value = {'detail': 'bad\nFAKE LINE'}
        mock_retry = mocker.Mock(return_value=mock_response)
        log_error = mocker.patch.object(api_auth.logger, 'error')

        api_auth.send_authenticated_heartbeat(mock_retry, {})

        logged = log_error.call_args.args[0] % log_error.call_args.args[1:]
        assert '\n' not in logged
        assert "'bad\\nFAKE LINE'" in logged

    def test_403_with_non_json_body_still_logs(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mock_response = mocker.Mock(status_code=403)
        mock_response.json.side_effect = ValueError('not json')
        mock_retry = mocker.Mock(return_value=mock_response)
        log_error = mocker.patch.object(api_auth.logger, 'error')

        assert api_auth.send_authenticated_heartbeat(mock_retry, {}) is None
        logged = [c.args[0] % c.args[1:] for c in log_error.call_args_list]
        assert any('no reason given' in m for m in logged)
