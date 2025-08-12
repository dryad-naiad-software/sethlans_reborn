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

# --- NEW: Test for DMG vs standard archive handling ---
def test_extract_archive_handles_dmg_on_mac(mocker):
    """Tests that the extract function calls the DMG handler on macOS for .dmg files."""
    # Arrange
    mocker.patch('platform.system', return_value="Darwin")
    mock_dmg_handler = mocker.patch('sethlans_worker_agent.utils.file_operations.handle_dmg_extraction_on_mac')
    mock_shutil_unpack = mocker.patch('shutil.unpack_archive')
    mock_tarfile_open = mocker.patch('tarfile.open')

    # Act
    file_operations.extract_archive("/tmp/archive.dmg", "/tmp/extract_to")

    # Assert
    mock_dmg_handler.assert_called_once_with("/tmp/archive.dmg", "/tmp/extract_to")
    mock_shutil_unpack.assert_not_called()
    mock_tarfile_open.assert_not_called()


@pytest.mark.parametrize("system, archive_path", [
    ("Darwin", "/tmp/archive.zip"),
    ("Windows", "/tmp/archive.zip"),
])
def test_extract_archive_uses_shutil_for_zip(mocker, system, archive_path):
    """Tests that the extract function calls shutil.unpack_archive for non-tar cases."""
    # Arrange
    mocker.patch('platform.system', return_value=system)
    mock_shutil_unpack = mocker.patch('shutil.unpack_archive')
    mock_tarfile_open = mocker.patch('tarfile.open')

    # Act
    file_operations.extract_archive(archive_path, "/tmp/extract_to")

    # Assert
    mock_shutil_unpack.assert_called_once_with(archive_path, "/tmp/extract_to")
    mock_tarfile_open.assert_not_called()


def test_extract_archive_uses_tarfile_for_tar_xz(mocker):
    """
    Tests that .tar.xz archives are handled by the tarfile module with the 'data' filter.
    """
    mocker.patch('platform.system', return_value="Linux")
    mock_tarfile_context = MagicMock()
    mock_tarfile_open = mocker.patch('tarfile.open', return_value=mock_tarfile_context)
    mock_shutil_unpack = mocker.patch('shutil.unpack_archive')

    archive_path = "/tmp/archive.tar.xz"
    extract_to = "/tmp/extract_to"
    file_operations.extract_archive(archive_path, extract_to)

    mock_tarfile_open.assert_called_once_with(archive_path, 'r:xz')
    # Check that extractall was called on the context manager's return value
    mock_tarfile_context.__enter__().extractall.assert_called_once_with(path=extract_to, filter='data')
    mock_shutil_unpack.assert_not_called()


def test_cleanup_archive(mocker):
    """Tests that the cleanup function calls os.remove."""
    mock_remove = mocker.patch('os.remove')

    file_operations.cleanup_archive("/tmp/archive.zip")

    mock_remove.assert_called_once_with("/tmp/archive.zip")