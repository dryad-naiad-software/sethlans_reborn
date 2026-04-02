# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the job_processor module.

Tests pause/resume, active jobs snapshot, GPU assignment tracking,
finalize logic, and recent jobs ring buffer.
"""
from sethlans_worker_agent import job_processor


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


# --- GPU Assignment Snapshot ---

class TestGpuAssignmentSnapshot:

    def test_empty_initially(self):
        assert job_processor.get_gpu_assignment_snapshot() == {}

    def test_returns_current_assignments(self):
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map[0] = 42
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


# --- _finalize_and_upload ---

class TestFinalizeAndUpload:

    def test_canceled_returns_canceled(self, mocker):
        result = job_processor._finalize_and_upload(
            False, True, 1, None
        )
        assert result == "CANCELED"

    def test_failure_returns_error(self, mocker):
        result = job_processor._finalize_and_upload(
            False, False, 1, None
        )
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

        result = job_processor._finalize_and_upload(
            True, False, 1, str(out_file)
        )
        assert result == "DONE"
        # File should be cleaned up
        assert not out_file.exists()

    def test_success_no_output_path(self, mocker):
        result = job_processor._finalize_and_upload(
            True, False, 1, None
        )
        assert result == "DONE"

    def test_success_upload_fails(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            return_value=False
        )
        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG')

        result = job_processor._finalize_and_upload(
            True, False, 1, str(out_file)
        )
        # Even if upload fails, status returned is still DONE
        assert result == "DONE"


# --- _reserve_next_available_gpu ---

class TestReserveNextAvailableGpu:

    def test_reserves_first_available(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[
                {'name': 'GPU0'}, {'name': 'GPU1'}
            ]
        )
        idx = job_processor._reserve_next_available_gpu(42)
        assert idx == 0
        assert job_processor.get_gpu_assignment_snapshot() == {0: 42}

    def test_skips_busy_gpu(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[
                {'name': 'GPU0'}, {'name': 'GPU1'}
            ]
        )
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map[0] = 99
        idx = job_processor._reserve_next_available_gpu(42)
        assert idx == 1

    def test_returns_none_when_all_busy(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{'name': 'GPU0'}]
        )
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map[0] = 99
        idx = job_processor._reserve_next_available_gpu(42)
        assert idx is None

    def test_returns_none_when_no_gpus(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[]
        )
        idx = job_processor._reserve_next_available_gpu(42)
        assert idx is None
