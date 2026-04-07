# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the --threads flag emitted by blender_executor.

Extracted from test_blender_executor.py for file size compliance. Covers
FR-14 (thread cap formula), FR-15 (ceiling semantics), and FR-16 (GPU
jobs must NOT receive --threads). Includes regression coverage for the
ANY-job-on-GPU gate bug fixed during #48 code review.
"""


class TestCpuThreadsFlag:
    """Test CPU thread limiting logic."""

    def _setup_cpu_job(self, mocker, tmp_path):
        """Common boilerplate for the CPU thread flag tests.

        Returns the captured command list populated by the mocked Popen.
        """
        mocker.patch(
            'sethlans_worker_agent.asset_manager.ensure_asset_is_available',
            return_value=str(tmp_path / 'scene.blend')
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager.tool_manager_instance'
            '.ensure_blender_version_available',
            return_value=str(tmp_path / 'blender')
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager.tool_manager_instance'
            '.acquire_version'
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager.tool_manager_instance'
            '.release_version'
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_OUTPUT_DIR',
            str(tmp_path / 'output')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_ROOT',
            str(tmp_path)
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_CPU_ONLY', False
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_ONLY', False
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
            return_value=8
        )
        mocker.patch(
            'sethlans_worker_agent.render_script'
            '.generate_render_config_script',
            return_value='import bpy'
        )

        captured_cmd = []
        mock_process = mocker.Mock()
        mock_process.poll.return_value = 0
        mock_process.wait.return_value = 0
        mock_process.pid = 12345
        mock_process.stdout = mocker.Mock()
        mock_process.stdout.readline = mocker.Mock(return_value='')
        mock_process.stderr = mocker.Mock()
        mock_process.stderr.readline = mocker.Mock(return_value='')
        mock_process.stdout.close = mocker.Mock()
        mock_process.stderr.close = mocker.Mock()

        def capture_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return mock_process

        mocker.patch('subprocess.Popen', side_effect=capture_popen)
        mocker.patch(
            'sethlans_worker_agent.api_handler.get_job_status',
            return_value='RENDERING'
        )
        return captured_cmd

    def _install_capacity(self, cpu_cores, cpu_threads_config):
        """Create and install a WorkerCapacity on the job_processor module."""
        from sethlans_worker_agent import job_processor
        from sethlans_worker_agent.capacity import (
            WorkerCapacity, compute_capacity_profile,
        )
        profile = compute_capacity_profile(
            detected_gpu_count=0,
            cpu_cores=cpu_cores,
            force_cpu_only=False,
            force_gpu_only=False,
            force_gpu_index=None,
            gpu_mode='split',
            cpu_threads_config=cpu_threads_config,
        )
        job_processor._capacity = WorkerCapacity(profile)

    def _cpu_job_data(self):
        return {
            'id': 3,
            'name': 'ThreadTest',
            'render_device': 'CPU',
            'render_engine': 'CYCLES',
            'blender_version': '4.1.1',
            'start_frame': 1,
            'end_frame': 1,
            'output_file_pattern': 'out/t_####',
            'asset': {
                'blend_file': 'http://localhost/scene.blend'
            },
            'render_settings': {},
        }

    def test_cpu_threads_config_below_ceiling_is_honored(
        self, mocker, tmp_path
    ):
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        self._install_capacity(cpu_cores=8, cpu_threads_config=4)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        execute_blender_job(self._cpu_job_data())
        assert '--threads' in captured_cmd
        idx = captured_cmd.index('--threads')
        assert captured_cmd[idx + 1] == '4'

    def test_cpu_threads_default_uses_ceiling(self, mocker, tmp_path):
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        # CPU_THREADS=0 with 8 cores -> ceiling = 7
        self._install_capacity(cpu_cores=8, cpu_threads_config=0)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        execute_blender_job(self._cpu_job_data())
        assert '--threads' in captured_cmd
        idx = captured_cmd.index('--threads')
        assert captured_cmd[idx + 1] == '7'

    def test_cpu_threads_above_ceiling_silently_capped(self, mocker, tmp_path):
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        self._install_capacity(cpu_cores=8, cpu_threads_config=20)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        execute_blender_job(self._cpu_job_data())
        assert '--threads' in captured_cmd
        idx = captured_cmd.index('--threads')
        assert captured_cmd[idx + 1] == '7'  # capped at cores - 1

    def test_gpu_job_does_not_get_threads_flag(self, mocker, tmp_path):
        """FR-16: Explicit GPU jobs must NOT receive --threads."""
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        self._install_capacity(cpu_cores=8, cpu_threads_config=0)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        gpu_job = self._cpu_job_data()
        gpu_job['render_device'] = 'GPU'
        execute_blender_job(gpu_job, assigned_gpu_index=0)
        assert '--threads' not in captured_cmd

    def test_any_job_with_gpu_slot_does_not_get_threads_flag(
        self, mocker, tmp_path
    ):
        """FR-16: ANY jobs dispatched to a GPU must NOT receive --threads.

        Regression for a gate bug where the --threads flag was keyed off
        render_device instead of the actual assigned slot. An ANY job that
        wins a GPU slot still has render_device='ANY', so a naive
        `if render_device != 'GPU'` gate would emit --threads on a GPU
        render in violation of FR-16 / AC-9.
        """
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        self._install_capacity(cpu_cores=8, cpu_threads_config=0)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        any_job = self._cpu_job_data()
        any_job['render_device'] = 'ANY'
        execute_blender_job(any_job, assigned_gpu_index=0)
        assert '--threads' not in captured_cmd

    def test_any_job_with_cpu_fallback_gets_threads_flag(
        self, mocker, tmp_path
    ):
        """ANY jobs falling back to CPU (no GPU slot assigned) DO get --threads."""
        captured_cmd = self._setup_cpu_job(mocker, tmp_path)
        self._install_capacity(cpu_cores=8, cpu_threads_config=0)
        from sethlans_worker_agent.blender_executor import execute_blender_job
        any_job = self._cpu_job_data()
        any_job['render_device'] = 'ANY'
        execute_blender_job(any_job, assigned_gpu_index=None)
        assert '--threads' in captured_cmd
        idx = captured_cmd.index('--threads')
        assert captured_cmd[idx + 1] == '7'
