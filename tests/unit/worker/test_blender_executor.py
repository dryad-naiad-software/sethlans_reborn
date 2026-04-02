# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the blender_executor module.

Tests command line construction, output tracking, and the stream reader.
"""
from sethlans_worker_agent.blender_executor import (
    get_last_output_line,
    _last_output_lines,
    _output_lock,
)


class TestGetLastOutputLine:

    def test_returns_none_for_unknown_job(self):
        assert get_last_output_line(999) is None

    def test_returns_stored_line(self):
        with _output_lock:
            _last_output_lines[42] = "Fra:1 rendering..."
        try:
            assert get_last_output_line(42) == "Fra:1 rendering..."
        finally:
            with _output_lock:
                _last_output_lines.pop(42, None)


class TestCommandConstruction:
    """
    Tests for the command line built inside execute_blender_job.

    Since execute_blender_job has heavy side effects (subprocess, file I/O),
    we test the logical components that determine command construction.
    """

    def test_single_frame_uses_f_flag(self, mocker, tmp_path):
        """Verify single-frame jobs use -f flag."""
        # We mock everything to prevent actual subprocess execution.
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
            'sethlans_worker_agent.config.CPU_THREADS', 0
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

        # Mock the render script generator
        mocker.patch(
            'sethlans_worker_agent.render_script'
            '.generate_render_config_script',
            return_value='import bpy'
        )

        # Capture the Popen call
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

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job_data = {
            'id': 1,
            'name': 'Test',
            'render_device': 'CPU',
            'render_engine': 'CYCLES',
            'blender_version': '4.1.1',
            'start_frame': 5,
            'end_frame': 5,
            'output_file_pattern': 'out/test_####',
            'asset': {
                'blend_file': 'http://localhost/scene.blend'
            },
            'render_settings': {},
        }

        execute_blender_job(job_data)

        # Check that -f 5 is in command (single frame)
        assert '-f' in captured_cmd
        assert '5' in captured_cmd
        # And -a (animation) is NOT
        assert '-a' not in captured_cmd

    def test_frame_range_uses_animation_flags(self, mocker, tmp_path):
        """Verify multi-frame jobs use -s/-e/-a flags."""
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
            'sethlans_worker_agent.config.CPU_THREADS', 0
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

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job_data = {
            'id': 2,
            'name': 'AnimTest',
            'render_device': 'CPU',
            'render_engine': 'CYCLES',
            'blender_version': '4.1.1',
            'start_frame': 1,
            'end_frame': 10,
            'output_file_pattern': 'out/anim_####',
            'asset': {
                'blend_file': 'http://localhost/anim.blend'
            },
            'render_settings': {},
        }

        execute_blender_job(job_data)

        assert '-s' in captured_cmd
        assert '-e' in captured_cmd
        assert '-a' in captured_cmd
        assert '1' in captured_cmd
        assert '10' in captured_cmd


class TestCpuThreadsFlag:
    """Test CPU thread limiting logic."""

    def test_cpu_threads_config_adds_threads_flag(self, mocker, tmp_path):
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
        # Set CPU_THREADS to a specific value
        mocker.patch(
            'sethlans_worker_agent.config.CPU_THREADS', 4
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

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job_data = {
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

        execute_blender_job(job_data)

        assert '--threads' in captured_cmd
        assert '4' in captured_cmd
