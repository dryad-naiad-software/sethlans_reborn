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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
import pytest
import requests
import tempfile
import os
import hashlib
from unittest.mock import MagicMock

# Import the functions to be tested
from sethlans_worker_agent.utils import file_operations


def test_download_file(mocker):
    """Tests that download_file correctly fetches content and writes it to a file."""
    # Arrange
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.headers.get.return_value = 12  # content-length
    mock_response.iter_content.return_value = [b"dummy", b" content"]

    # This mock correctly handles the 'with requests.get(...) as r:' syntax
    mock_get_patch = mocker.patch('requests.get')
    mock_get_patch.return_value.__enter__.return_value = mock_response

    m_open = mocker.patch('builtins.open', mocker.mock_open())
    # ** THE FIX IS HERE **
    # Configure the mock file handle that open() will return, without calling open() itself.
    m_open.return_value.write.side_effect = lambda chunk: len(chunk)

    mocker.patch('tqdm.tqdm')  # Mock tqdm to prevent console output

    # Act
    dest_folder = "/tmp/test"
    download_path = file_operations.download_file("http://fake.url/file.zip", dest_folder)

    # Assert
    assert download_path == os.path.join(dest_folder, "file.zip")
    m_open.assert_called_once_with(os.path.join(dest_folder, "file.zip"), 'wb')

    # Assert on the file handle that open returns
    handle = m_open.return_value
    assert handle.write.call_count == 2


def test_verify_hash():
    """Tests that hash verification works for correct and incorrect hashes."""
    content = b"sethlans reborn test content"
    correct_hash = hashlib.sha256(content).hexdigest()
    incorrect_hash = "wrong_hash"

    # Create a temporary file with the known content
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Assert success for correct hash
        assert file_operations.verify_hash(tmp_path, correct_hash) is True
        # Assert failure for incorrect hash
        assert file_operations.verify_hash(tmp_path, incorrect_hash) is False
    finally:
        # Clean up the temporary file
        os.remove(tmp_path)


def test_extract_archive(mocker):
    """Tests that the extract function calls shutil.unpack_archive."""
    mock_unpack = mocker.patch('shutil.unpack_archive')

    file_operations.extract_archive("/tmp/archive.zip", "/tmp/extract_to")

    mock_unpack.assert_called_once_with("/tmp/archive.zip", "/tmp/extract_to")


def test_cleanup_archive(mocker):
    """Tests that the cleanup function calls os.remove."""
    mock_remove = mocker.patch('os.remove')

    file_operations.cleanup_archive("/tmp/archive.zip")

    mock_remove.assert_called_once_with("/tmp/archive.zip")