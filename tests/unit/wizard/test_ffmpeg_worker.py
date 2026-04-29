# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/ffmpeg_worker.py``
(FR-M2-7 / FR-M2-7a / FR-M2-7b).

Locks the staged-pipeline contract: each stage transitions the task
status, surfaces an error category on failure, cleans up the archive,
and the success path stashes ffmpeg metadata into wizard_state and
records the FFMPEG_INSTALLED checkpoint.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import requests

from wizard.sethlans_wizard import (
    ffmpeg_download as ffdl,
    ffmpeg_worker,
    progress,
    wizard_state,
)


@pytest.fixture(autouse=True)
def _reset():
    wizard_state.reset_state_for_tests()
    yield
    wizard_state.reset_state_for_tests()


@pytest.fixture
def status_recorder():
    calls: list[dict[str, Any]] = []

    def set_status(task_id, **kwargs):
        calls.append({"task_id": task_id, **kwargs})

    set_status.calls = calls  # type: ignore[attr-defined]
    return set_status


def _patch_supported_platform(mocker):
    # Default to linux-x64 so the URL dict has a hit.
    mocker.patch.object(ffdl, "_get_platform_id", return_value="linux-x64")


class TestUnsupportedPlatform:

    def test_unknown_platform_fails_immediately(
        self, tmp_path, mocker, status_recorder,
    ):
        mocker.patch.object(ffdl, "_get_platform_id", return_value=None)
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        # Final status is failed with category download_failed.
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "download_failed"


class TestStageDownloadFailure:

    def test_network_exception_marks_network_error(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(
            ffdl, "_stream_download",
            side_effect=requests.RequestException("dns lookup failed"),
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "network_error"

    def test_cancel_event_marks_download_failed(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=False)
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "download_failed"


class TestStageVerifySha:

    def test_sha_mismatch_marks_sha_mismatch(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(
            ffdl, "_verify_sha256", return_value="sha_mismatch",
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "sha_mismatch"


class TestStageExtract:

    def test_extract_failure_marks_extraction_failed(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(
            ffdl, "_extract_archive",
            side_effect=ValueError("bad archive"),
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "extraction_failed"


class TestStageVersionCheck:

    def test_binary_not_found_marks_extraction_failed(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(ffdl, "_extract_archive", return_value=None)
        mocker.patch.object(ffdl, "get_ffmpeg_binary", return_value=None)

        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "extraction_failed"

    def test_version_mismatch_marks_version_mismatch(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(ffdl, "_extract_archive", return_value=None)
        mocker.patch.object(
            ffdl, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffdl, "run_version_check",
            return_value=(True, "ffmpeg 6.0 not the right version"),
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "version_mismatch"

    def test_version_check_subprocess_failure_marks_version_mismatch(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(ffdl, "_extract_archive", return_value=None)
        mocker.patch.object(
            ffdl, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffdl, "run_version_check", return_value=(False, ""),
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        last = status_recorder.calls[-1]
        assert last["status"] == "failed"
        assert last["category"] == "version_mismatch"


class TestHappyPath:

    def test_full_pipeline_success_stashes_state_and_records_checkpoint(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(ffdl, "_extract_archive", return_value=None)
        binary_path = tmp_path / "bin" / "ffmpeg"
        mocker.patch.object(ffdl, "get_ffmpeg_binary", return_value=binary_path)
        mocker.patch.object(
            ffdl, "run_version_check",
            return_value=(True, f"ffmpeg version {ffdl.FFMPEG_VERSION}"),
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        # State stashed.
        meta = wizard_state.get_ffmpeg()
        assert meta is not None
        assert meta["version"] == ffdl.FFMPEG_VERSION
        assert meta["binary_path"] == str(binary_path)
        # Final status is complete @ 100%.
        last = status_recorder.calls[-1]
        assert last["status"] == "complete"
        assert last["percent"] == 100
        # Checkpoint recorded.
        payload = progress.read_checkpoints(tmp_path)
        assert "ffmpeg_installed" in payload["checkpoints"]


class TestCleanup:

    def test_archive_cleanup_after_failure(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)

        # Make the download "succeed" by writing an archive file then
        # fail SHA verify; the worker MUST remove the archive.
        url = ffdl._FFMPEG_URLS["linux-x64"]
        archive_name = url.rsplit("/", 1)[-1]
        archive_path = ffdl.get_ffmpeg_dir(tmp_path) / archive_name

        def fake_stream(*args, **kwargs):
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(b"x" * 100)
            return True

        mocker.patch.object(ffdl, "_stream_download", side_effect=fake_stream)
        mocker.patch.object(
            ffdl, "_verify_sha256", return_value="sha_mismatch",
        )
        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        assert not archive_path.exists()

    def test_cleanup_unlink_oserror_swallowed(self, mocker):
        # Coverage expansion: _cleanup must NEVER raise — the whole
        # point is post-failure recovery.
        fake_path = mocker.MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.unlink.side_effect = OSError("denied")
        # Should not raise.
        ffmpeg_worker._cleanup(fake_path)


class TestSetStatusContract:
    """The worker passes a callable through every stage; assert each
    stage drops the expected status keywords."""

    def test_stages_emit_expected_status_sequence(
        self, tmp_path, mocker, status_recorder,
    ):
        _patch_supported_platform(mocker)
        mocker.patch.object(ffdl, "_stream_download", return_value=True)
        mocker.patch.object(ffdl, "_verify_sha256", return_value=None)
        mocker.patch.object(ffdl, "_extract_archive", return_value=None)
        binary_path = tmp_path / "bin" / "ffmpeg"
        mocker.patch.object(ffdl, "get_ffmpeg_binary", return_value=binary_path)
        mocker.patch.object(
            ffdl, "run_version_check",
            return_value=(True, f"ffmpeg version {ffdl.FFMPEG_VERSION}"),
        )

        ffmpeg_worker.download_worker(
            "tid", tmp_path, threading.Event(), status_recorder,
        )
        statuses = [c.get("status") for c in status_recorder.calls if c.get("status")]
        # Expect: downloading, verifying, extracting, complete.
        assert "downloading" in statuses
        assert "verifying" in statuses
        assert "extracting" in statuses
        assert statuses[-1] == "complete"
