#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/24/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/worker_agent/test_job_processor.py

import pytest
import os
from unittest.mock import MagicMock, ANY

# Import the function to be tested and its dependencies
from sethlans_worker_agent import job_processor, config, system_monitor
from workers.constants import RenderSettings
from sethlans_worker_agent.tool_manager import tool_manager_instance

# --- Test data for time parsing ---
VALID_STDOUT_UNDER_AN_HOUR = "Blender render complete\nSaved: '/tmp/test.png'\nTime: 01:35.25 (Saving: 00:00.10)\n"
VALID_STDOUT_OVER_AN_HOUR = "Blender render complete\nSaved: '/tmp/test.png'\nTime: 01:02:03.99 (Saving: 00:00.00)\n"
VALID_STDOUT_SUB_SECOND = "Render complete.\nTime: 00:00.32 (Saving: 00:00.14)\n"
NO_TIME_STDOUT = "Blender render complete\n"
PROGRESS_BAR_TIME_STDOUT = "Fra:1 Mem:158.90M | Time:00:09.53 | Remaining:00:20.23"


@pytest.fixture
def mock_job_exec_deps(mocker):
    """
    A fixture to provide a standard, complex mock setup for subprocess.Popen,
    tempfile, and other dependencies for testing execute_blender_job.
    This fixture returns a dictionary of key mocks for tests to use.
    """
    # Mock config directories
    mocker.patch.object(config, 'WORKER_OUTPUT_DIR', '/mock/worker_output')
    mocker.patch.object(config, 'WORKER_TEMP_DIR', '/mock/worker_temp')

    # Mock subprocess management
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['']
    mock_process.poll.return_value = 0
    mock_process.wait.return_value = 0
    mock_popen = mocker.patch('subprocess.Popen', return_value=mock_process)
    mocker.patch('time.sleep')

    # Mock dependencies of execute_blender_job
    mocker.patch('requests.get', return_value=MagicMock(status_code=200, json=lambda: {'status': 'RENDERING'}))
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value="/mock/tools/blender")
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')
    mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available',
                 return_value="/mock/local/scene.blend")

    # Mock tempfile to capture script content
    mock_write_method = MagicMock()
    mock_temp_file_context = MagicMock()
    mock_temp_file_context.__enter__.return_value.name = "/mock/worker_temp/fake_script.py"
    mock_temp_file_context.__enter__.return_value.write = mock_write_method
    mocker.patch('tempfile.NamedTemporaryFile', return_value=mock_temp_file_context)
    mocker.patch('os.remove')

    return {
        "popen": mock_popen,
        "process": mock_process,
        "script_write": mock_write_method
    }


@pytest.mark.parametrize("stdout, expected_seconds", [
    (VALID_STDOUT_SUB_SECOND, 1),
    (VALID_STDOUT_UNDER_AN_HOUR, 96),
    (VALID_STDOUT_OVER_AN_HOUR, 3724),
    ("Some other text...\nTime: 01:02:03.99 (Saving: 00:00.00)", 3724),
    ("Another line...\nTime: 12.34 (Saving: 00.01)", None),
    (NO_TIME_STDOUT, None),
    (PROGRESS_BAR_TIME_STDOUT, None),
    ("", None)
])
def test_parse_render_time(stdout, expected_seconds):
    """Tests the _parse_render_time function with various inputs."""
    result = job_processor._parse_render_time(stdout)
    assert result == expected_seconds


def test_command_always_includes_factory_startup(mock_job_exec_deps):
    """
    Verifies that --factory-startup is always used.
    """
    mock_popen = mock_job_exec_deps["popen"]
    mock_job_data = {
        'id': 1, 'asset': {'blend_file': 'http://a.blend'}, 'output_file_pattern': 'f',
        'blender_version': '4.5.0'
    }

    job_processor.execute_blender_job(mock_job_data)

    assert "--factory-startup" in mock_popen.call_args.args[0]


def test_gpu_job_generates_correct_script(mocker, mock_job_exec_deps):
    """
    Verifies that a GPU job generates a script to enable the best available GPU backend.
    """
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['HIP', 'OPTIX'])
    mock_write = mock_job_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'GPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "prefs.compute_device_type = 'OPTIX'" in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" in written_script


def test_cpu_job_generates_correct_script(mocker, mock_job_exec_deps):
    """Verifies that a CPU job generates a script that sets the device to CPU."""
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_job_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'CPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script


def test_workbench_job_skips_cycles_config(mocker, mock_job_exec_deps):
    """
    Verifies that a non-Cycles job does not attempt to configure Cycles devices.
    """
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_job_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'CPU',
        'render_engine': 'WORKBENCH', 'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'WORKBENCH'" in written_script
    assert "cycles.device" not in written_script


def test_command_omits_render_engine_flag(mock_job_exec_deps):
    """
    Tests that the -E flag is no longer used, as it's handled by the script.
    """
    mock_popen = mock_job_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }

    job_processor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "-E" not in called_command


def test_gpu_job_isolates_single_gpu_when_index_is_set(mocker, mock_job_exec_deps):
    """
    Verifies that setting FORCE_GPU_INDEX generates a script that enables
    only the specified GPU and disables all others.
    """
    # Arrange
    mocker.patch.object(config, 'FORCE_GPU_INDEX', '1') # Target the second GPU (index 1)
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_job_exec_deps["script_write"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'GPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }

    # Act
    job_processor.execute_blender_job(job_data)

    # Assert
    written_script = mock_write.call_args.args[0]
    assert "target_gpu_index = 1" in written_script
    # This loop logic ensures other GPUs are disabled
    assert "device.use = (i == target_gpu_index)" in written_script


# --- NEW TEST SUITE FOR POLLING LOGIC ---
class TestJobPolling:
    @pytest.fixture
    def mock_poll_deps(self, mocker):
        """Mocks dependencies for get_and_claim_job."""
        mocker.patch.object(config, 'FORCE_CPU_ONLY', False)
        mocker.patch.object(config, 'FORCE_GPU_ONLY', False)
        mock_requests_get = mocker.patch('requests.get')
        mock_requests_get.return_value.json.return_value = [] # No jobs by default
        mock_detect_gpu = mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices')
        return mock_requests_get, mock_detect_gpu

    def test_poll_in_force_cpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_CPU_ONLY mode should poll for gpu_available=false."""
        mock_requests_get, _ = mock_poll_deps
        mocker.patch.object(config, 'FORCE_CPU_ONLY', True)

        job_processor.get_and_claim_job(1)

        mock_requests_get.assert_called_once()
        call_params = mock_requests_get.call_args.kwargs.get('params', {})
        assert call_params.get('gpu_available') == 'false'

    def test_poll_in_force_gpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_GPU_ONLY mode should poll for gpu_available=true."""
        mock_requests_get, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA'] # Simulate GPU present
        mocker.patch.object(config, 'FORCE_GPU_ONLY', True)

        job_processor.get_and_claim_job(1)

        mock_requests_get.assert_called_once()
        call_params = mock_requests_get.call_args.kwargs.get('params', {})
        assert call_params.get('gpu_available') == 'true'

    def test_poll_in_default_mode(self, mock_poll_deps):
        """A normal worker (no force flags) should not specify gpu_available, making it flexible."""
        mock_requests_get, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']  # Simulate a GPU-capable worker

        job_processor.get_and_claim_job(1)

        mock_requests_get.assert_called_once()
        call_params = mock_requests_get.call_args.kwargs.get('params', {})
        assert 'gpu_available' not in call_params
