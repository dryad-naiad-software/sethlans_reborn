# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_downloads.py``.

Covers FFmpeg and Blender start/progress/cancel endpoints under the new
session-backed auth model.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

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


def _setup_phase_request(method, *args, **kwargs):
    request = method(*args, **kwargs)
    session = {"setup_phase": True, "setup_session_id": "sid-1"}
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    request._setup_snapshot = {
        "complete": False, "phase": "ffmpeg", "session_id": None,
    }
    return request


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        "workers.views.setup_downloads.is_frozen", return_value=False,
    )


class TestFFmpegStart:

    def test_starts_download(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads.ffmpeg_already_installed",
            return_value=False,
        )
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=None,
        )
        mocker.patch(
            "workers.views.setup_downloads.create_tagged_task",
            return_value=("ffmpeg_abc123", DownloadProgress()),
        )
        mocker.patch("workers.views.setup_downloads.start_ffmpeg_download")
        req = _setup_phase_request(api_rf.post, "/api/setup/ffmpeg/start/")
        resp = setup_ffmpeg_start_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "started"
        assert resp.data["task_id"] == "ffmpeg_abc123"

    def test_returns_existing_when_in_progress(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads.ffmpeg_already_installed",
            return_value=False,
        )
        prog = DownloadProgress(status="downloading", percent=50)
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=("ffmpeg_existing", prog),
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/ffmpeg/start/")
        resp = setup_ffmpeg_start_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "in_progress"
        assert resp.data["task_id"] == "ffmpeg_existing"

    def test_already_installed(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads.ffmpeg_already_installed",
            return_value=True,
        )
        mocker.patch("workers.views.setup_downloads.append_checkpoint")
        req = _setup_phase_request(api_rf.post, "/api/setup/ffmpeg/start/")
        resp = setup_ffmpeg_start_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "already_installed"


class TestFFmpegProgress:

    def test_returns_progress(self, api_rf, mocker):
        prog = DownloadProgress(
            status="downloading", percent=42, error=None,
        )
        mocker.patch(
            "workers.views.setup_downloads.get_task", return_value=prog,
        )
        req = _setup_phase_request(
            api_rf.get, "/api/setup/ffmpeg/progress/ffmpeg_abc123/",
        )
        resp = setup_ffmpeg_progress_view(req, task_id="ffmpeg_abc123")
        assert resp.status_code == 200
        assert resp.data["percent"] == 42

    def test_unknown_task_returns_404(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads.get_task", return_value=None,
        )
        req = _setup_phase_request(
            api_rf.get, "/api/setup/ffmpeg/progress/nope/",
        )
        resp = setup_ffmpeg_progress_view(req, task_id="nope")
        assert resp.status_code == 404
        assert resp.data["error"]["code"] == "precondition_unmet"

    def test_records_checkpoint_on_complete(self, api_rf, mocker):
        prog = DownloadProgress(status="complete", percent=100)
        mocker.patch(
            "workers.views.setup_downloads.get_task", return_value=prog,
        )
        mock_cp = mocker.patch(
            "workers.views.setup_downloads.append_checkpoint",
        )
        req = _setup_phase_request(
            api_rf.get, "/api/setup/ffmpeg/progress/ffmpeg_abc/",
        )
        setup_ffmpeg_progress_view(req, task_id="ffmpeg_abc")
        mock_cp.assert_called_once()


class TestFFmpegCancel:

    def test_cancels_active_task(self, api_rf, mocker):
        cancel_event = threading.Event()
        prog = DownloadProgress(
            status="downloading", percent=50,
            cancel_event=cancel_event,
        )
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=("ffmpeg_abc", prog),
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/ffmpeg/cancel/")
        resp = setup_ffmpeg_cancel_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"
        assert cancel_event.is_set()

    def test_returns_not_found_when_no_active(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=None,
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/ffmpeg/cancel/")
        resp = setup_ffmpeg_cancel_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "not_found"


class TestBlenderStart:

    def test_starts_download(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads._get_default_blender_version",
            return_value=("4.2", "4.2.19"),
        )
        mocker.patch(
            "workers.views.setup_downloads.blender_already_installed",
            return_value=False,
        )
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=None,
        )
        mocker.patch(
            "workers.views.setup_downloads.create_tagged_task",
            return_value=("blender_xyz", DownloadProgress()),
        )
        mocker.patch("workers.views.setup_downloads.start_blender_download")
        req = _setup_phase_request(api_rf.post, "/api/setup/blender/start/")
        resp = setup_blender_start_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "started"
        assert resp.data["version"] == "4.2.19"

    def test_no_default_version_returns_400(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_downloads._get_default_blender_version",
            return_value=None,
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/blender/start/")
        resp = setup_blender_start_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"


class TestBlenderCancel:

    def test_cancels_active_task(self, api_rf, mocker):
        cancel_event = threading.Event()
        prog = DownloadProgress(
            status="downloading", percent=30,
            cancel_event=cancel_event,
        )
        mocker.patch(
            "workers.views.setup_downloads.find_active_task",
            return_value=("blender_abc", prog),
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/blender/cancel/")
        resp = setup_blender_cancel_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"
        assert cancel_event.is_set()
