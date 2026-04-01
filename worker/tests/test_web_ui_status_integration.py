# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
# worker/tests/test_web_ui_status_integration.py
"""
Integration tests for status snapshot assembly with real state.

Sets up real module-level state in job_processor and blender_executor
(active jobs, GPU assignments, output lines, recent jobs, pause state)
then calls get_status_snapshot() and verifies the assembled JSON
reflects the actual state. Only hardware detection and tool_manager
are mocked since they require real GPU/Blender installations.
"""

import datetime

import pytest

from sethlans_worker_agent import config, job_processor, blender_executor
from sethlans_worker_agent.web_ui.status import get_status_snapshot


def _clear_state():
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._gpu_lock:
        job_processor._gpu_assignment_map.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    with blender_executor._output_lock:
        blender_executor._last_output_lines.clear()
    job_processor._pause_event.clear()


@pytest.fixture(autouse=True)
def reset_module_state():
    _clear_state()
    yield
    _clear_state()


def _make_job_entry(job_id, name='Test', engine='CYCLES',
                    device='GPU', gpu_idx=None, used='GPU'):
    return {
        'job_id': job_id, 'name': name, 'render_engine': engine,
        'render_device': device, 'gpu_index': gpu_idx,
        'device_used': used,
        'start_time': datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }


@pytest.fixture
def mock_hardware(mocker):
    """Mock only hardware detection and tool manager."""
    for attr, val in [('HOSTNAME', 'integ-host'), ('IP_ADDRESS', '10.0.0.5'),
                      ('OS_INFO', 'Linux 6.5')]:
        mocker.patch(f'sethlans_worker_agent.web_ui.status.{attr}', val)
    mocker.patch('sethlans_worker_agent.system_monitor.WORKER_ID', 7)
    mocker.patch('sethlans_worker_agent.web_ui.status.get_gpu_device_details',
                 return_value=[
                     {'name': 'RTX 3090', 'type': 'OPTIX', 'vram': 24576},
                     {'name': 'RTX 3080', 'type': 'OPTIX', 'vram': 10240},
                 ])
    mocker.patch('sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
                 return_value=32)
    mocker.patch('sethlans_worker_agent.web_ui.status.tool_manager_instance'
                 ).scan_for_local_blenders.return_value = [
        {'version': '4.5.1', 'path': '/hidden/path'},
        {'version': '4.5.0', 'path': '/hidden/other'},
    ]


class TestSnapshotWithRealActiveJobs:
    """Snapshot reflects real entries in _active_jobs."""

    def test_single_active_job(self, mock_hardware):
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[100] = _make_job_entry(
                100, 'Render Scene A')

        snap = get_status_snapshot()
        assert len(snap['active_jobs']) == 1
        assert snap['active_jobs'][0]['job_id'] == 100
        assert snap['active_jobs'][0]['name'] == 'Render Scene A'

    def test_multiple_active_jobs(self, mock_hardware):
        with job_processor._active_jobs_lock:
            for i in range(3):
                job_processor._active_jobs[200 + i] = _make_job_entry(
                    200 + i, f'Job {i}', device='CPU', used='CPU')

        snap = get_status_snapshot()
        assert len(snap['active_jobs']) == 3
        assert {j['job_id'] for j in snap['active_jobs']} == {200, 201, 202}

    def test_empty_active_jobs(self, mock_hardware):
        assert get_status_snapshot()['active_jobs'] == []


class TestSnapshotWithRealGpuAssignment:
    """Snapshot reflects real GPU assignment map entries."""

    def test_gpu_assignments_with_string_keys(self, mock_hardware):
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map[0] = 100
            job_processor._gpu_assignment_map[1] = 101
        snap = get_status_snapshot()
        assert snap['gpu_allocation'] == {'0': 100, '1': 101}

    def test_empty_gpu_assignment(self, mock_hardware):
        assert get_status_snapshot()['gpu_allocation'] == {}


class TestSnapshotWithRealBlenderOutput:
    """Snapshot includes real Blender output lines per job."""

    def test_output_line_enriches_active_job(self, mock_hardware):
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[300] = _make_job_entry(300, 'Tile')
        with blender_executor._output_lock:
            blender_executor._last_output_lines[300] = (
                'Fra:42 Mem:1.2G | Rendering 512 / 1024 samples')

        snap = get_status_snapshot()
        assert snap['active_jobs'][0]['last_output_line'] == (
            'Fra:42 Mem:1.2G | Rendering 512 / 1024 samples')

    def test_missing_output_returns_none(self, mock_hardware):
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[301] = _make_job_entry(
                301, 'No Output', device='CPU', used='CPU')
        assert get_status_snapshot()['active_jobs'][0][
            'last_output_line'] is None


class TestSnapshotWithRealRecentJobs:
    """Snapshot includes real recent completed jobs."""

    def test_recent_jobs_included(self, mock_hardware):
        with job_processor._recent_jobs_lock:
            job_processor._recent_jobs.extend([
                {'job_id': 400, 'name': 'Done', 'status': 'DONE',
                 'render_time_seconds': 45.3,
                 'completed_at': '2025-08-01T13:00:00Z'},
                {'job_id': 401, 'name': 'Failed', 'status': 'ERROR',
                 'render_time_seconds': None,
                 'completed_at': '2025-08-01T13:05:00Z'},
            ])
        snap = get_status_snapshot()
        assert len(snap['recent_jobs']) == 2
        assert snap['recent_jobs'][0]['status'] == 'DONE'
        assert snap['recent_jobs'][1]['status'] == 'ERROR'


class TestSnapshotPauseAndConfig:
    """Snapshot reflects pause state and config values."""

    def test_paused_true(self, mock_hardware):
        job_processor.pause()
        assert get_status_snapshot()['worker']['paused'] is True

    def test_paused_false(self, mock_hardware):
        assert get_status_snapshot()['worker']['paused'] is False

    def test_config_values_match_module(self, mock_hardware):
        config.JOB_POLLING_INTERVAL_SECONDS = 15
        config.HEARTBEAT_INTERVAL_SECONDS = 60
        config.FORCE_CPU_ONLY = True
        config.FORCE_GPU_ONLY = False
        config.GPU_SPLIT_MODE = True

        cfg = get_status_snapshot()['config']
        assert cfg['polling_interval'] == 15
        assert cfg['heartbeat_interval'] == 60
        assert cfg['force_cpu'] is True
        assert cfg['force_gpu'] is False
        assert cfg['gpu_split_mode'] is True

    def test_tools_contains_only_versions(self, mock_hardware):
        snap = get_status_snapshot()
        assert snap['tools'] == ['4.5.1', '4.5.0']
        assert '/hidden/' not in str(snap)


class TestSnapshotCombinedState:
    """Integration test with all state components populated."""

    def test_full_state_snapshot(self, mock_hardware):
        with job_processor._active_jobs_lock:
            job_processor._active_jobs[500] = _make_job_entry(
                500, 'GPU Render', gpu_idx=0)
            job_processor._active_jobs[501] = _make_job_entry(
                501, 'CPU Render', engine='EEVEE',
                device='CPU', used='CPU')
        with job_processor._gpu_lock:
            job_processor._gpu_assignment_map[0] = 500
        with blender_executor._output_lock:
            blender_executor._last_output_lines[500] = 'Rendering...'
            blender_executor._last_output_lines[501] = 'Compositing...'
        with job_processor._recent_jobs_lock:
            job_processor._recent_jobs.append(
                {'job_id': 499, 'name': 'Prev', 'status': 'DONE',
                 'render_time_seconds': 10.0,
                 'completed_at': '2025-08-01T13:59:00Z'})
        job_processor.pause()

        snap = get_status_snapshot()

        assert len(snap['active_jobs']) == 2
        assert snap['gpu_allocation'] == {'0': 500}
        assert len(snap['recent_jobs']) == 1
        assert snap['worker']['paused'] is True
        assert snap['hardware']['cpu_threads'] == 32
        assert len(snap['hardware']['gpus']) == 2

        output_map = {j['job_id']: j['last_output_line']
                      for j in snap['active_jobs']}
        assert output_map[500] == 'Rendering...'
        assert output_map[501] == 'Compositing...'
