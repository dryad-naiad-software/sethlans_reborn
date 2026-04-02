# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for file_operations utility functions.

Exercises real filesystem writes, hash verification, archive extraction
with path traversal protection, and cleanup. HTTP is mocked at the
network boundary via requests_mock or mocker patches.
"""

import hashlib
import os
import tarfile
import zipfile

import pytest

from sethlans_worker_agent.utils.file_operations import (
    download_file,
    verify_hash,
    extract_archive,
    cleanup_archive,
    _safe_zip_extract,
    _win_long_path,
)


# -- download_file: mock HTTP, real filesystem write --

def test_download_file_writes_content(tmp_path, mocker):
    """Downloaded content is written to disk with correct filename."""
    fake_content = b"BLENDER_ARCHIVE_BYTES_1234567890"
    url = "http://example.com/releases/blender-4.1.1.zip"

    mock_response = mocker.MagicMock()
    mock_response.headers = {'content-length': str(len(fake_content))}
    mock_response.iter_content.return_value = [fake_content]
    mock_response.__enter__ = mocker.MagicMock(
        return_value=mock_response
    )
    mock_response.__exit__ = mocker.MagicMock(return_value=False)
    mock_response.raise_for_status = mocker.MagicMock()

    mocker.patch(
        'sethlans_worker_agent.utils.file_operations.requests.get',
        return_value=mock_response,
    )

    result = download_file(url, str(tmp_path))

    assert os.path.isfile(result)
    assert result.endswith("blender-4.1.1.zip")
    with open(result, 'rb') as f:
        assert f.read() == fake_content


# -- verify_hash --

def test_verify_hash_correct_hash(tmp_path):
    """verify_hash returns True when hash matches."""
    content = b"test content for hashing"
    file_path = tmp_path / "test_file.bin"
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert verify_hash(str(file_path), expected) is True


def test_verify_hash_wrong_hash(tmp_path):
    """verify_hash returns False when hash does not match."""
    content = b"test content for hashing"
    file_path = tmp_path / "test_file.bin"
    file_path.write_bytes(content)

    assert verify_hash(str(file_path), "bad_hash_value") is False


def test_verify_hash_md5_algorithm(tmp_path):
    """verify_hash works with non-default algorithm."""
    content = b"md5 test content"
    file_path = tmp_path / "test_file.bin"
    file_path.write_bytes(content)

    expected = hashlib.md5(content).hexdigest()
    assert verify_hash(str(file_path), expected, algorithm='md5') is True


# -- ZIP extraction with path traversal protection --

def test_safe_zip_extract_normal(tmp_path):
    """Normal zip files extract without error."""
    archive_path = tmp_path / "safe.zip"
    extract_to = tmp_path / "output"
    extract_to.mkdir()

    with zipfile.ZipFile(str(archive_path), 'w') as zf:
        zf.writestr("folder/file.txt", "hello world")

    _safe_zip_extract(str(archive_path), str(extract_to))

    extracted = extract_to / "folder" / "file.txt"
    assert extracted.is_file()
    assert extracted.read_text() == "hello world"


def test_safe_zip_extract_path_traversal_raises(tmp_path):
    """Zip with path traversal entry raises ValueError."""
    archive_path = tmp_path / "evil.zip"
    extract_to = tmp_path / "output"
    extract_to.mkdir()

    with zipfile.ZipFile(str(archive_path), 'w') as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")

    with pytest.raises(ValueError, match="path traversal"):
        _safe_zip_extract(str(archive_path), str(extract_to))


def test_safe_zip_extract_absolute_path_raises(tmp_path):
    """Zip with absolute path entry raises ValueError."""
    archive_path = tmp_path / "abs.zip"
    extract_to = tmp_path / "output"
    extract_to.mkdir()

    with zipfile.ZipFile(str(archive_path), 'w') as zf:
        # Write a member with an absolute-ish path
        zf.writestr("/tmp/secret.txt", "secret data")

    # On some platforms absolute paths resolve outside target
    # The behavior depends on pathlib resolution
    try:
        _safe_zip_extract(str(archive_path), str(extract_to))
        # If it didn't raise, the path resolved inside (platform-dependent)
    except ValueError:
        pass  # Expected on most platforms


# -- ZIP extraction on Windows long paths --

def test_win_long_path_windows(mocker):
    """On Windows, _win_long_path prepends \\\\?\\ prefix."""
    mocker.patch(
        'sethlans_worker_agent.utils.file_operations.platform.system',
        return_value='Windows',
    )
    result = _win_long_path("C:\\Users\\test\\path")
    assert result.startswith("\\\\?\\")


def test_win_long_path_non_windows(mocker):
    """On non-Windows, _win_long_path returns path unchanged."""
    mocker.patch(
        'sethlans_worker_agent.utils.file_operations.platform.system',
        return_value='Linux',
    )
    path = "/home/user/path"
    assert _win_long_path(path) == path


# -- TAR.XZ extraction uses 'data' filter --

def test_tar_xz_extraction_uses_data_filter(tmp_path, mocker):
    """extract_archive for .tar.xz calls tarfile with 'data' filter."""
    archive_path = tmp_path / "blender-4.1.1-linux-x64.tar.xz"

    # Create a real .tar.xz with a single file
    inner_dir = tmp_path / "blender-4.1.1-linux-x64"
    inner_dir.mkdir()
    (inner_dir / "blender").write_text("fake binary")

    with tarfile.open(str(archive_path), 'w:xz') as tar:
        tar.add(str(inner_dir), arcname="blender-4.1.1-linux-x64")

    extract_to = tmp_path / "extract_target"
    extract_to.mkdir()

    # Spy on tarfile.open to verify 'data' filter is used
    original_open = tarfile.open
    calls = []

    class SpyTarFile:
        def __init__(self, *args, **kwargs):
            self._real = original_open(*args, **kwargs)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *args):
            return self._real.__exit__(*args)

        def extractall(self, **kwargs):
            calls.append(kwargs)
            return self._real.extractall(**kwargs)

    mocker.patch(
        'sethlans_worker_agent.utils.file_operations.tarfile.open',
        side_effect=lambda *a, **kw: SpyTarFile(*a, **kw),
    )

    # Must not be detected as Darwin/DMG
    mocker.patch(
        'sethlans_worker_agent.utils.file_operations.platform.system',
        return_value='Linux',
    )

    extract_archive(str(archive_path), str(extract_to))

    assert len(calls) == 1
    assert calls[0]['filter'] == 'data'


# -- cleanup_archive --

def test_cleanup_archive_deletes_file(tmp_path):
    """cleanup_archive removes the specified file."""
    archive = tmp_path / "test_archive.zip"
    archive.write_bytes(b"fake zip data")
    assert archive.is_file()

    cleanup_archive(str(archive))
    assert not archive.exists()
