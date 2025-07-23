# sethlans_reborn/tests/unit/worker_agent/test_file_operations.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

import pytest
import os
import tempfile
import shutil
import zipfile
import hashlib

import requests

# Import the functions to be tested
from sethlans_worker_agent.utils.file_operations import download_file, extract_zip_file
from sethlans_worker_agent.utils.file_hasher import calculate_file_sha256


# --- Fixtures for common test setup/teardown ---

@pytest.fixture
def temp_dir(tmp_path):
    """Provides a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def mock_requests_get(mocker):
    """
    Mocks requests.get to return a mock response that supports the context manager protocol.
    """
    # This is the mock object that will be returned *by* requests.get
    # and then used as 'r' in 'with ... as r:'
    mock_response_object = mocker.Mock()
    mock_response_object.status_code = 200
    mock_response_object.raise_for_status.return_value = None
    mock_response_object.iter_content.return_value = [b"dummy file content"]  # Default content

    # This mocks requests.get itself.
    # When requests.get() is called, it returns mock_response_object,
    # but that object *also* needs to be a context manager.
    mock_get = mocker.patch('requests.get')

    # Configure mock_get's return_value to be a context manager itself.
    # When mock_get() is called, it returns another mock.
    # When *that* mock's __enter__ is called, it returns mock_response_object.
    mock_get.return_value.__enter__.return_value = mock_response_object
    mock_get.return_value.__exit__.return_value = None

    return mock_get  # Return the patched requests.get mock


# --- Tests for download_file ---

def test_download_file_success(tmp_path, mock_requests_get):
    """Tests successful file download without hash verification."""
    test_url = "http://example.com/test.zip"
    dest_path = tmp_path / "downloaded_file.zip"

    success = download_file(test_url, dest_path)

    assert success is True
    assert os.path.exists(dest_path)
    with open(dest_path, 'rb') as f:
        assert f.read() == b"dummy file content"
    mock_requests_get.assert_called_once_with(test_url, stream=True, timeout=300)  # Now asserts on requests.get
    print(f"\n[UNIT TEST] download_file success test passed. File: {dest_path}")


def test_download_file_http_error(tmp_path, mock_requests_get):
    """Tests download failure due to HTTP error."""
    # Configure the mock response object (which is mock_requests_get.return_value.__enter__.return_value)
    mock_requests_get.return_value.__enter__.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "404 Not Found")

    test_url = "http://example.com/notfound.zip"
    dest_path = tmp_path / "failed_download.zip"

    success = download_file(test_url, dest_path)

    assert success is False
    assert not os.path.exists(dest_path)
    mock_requests_get.assert_called_once_with(test_url, stream=True, timeout=300)  # Assert requests.get was called
    print(f"\n[UNIT TEST] download_file HTTP error test passed. File: {dest_path}")


def test_download_file_hash_success(tmp_path, mock_requests_get):
    """Tests successful file download with correct hash verification."""
    known_content = b"Content for hash test!"
    known_hash = hashlib.sha256(known_content).hexdigest()

    # Configure the mock response object
    mock_requests_get.return_value.__enter__.return_value.iter_content.return_value = [known_content]
    mock_requests_get.return_value.__enter__.return_value.status_code = 200

    test_url = "http://example.com/hashed.zip"
    dest_path = tmp_path / "hashed_file.zip"

    success = download_file(test_url, dest_path, expected_hash=known_hash)

    assert success is True
    assert os.path.exists(dest_path)
    mock_requests_get.assert_called_once_with(test_url, stream=True, timeout=300)  # Assert requests.get was called
    print(f"\n[UNIT TEST] download_file hash success test passed. File: {dest_path}")


def test_download_file_hash_failure(tmp_path, mock_requests_get):
    """Tests download failure due to incorrect hash verification."""
    known_content = b"Content for hash test!"
    wrong_hash = "a" * 64

    # Configure the mock response object
    mock_requests_get.return_value.__enter__.return_value.iter_content.return_value = [known_content]
    mock_requests_get.return_value.__enter__.return_value.status_code = 200

    test_url = "http://example.com/wrong_hash.zip"
    dest_path = tmp_path / "wrong_hashed_file.zip"

    success = download_file(test_url, dest_path, expected_hash=wrong_hash)

    assert success is False
    assert not os.path.exists(dest_path)
    mock_requests_get.assert_called_once_with(test_url, stream=True, timeout=300)  # Assert requests.get was called
    print(f"\n[UNIT TEST] download_file hash failure test passed. File: {dest_path}")


# --- Tests for extract_zip_file ---

@pytest.fixture
def create_dummy_zip(tmp_path):
    """Creates a dummy zip file for extraction tests."""
    zip_path = tmp_path / "dummy.zip"
    extracted_folder_name = "dummy_extracted_folder"
    content_file = "content.txt"

    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr(f"{extracted_folder_name}/{content_file}", b"This is dummy content.")

    yield zip_path, extracted_folder_name, content_file


def test_extract_zip_file_success(tmp_path, create_dummy_zip):
    """Tests successful ZIP file extraction."""
    zip_path, extracted_folder_name, content_file = create_dummy_zip
    extract_to_path = tmp_path / "extracted_target"

    success, actual_extract_path = extract_zip_file(zip_path, extract_to_path)

    assert success is True
    assert os.path.exists(actual_extract_path)
    assert os.path.isdir(actual_extract_path)
    assert os.path.exists(os.path.join(actual_extract_path, content_file))
    with open(os.path.join(actual_extract_path, content_file), 'rb') as f:
        assert f.read() == b"This is dummy content."
    print(f"\n[UNIT TEST] extract_zip_file success test passed. Extracted to: {actual_extract_path}")


def test_extract_zip_file_invalid_zip(tmp_path):
    """Tests extraction failure for an invalid ZIP file."""
    invalid_zip_path = tmp_path / "invalid.zip"
    with open(invalid_zip_path, 'wb') as f:
        f.write(b"NOT A ZIP FILE")

    success, actual_extract_path = extract_zip_file(invalid_zip_path, tmp_path / "extract_invalid")

    assert success is False
    assert actual_extract_path is None
    assert not os.path.exists(tmp_path / "extract_invalid")
    print(f"\n[UNIT TEST] extract_zip_file invalid zip test passed.")