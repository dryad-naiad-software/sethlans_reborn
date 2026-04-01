# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the worker web UI status snapshot assembly.

Covers snapshot structure, lock handling, VRAM null handling,
sensitive path exclusion, and registration status.
"""

import pytest

from sethlans_worker_agent import config, job_processor, blender_executor
from sethlans_worker_agent.web_ui.status import get_status_snapshot


@pytest.fixture
def mock_status_deps(mocker):
    """Mock all dependencies that get_status_snapshot reads from."""
    mocker.patch.object(config, 'MANAGER_API_URL', 'http://192.168.1.1:7075/api/')
    mocker.patch.object(config, 'JOB_POLLING_INTERVAL_SECONDS', 5)
    mocker.patch.object(config, 'HEARTBEAT_INTERVAL_SECONDS', 30)
    mocker.patch.object(config, 'FORCE_CPU_ONLY', False)
    mocker.patch.object(config, 'FORCE_GPU_ONLY', False)
    mocker.patch.object(config, 'GPU_SPLIT_MODE', True)

    mocker.patch(
        'sethlans_worker_agent.web_ui.status.HOSTNAME', 'test-host'
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.IP_ADDRESS', '192.168.1.100'
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.OS_INFO', 'Linux 6.1'
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.WORKER_ID', 42
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_gpu_device_details',
        return_value=[
            {'name': 'RTX 4090', 'type': 'OPTIX', 'vram': 24576},
        ]
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
        return_value=16
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.tool_manager_instance'
    ).scan_for_local_blenders.return_value = [
        {'version': '4.5.1', 'path': '/should/not/appear'}
    ]
    mocker.patch.object(
        job_processor, 'get_gpu_assignment_snapshot',
        return_value={0: 101}
    )
    mocker.patch.object(
        job_processor, 'get_active_jobs_snapshot',
        return_value={}
    )
    mocker.patch.object(
        job_processor, 'get_recent_jobs_snapshot',
        return_value=[]
    )
    mocker.patch.object(job_processor, 'is_paused', return_value=False)
    mocker.patch.object(
        blender_executor, 'get_last_output_line', return_value=None
    )


class TestGetStatusSnapshot:
    """Tests for status.get_status_snapshot()."""

    def test_snapshot_contains_all_required_sections(self, mock_status_deps):
        """Snapshot has worker, hardware, config, active_jobs, etc."""
        snap = get_status_snapshot()

        assert 'worker' in snap
        assert 'hardware' in snap
        assert 'config' in snap
        assert 'active_jobs' in snap
        assert 'gpu_allocation' in snap
        assert 'recent_jobs' in snap
        assert 'tools' in snap

    def test_worker_identity_fields(self, mock_status_deps):
        """Worker section has all identity fields."""
        snap = get_status_snapshot()
        w = snap['worker']

        assert w['hostname'] == 'test-host'
        assert w['ip_address'] == '192.168.1.100'
        assert w['os'] == 'Linux 6.1'
        assert w['worker_id'] == 42
        assert w['registration_status'] == 'registered'
        assert w['manager_url'] == 'http://192.168.1.1:7075/api/'
        assert w['paused'] is False

    def test_registration_status_pending_when_no_worker_id(
        self, mock_status_deps, mocker
    ):
        """When WORKER_ID is None, registration_status is 'pending'."""
        mocker.patch(
            'sethlans_worker_agent.system_monitor.WORKER_ID', None
        )

        snap = get_status_snapshot()

        assert snap['worker']['registration_status'] == 'pending'
        assert snap['worker']['worker_id'] is None

    def test_hardware_section(self, mock_status_deps):
        """Hardware section includes GPU list and CPU threads."""
        snap = get_status_snapshot()
        hw = snap['hardware']

        assert hw['cpu_threads'] == 16
        assert len(hw['gpus']) == 1
        assert hw['gpus'][0]['name'] == 'RTX 4090'
        assert hw['gpus'][0]['type'] == 'OPTIX'
        assert hw['gpus'][0]['vram'] == 24576

    def test_vram_null_when_unavailable(self, mock_status_deps, mocker):
        """VRAM is null (not omitted) when detection fails."""
        mocker.patch(
            'sethlans_worker_agent.web_ui.status.get_gpu_device_details',
            return_value=[{'name': 'AMD GPU', 'type': 'HIP'}]
        )

        snap = get_status_snapshot()

        gpu = snap['hardware']['gpus'][0]
        assert 'vram' in gpu
        assert gpu['vram'] is None

    def test_config_section_values(self, mock_status_deps):
        """Config section reflects current module-level config values."""
        snap = get_status_snapshot()
        cfg = snap['config']

        assert cfg['polling_interval'] == 5
        assert cfg['heartbeat_interval'] == 30
        assert cfg['force_cpu'] is False
        assert cfg['force_gpu'] is False
        assert cfg['gpu_split_mode'] is True
        assert cfg['blender_versions'] == ['4.5.1']

    def test_no_sensitive_paths_in_response(self, mock_status_deps):
        """managed_tools_dir and other paths must not appear."""
        snap = get_status_snapshot()
        snap_str = str(snap)

        assert 'managed_tools' not in snap_str
        assert '/should/not/appear' not in snap_str

    def test_tools_contains_only_version_strings(self, mock_status_deps):
        """Tools list has version strings only, no paths."""
        snap = get_status_snapshot()

        assert snap['tools'] == ['4.5.1']

    def test_gpu_allocation_keys_are_strings(self, mock_status_deps):
        """GPU allocation map keys are string-ified for JSON."""
        snap = get_status_snapshot()

        assert '0' in snap['gpu_allocation']
        assert snap['gpu_allocation']['0'] == 101

    def test_active_jobs_enriched_with_output_line(
        self, mock_status_deps, mocker
    ):
        """Active jobs include last_output_line from blender_executor."""
        mocker.patch.object(
            job_processor, 'get_active_jobs_snapshot',
            return_value={
                99: {
                    'job_id': 99, 'name': 'Test Render',
                    'render_engine': 'CYCLES',
                    'render_device': 'GPU', 'gpu_index': 0,
                    'device_used': 'GPU',
                    'start_time': '2025-01-01T00:00:00Z',
                }
            }
        )
        mocker.patch.object(
            blender_executor, 'get_last_output_line',
            return_value='Fra:1 Mem:256M | Rendering...'
        )

        snap = get_status_snapshot()

        assert len(snap['active_jobs']) == 1
        job = snap['active_jobs'][0]
        assert job['job_id'] == 99
        assert job['last_output_line'] == 'Fra:1 Mem:256M | Rendering...'

    def test_paused_state_reflected(self, mock_status_deps, mocker):
        """Paused state is reported correctly."""
        mocker.patch.object(job_processor, 'is_paused', return_value=True)

        snap = get_status_snapshot()

        assert snap['worker']['paused'] is True

    def test_empty_gpu_allocation(self, mock_status_deps, mocker):
        """Empty GPU allocation returns empty dict."""
        mocker.patch.object(
            job_processor, 'get_gpu_assignment_snapshot',
            return_value={}
        )

        snap = get_status_snapshot()

        assert snap['gpu_allocation'] == {}

    def test_recent_jobs_included(self, mock_status_deps, mocker):
        """Recent jobs list is included in the snapshot."""
        mocker.patch.object(
            job_processor, 'get_recent_jobs_snapshot',
            return_value=[{
                'job_id': 50, 'name': 'Done Job',
                'status': 'DONE', 'render_time_seconds': 12.5,
                'completed_at': '2025-01-01T01:00:00Z',
            }]
        )

        snap = get_status_snapshot()

        assert len(snap['recent_jobs']) == 1
        assert snap['recent_jobs'][0]['status'] == 'DONE'
