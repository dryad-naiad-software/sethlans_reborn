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
# Created by Gemini on 8/5/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Unit tests for the blender_executor module.
"""

import pytest
from unittest.mock import MagicMock

# Import the module to be tested and its dependencies
from sethlans_worker_agent import blender_executor, config
from sethlans_worker_agent.tool_manager import tool_manager_instance


@pytest.fixture
def mock_exec_deps(mocker):
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

    # Mock the system_monitor dependency
    mock_gpu_details_data = [
        {'name': 'Mock Physical GPU 0', 'type': 'OPTIX', 'id': 'GPU_ID_0'},
        {'name': 'Mock Physical GPU 1', 'type': 'OPTIX', 'id': 'GPU_ID_1'}
    ]
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=mock_gpu_details_data
    )

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


def test_command_always_includes_factory_startup(mock_exec_deps):
    """
    Verifies that --factory-startup is always used.
    """
    mock_popen = mock_exec_deps["popen"]
    mock_job_data = {
        'id': 1, 'asset': {'blend_file': 'http://a.blend'}, 'output_file_pattern': 'f',
        'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(mock_job_data)

    assert "--factory-startup" in mock_popen.call_args.args[0]


def test_gpu_job_generates_correct_script(mocker, mock_exec_deps):
    """
    Verifies that a GPU job generates a script to enable the best available GPU backend.
    """
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['HIP', 'OPTIX'])
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'GPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "prefs.compute_device_type = 'OPTIX'" in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" in written_script


def test_cpu_job_generates_correct_script(mocker, mock_exec_deps):
    """Verifies that a CPU job generates a script that sets the device to CPU."""
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'CPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script


def test_workbench_job_skips_cycles_config(mocker, mock_exec_deps):
    """
    Verifies that a non-Cycles job does not attempt to configure Cycles devices.
    """
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'CPU',
        'render_engine': 'WORKBENCH', 'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'WORKBENCH'" in written_script
    assert "cycles.device" not in written_script


def test_command_omits_render_engine_flag(mock_exec_deps):
    """
    Tests that the -E flag is no longer used, as it's handled by the script.
    """
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "-E" not in called_command


def test_gpu_job_isolates_single_gpu_when_index_is_set(mocker, mock_exec_deps):
    """
    Verifies that setting FORCE_GPU_INDEX generates a script that disables all
    devices first, then enables only the specified GPU.
    """
    # Arrange
    mocker.patch.object(config, 'FORCE_GPU_INDEX', '1') # Target the second GPU (index 1)
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_exec_deps["script_write"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'GPU',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }

    # Act
    blender_executor.execute_blender_job(job_data)

    # Assert
    written_script = mock_write.call_args.args[0]
    assert "target_gpu_index = 1" in written_script
    # This logic ensures other GPUs are disabled
    assert "for device in prefs.devices: device.use = False" in written_script
    assert "target_device.use = True" in written_script


def test_render_script_generation_with_gpu_index_override(mocker, mock_exec_deps):
    """
    Verifies that the `gpu_index_override` parameter correctly generates a script
    to isolate a single GPU, taking precedence over FORCE_GPU_INDEX.
    """
    # Arrange: Set both the global force flag and the override. Override should win.
    mocker.patch.object(config, 'FORCE_GPU_INDEX', '0')
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_exec_deps["script_write"]
    job_data = {'id': 1, 'asset': {}, 'output_file_pattern': 'f', 'render_device': 'GPU',
                'render_engine': 'CYCLES', 'blender_version': '4.5.0'}

    # Act: Pass the override index to the function
    blender_executor.execute_blender_job(job_data, assigned_gpu_index=1)

    # Assert
    written_script = mock_write.call_args.args[0]
    assert "target_gpu_index = 1" in written_script # Asserts the override was used
    assert "for device in prefs.devices: device.use = False" in written_script
    assert "target_device.use = True" in written_script
    assert "target_gpu_index = 0" not in written_script # Asserts the global flag was ignored