# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
import pytest
import requests
from unittest.mock import MagicMock

# Import the function to be tested
from sethlans_worker_agent.utils.hash_parser import get_all_hashes_from_url

DUMMY_HASH_CONTENT = """
hash_abc123  file-one.zip
hash_def456  file-two.tar.xz
"""

def test_get_all_hashes_from_url_success(mocker):
    """
    Tests that the hash parser correctly fetches and parses a SHA256 file.
    """
    # Arrange: Mock the requests.get call
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.text = DUMMY_HASH_CONTENT
    mocker.patch('requests.get', return_value=mock_response)

    # Act
    hashes = get_all_hashes_from_url("http://fake.url/hashes.sha256")

    # Assert
    assert len(hashes) == 2
    assert hashes["file-one.zip"] == "hash_abc123"
    assert hashes["file-two.tar.xz"] == "hash_def456"
    requests.get.assert_called_once_with("http://fake.url/hashes.sha256", timeout=5)

def test_get_all_hashes_from_url_network_error(mocker):
    """
    Tests that an empty dictionary is returned if a network error occurs.
    """
    # Arrange: Mock requests.get to raise an exception
    mocker.patch('requests.get', side_effect=requests.exceptions.RequestException)

    # Act
    hashes = get_all_hashes_from_url("http://fake.url/hashes.sha256")

    # Assert
    assert hashes == {}