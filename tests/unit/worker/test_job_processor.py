# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the job_processor module.

Tests pause/resume, active jobs snapshot, GPU assignment snapshot via
the WorkerCapacity pass-through, finalize logic, and the recent jobs
ring buffer. The capacity-aware integration points (init_capacity,
poll_and_claim_job slot release, drift cadence, terminate_all_active
helper) live in test_job_processor_capacity.py.
"""
from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import (
    CapacityProfile,
    WorkerCapacity,
)
from sethlans_worker_agent.job_lifecycle import _finalize_and_upload


def _build_profile():
    return CapacityProfile(
        gpu_slot_count=1,
        cpu_slot_count=1,
        cpu_thread_ceiling=15,
        cpu_threads_effective=15,
        startup_gpu_count=1,
        gpu_mode='split',
    )


# --- Pause / Resume ---

class TestPauseResume:

    def test_initially_not_paused(self):
        assert job_processor.is_paused() is False

    def test_pause_sets_flag(self):
        job_processor.pause()
        assert job_processor.is_paused() is True

    def test_resume_clears_flag(self):
        job_processor.pause()
        job_processor.resume()
        assert job_processor.is_paused() is False


# --- Active Jobs Snapshot ---

class TestActiveJobsSnapshot:

    def test_empty_initially(self):
        assert job_processor.get_active_jobs_snapshot() == {}

    def test_returns_snapshot(self):
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[1] = {'job_id': 1, 'name': 'Test'}

        snapshot = job_processor.get_active_jobs_snapshot()
        assert snapshot == {1: {'job_id': 1, 'name': 'Test'}}
        # Top-level dict is a copy: adding new keys doesn't affect internal
        snapshot[999] = {'job_id': 999}
        with job_processor._active_jobs_lock:
            assert 999 not in job_processor._active_jobs


# --- GPU Assignment Snapshot pass-through ---

class TestGpuAssignmentSnapshot:

    def test_empty_when_capacity_not_initialized(self):
        job_processor._capacity = None
        assert job_processor.get_gpu_assignment_snapshot() == {}

    def test_delegates_to_capacity(self):
        job_processor._capacity = WorkerCapacity(_build_profile())
        reservation = job_processor._capacity.reserve_for_job(42, 'GPU')
        assert reservation is not None
        snapshot = job_processor.get_gpu_assignment_snapshot()
        assert snapshot == {0: 42}


# --- Recent Jobs ---

class TestRecentJobs:

    def test_empty_initially(self):
        assert job_processor.get_recent_jobs_snapshot() == []

    def test_returns_copy(self):
        with job_processor._recent_jobs_lock:
            job_processor._recent_jobs.append(
                {'job_id': 1, 'status': 'DONE'}
            )
        snapshot = job_processor.get_recent_jobs_snapshot()
        assert len(snapshot) == 1
        snapshot.append({'extra': True})
        with job_processor._recent_jobs_lock:
            assert len(job_processor._recent_jobs) == 1


# --- _finalize_and_upload (lifted from job_lifecycle) ---

class TestFinalizeAndUpload:

    def test_canceled_returns_canceled(self):
        result = _finalize_and_upload(False, True, 1, None)
        assert result == "CANCELED"

    def test_failure_returns_error(self):
        result = _finalize_and_upload(False, False, 1, None)
        assert result == "ERROR"

    def test_success_with_upload(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            return_value=True
        )
        out_dir = tmp_path / 'output'
        out_dir.mkdir()
        out_file = out_dir / 'render.png'
        out_file.write_bytes(b'PNG')

        result = _finalize_and_upload(True, False, 1, str(out_file))
        assert result == "DONE"
        assert not out_file.exists()

    def test_success_no_output_path(self):
        result = _finalize_and_upload(True, False, 1, None)
        assert result == "DONE"

    def test_success_upload_fails(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            return_value=False
        )
        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG')

        result = _finalize_and_upload(True, False, 1, str(out_file))
        # Even if upload fails, status returned is still DONE
        assert result == "DONE"

    def test_success_with_thumbnail_cleanup(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            return_value=True
        )
        out_dir = tmp_path / 'output'
        out_dir.mkdir()
        out_file = out_dir / 'render.exr'
        out_file.write_bytes(b'EXR')
        thumb_file = out_dir / 'thumb_render.png'
        thumb_file.write_bytes(b'PNG')

        result = _finalize_and_upload(
            True, False, 1, str(out_file), thumbnail_path=str(thumb_file)
        )
        assert result == "DONE"
        assert not out_file.exists()
        assert not thumb_file.exists()

    def test_thumbnail_path_passed_to_upload(self, mocker, tmp_path):
        mock_upload = mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            return_value=True
        )
        out_dir = tmp_path / 'output'
        out_dir.mkdir()
        out_file = out_dir / 'render.exr'
        out_file.write_bytes(b'EXR')
        thumb_file = out_dir / 'thumb.png'
        thumb_file.write_bytes(b'PNG')

        _finalize_and_upload(
            True, False, 1, str(out_file),
            thumbnail_path=str(thumb_file),
        )
        mock_upload.assert_called_once_with(
            1, str(out_file),
            thumbnail_path=str(thumb_file),
        )
