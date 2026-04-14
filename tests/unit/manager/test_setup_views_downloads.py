# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/views/setup_downloads.py``.

Covers FFmpeg and Blender start/progress/cancel endpoints.  All
download services and progress tracking are mocked.
"""

import threading

import pytest
from rest_framework.test import APIRequestFactory

from workers.services.download_progress import DownloadProgress
from workers.views.setup_downloads import (
    setup_blender_cancel_view,
    setup_blender_start_view,
    setup_ffmpeg_cancel_view,
    setup_ffmpeg_progress_view,
    setup_ffmpeg_start_view,
)


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture
def _mock_setup_incomplete(mocker):
    mocker.patch(
        'workers.views.setup_downloads.read_sentinel',
        return_value={
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": [],
        },
    )
    mocker.patch(
        'workers.views.setup_downloads.is_frozen',
        return_value=False,
    )


@pytest.fixture
def _mock_setup_complete(mocker):
    mocker.patch(
        'workers.views.setup_downloads.read_sentinel',
        return_value={
            "version": 1,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "manager",
            "checkpoints": [],
        },
    )
    mocker.patch(
        'workers.views.setup_downloads.is_frozen',
        return_value=False,
    )


# ---- FFmpeg start ------------------------------------------------------------

class TestFFmpegStart:

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_starts_download(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_downloads.ffmpeg_already_installed',
            return_value=False,
        )
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=None,
        )
        mocker.patch(
            'workers.views.setup_downloads.create_tagged_task',
            return_value=("ffmpeg_abc123", DownloadProgress()),
        )
        mocker.patch(
            'workers.views.setup_downloads.start_ffmpeg_download',
        )
        request = api_rf.post('/api/setup/ffmpeg/start/')
        response = setup_ffmpeg_start_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "started"
        assert response.data["task_id"] == "ffmpeg_abc123"

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_returns_existing_when_in_progress(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_downloads.ffmpeg_already_installed',
            return_value=False,
        )
        prog = DownloadProgress(status="downloading", percent=50)
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=("ffmpeg_existing", prog),
        )
        request = api_rf.post('/api/setup/ffmpeg/start/')
        response = setup_ffmpeg_start_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "in_progress"
        assert response.data["task_id"] == "ffmpeg_existing"

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_already_installed(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_downloads.ffmpeg_already_installed',
            return_value=True,
        )
        mocker.patch(
            'workers.views.setup_downloads.append_checkpoint',
        )
        request = api_rf.post('/api/setup/ffmpeg/start/')
        response = setup_ffmpeg_start_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "already_installed"

    @pytest.mark.usefixtures("_mock_setup_complete")
    def test_returns_404_when_complete(self, api_rf):
        request = api_rf.post('/api/setup/ffmpeg/start/')
        response = setup_ffmpeg_start_view(request)
        assert response.status_code == 404


# ---- FFmpeg progress ---------------------------------------------------------

class TestFFmpegProgress:

    def test_returns_progress(self, api_rf, mocker):
        prog = DownloadProgress(
            status="downloading", percent=42, error=None,
        )
        mocker.patch(
            'workers.views.setup_downloads.get_task',
            return_value=prog,
        )
        request = api_rf.get(
            '/api/setup/ffmpeg/progress/ffmpeg_abc123/',
        )
        response = setup_ffmpeg_progress_view(
            request, task_id="ffmpeg_abc123",
        )
        assert response.status_code == 200
        assert response.data["status"] == "downloading"
        assert response.data["percent"] == 42
        assert response.data["error"] is None

    def test_returns_404_for_unknown_task(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_downloads.get_task',
            return_value=None,
        )
        request = api_rf.get(
            '/api/setup/ffmpeg/progress/nonexistent/',
        )
        response = setup_ffmpeg_progress_view(
            request, task_id="nonexistent",
        )
        assert response.status_code == 404

    def test_records_checkpoint_on_complete(self, api_rf, mocker):
        prog = DownloadProgress(status="complete", percent=100)
        mocker.patch(
            'workers.views.setup_downloads.get_task',
            return_value=prog,
        )
        mocker.patch(
            'workers.views.setup_downloads.is_frozen',
            return_value=False,
        )
        mock_cp = mocker.patch(
            'workers.views.setup_downloads.append_checkpoint',
        )
        request = api_rf.get(
            '/api/setup/ffmpeg/progress/ffmpeg_abc/',
        )
        setup_ffmpeg_progress_view(
            request, task_id="ffmpeg_abc",
        )
        mock_cp.assert_called_once()


# ---- FFmpeg cancel -----------------------------------------------------------

class TestFFmpegCancel:

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_cancels_active_task(self, api_rf, mocker):
        cancel_event = threading.Event()
        prog = DownloadProgress(
            status="downloading",
            percent=50,
            cancel_event=cancel_event,
        )
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=("ffmpeg_abc", prog),
        )
        request = api_rf.post('/api/setup/ffmpeg/cancel/')
        response = setup_ffmpeg_cancel_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "cancelled"
        assert cancel_event.is_set()

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_returns_not_found_when_no_active(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=None,
        )
        request = api_rf.post('/api/setup/ffmpeg/cancel/')
        response = setup_ffmpeg_cancel_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "not_found"


# ---- Blender start -----------------------------------------------------------

class TestBlenderStart:

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_starts_download(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_downloads._get_default_blender_version',
            return_value=("4.2", "4.2.19"),
        )
        mocker.patch(
            'workers.views.setup_downloads.blender_already_installed',
            return_value=False,
        )
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=None,
        )
        mocker.patch(
            'workers.views.setup_downloads.create_tagged_task',
            return_value=("blender_xyz", DownloadProgress()),
        )
        mocker.patch(
            'workers.views.setup_downloads.start_blender_download',
        )
        request = api_rf.post('/api/setup/blender/start/')
        response = setup_blender_start_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "started"
        assert response.data["version"] == "4.2.19"

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_no_default_version_returns_400(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_downloads._get_default_blender_version',
            return_value=None,
        )
        request = api_rf.post('/api/setup/blender/start/')
        response = setup_blender_start_view(request)
        assert response.status_code == 400
        assert "No default" in response.data["error"]


# ---- Blender cancel ----------------------------------------------------------

class TestBlenderCancel:

    @pytest.mark.usefixtures("_mock_setup_incomplete")
    def test_cancels_active_task(self, api_rf, mocker):
        cancel_event = threading.Event()
        prog = DownloadProgress(
            status="downloading",
            percent=30,
            cancel_event=cancel_event,
        )
        mocker.patch(
            'workers.views.setup_downloads.find_active_task',
            return_value=("blender_abc", prog),
        )
        request = api_rf.post('/api/setup/blender/cancel/')
        response = setup_blender_cancel_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "cancelled"
        assert cancel_event.is_set()
