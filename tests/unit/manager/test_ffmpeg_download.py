# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/services/ffmpeg_download.py``.

Covers platform URL selection, download with mocked requests,
cancel mid-download, hash-less verification, and extraction.
All HTTP and filesystem I/O is mocked.
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workers.services.download_progress import (
    _download_tasks,
    _tasks_lock,
    create_tagged_task,
    get_task,
)
from workers.services.ffmpeg_download import (
    _FFMPEG_URLS,
    _get_platform_id,
    _stream_download,
    download_ffmpeg,
    ffmpeg_already_installed,
    get_ffmpeg_binary,
    get_ffmpeg_dir,
    start_ffmpeg_download,
)


@pytest.fixture(autouse=True)
def _clean_task_registry():
    with _tasks_lock:
        _download_tasks.clear()
    yield
    with _tasks_lock:
        _download_tasks.clear()


# ---- _get_platform_id() -----------------------------------------------------

class TestGetPlatformId:

    def test_windows(self, mocker):
        mocker.patch('platform.system', return_value='Windows')
        mocker.patch('platform.machine', return_value='AMD64')
        assert _get_platform_id() == "windows-x64"

    def test_linux_x64(self, mocker):
        mocker.patch('platform.system', return_value='Linux')
        mocker.patch('platform.machine', return_value='x86_64')
        assert _get_platform_id() == "linux-x64"

    def test_linux_arm(self, mocker):
        mocker.patch('platform.system', return_value='Linux')
        mocker.patch('platform.machine', return_value='aarch64')
        assert _get_platform_id() == "linux-arm64"

    def test_macos_arm(self, mocker):
        mocker.patch('platform.system', return_value='Darwin')
        mocker.patch('platform.machine', return_value='arm64')
        assert _get_platform_id() == "macos-arm64"

    def test_macos_x64(self, mocker):
        mocker.patch('platform.system', return_value='Darwin')
        mocker.patch('platform.machine', return_value='x86_64')
        assert _get_platform_id() == "macos-x64"

    def test_unknown_platform(self, mocker):
        mocker.patch('platform.system', return_value='Haiku')
        assert _get_platform_id() is None


# ---- ffmpeg_already_installed() / get_ffmpeg_binary() ------------------------

class TestFFmpegInstalled:

    def test_not_installed_empty_dir(self, tmp_path):
        assert ffmpeg_already_installed(tmp_path) is False
        assert get_ffmpeg_binary(tmp_path) is None

    def test_installed_when_binary_exists(self, tmp_path, mocker):
        mocker.patch('platform.system', return_value='Linux')
        ffmpeg_dir = get_ffmpeg_dir(tmp_path)
        subdir = ffmpeg_dir / "ffmpeg-n7.1-linux64"
        subdir.mkdir(parents=True)
        binary = subdir / "ffmpeg"
        binary.write_text("fake")
        assert get_ffmpeg_binary(tmp_path) == binary


# ---- _stream_download() -----------------------------------------------------

class TestStreamDownload:

    def test_successful_download(self, mocker, tmp_path):
        task_id, _ = create_tagged_task("ffmpeg_")
        content = b"x" * 1024
        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_content.return_value = [content]
        mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=mock_resp,
        )
        archive = tmp_path / "test.zip"
        cancel = threading.Event()
        result = _stream_download(
            "http://example.com/ffmpeg.zip",
            archive, task_id, cancel,
        )
        assert result is True
        assert archive.read_bytes() == content

    def test_cancel_mid_download(self, mocker, tmp_path):
        task_id, _ = create_tagged_task("ffmpeg_")
        cancel = threading.Event()
        cancel.set()
        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": "2048"}
        mock_resp.iter_content.return_value = [b"x" * 1024] * 2
        mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=mock_resp,
        )
        archive = tmp_path / "test.zip"
        result = _stream_download(
            "http://example.com/ffmpeg.zip",
            archive, task_id, cancel,
        )
        assert result is False


# ---- download_ffmpeg() -------------------------------------------------------

class TestDownloadFFmpeg:

    def test_unsupported_platform_sets_failed(self, mocker):
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download._get_platform_id',
            return_value=None,
        )
        download_ffmpeg(task_id, Path("/fake"))
        progress = get_task(task_id)
        assert progress.status == "failed"
        assert "Unsupported" in progress.error

    def test_request_exception_sets_failed(self, mocker, tmp_path):
        import requests
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download._get_platform_id',
            return_value="linux-x64",
        )
        mocker.patch(
            'workers.services.ffmpeg_download._stream_download',
            side_effect=requests.ConnectionError("refused"),
        )
        download_ffmpeg(task_id, tmp_path)
        progress = get_task(task_id)
        assert progress.status == "failed"
        assert "Download error" in progress.error

    def test_complete_pipeline(self, mocker, tmp_path):
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download._get_platform_id',
            return_value="linux-x64",
        )
        mocker.patch(
            'workers.services.ffmpeg_download._stream_download',
            return_value=True,
        )
        mocker.patch(
            'workers.services.ffmpeg_download._extract_archive',
        )
        # Simulate archive exists then cleanup
        ffmpeg_dir = get_ffmpeg_dir(tmp_path)
        ffmpeg_dir.mkdir(parents=True)
        url = _FFMPEG_URLS["linux-x64"]
        archive_name = url.rsplit("/", 1)[-1]
        archive = ffmpeg_dir / archive_name
        archive.write_text("fake")
        # Simulate binary found after extraction
        mocker.patch(
            'workers.services.ffmpeg_download.get_ffmpeg_binary',
            return_value=ffmpeg_dir / "ffmpeg",
        )
        download_ffmpeg(task_id, tmp_path)
        progress = get_task(task_id)
        assert progress.status == "complete"
        assert progress.percent == 100


# ---- start_ffmpeg_download() -------------------------------------------------

class TestStartFFmpegDownload:

    def test_launches_daemon_thread(self, mocker, tmp_path):
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download.download_ffmpeg',
        )
        t = start_ffmpeg_download(task_id, tmp_path)
        t.join(timeout=2)
        assert t.daemon is True
        assert t.name.startswith("ffmpeg-download-")


# ---- Platform URL coverage ---------------------------------------------------

class TestFFmpegURLs:

    def test_all_platforms_have_urls(self):
        expected = {
            "windows-x64", "linux-x64", "linux-arm64",
            "macos-arm64", "macos-x64",
        }
        assert set(_FFMPEG_URLS.keys()) == expected

    def test_urls_are_https(self):
        for url in _FFMPEG_URLS.values():
            assert url.startswith("https://")
