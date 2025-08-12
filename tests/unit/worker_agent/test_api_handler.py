# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/5/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Unit tests for the api_handler module.
"""
import pytest
import requests
from unittest.mock import MagicMock

from sethlans_worker_agent import api_handler, config


def test_poll_for_available_jobs_success(mocker):
    """
    Tests that poll_for_available_jobs returns job data on success.
    """
    mock_get = mocker.patch('requests.get')
    mock_job_list = [{'id': 1, 'name': 'Test Job'}]
    mock_get.return_value.json.return_value = mock_job_list
    mock_get.return_value.raise_for_status.return_value = None

    params = {'status': 'QUEUED'}
    result = api_handler.poll_for_available_jobs(params)

    assert result == mock_job_list
    mock_get.assert_called_once_with(f"{config.MANAGER_API_URL}jobs/", params=params, timeout=10)


def test_poll_for_available_jobs_failure(mocker):
    """
    Tests that poll_for_available_jobs returns None on a network error.
    """
    mocker.patch('requests.get', side_effect=requests.exceptions.RequestException)
    result = api_handler.poll_for_available_jobs({})
    assert result is None


def test_claim_job_success(mocker):
    """
    Tests that claim_job returns True on a 200 OK response.
    """
    mock_patch = mocker.patch('requests.patch')
    mock_patch.return_value.status_code = 200
    result = api_handler.claim_job(1, 101)
    assert result is True
    mock_patch.assert_called_once_with(f"{config.MANAGER_API_URL}jobs/1/", json={"assigned_worker": 101}, timeout=5)


@pytest.mark.parametrize("status_code", [409, 404, 500])
def test_claim_job_failure(mocker, status_code):
    """
    Tests that claim_job returns False on non-200 responses.
    """
    mock_patch = mocker.patch('requests.patch')
    mock_patch.return_value.status_code = status_code
    result = api_handler.claim_job(1, 101)
    assert result is False


def test_update_job_status(mocker):
    """
    Tests that update_job_status makes the correct PATCH request.
    """
    mock_patch = mocker.patch('requests.patch')
    payload = {'status': 'DONE'}
    api_handler.update_job_status(5, payload)
    mock_patch.assert_called_once_with(f"{config.MANAGER_API_URL}jobs/5/", json=payload, timeout=5)


def test_upload_render_output(mocker):
    """
    Tests that upload_render_output makes the correct multipart POST request.
    """
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data=b'file_content'))
    mock_post = mocker.patch('requests.post')

    result = api_handler.upload_render_output(10, "/path/to/file.png")

    assert result is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == f"{config.MANAGER_API_URL}jobs/10/upload_output/"
    assert 'files' in kwargs