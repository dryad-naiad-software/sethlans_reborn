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


@pytest.mark.parametrize("gpu_devices, expected_param", [
    (['CUDA'], 'true'),  # Case with GPU
    ([], 'false'),       # Case without GPU
])
def test_get_and_claim_job_sends_gpu_capability(mocker, gpu_devices, expected_param):
    """
    Tests that the worker correctly reports its GPU capability when polling for jobs.
    """
    mock_worker_id = 123
    mock_get = mocker.patch('requests.get', return_value=MagicMock(json=lambda: []))
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=gpu_devices)

    job_processor.get_and_claim_job(mock_worker_id)

    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert 'params' in call_args.kwargs
    assert call_args.kwargs['params']['gpu_available'] == expected_param


def test_get_and_claim_job_sends_render_time(mocker):
    """
    Tests that the render time is correctly parsed and included in the
    final PATCH request when a job is successfully completed.
    """
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Time Test Job'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_available_job], raise_for_status=lambda: None
    ))
    mock_patch = mocker.patch('requests.patch')
    mock_patch.return_value = MagicMock(status_code=200)
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, VALID_STDOUT_UNDER_AN_HOUR, "", "", "/path/to/render.png")
    )
    mocker.patch('sethlans_worker_agent.job_processor._upload_render_output', return_value=True)
    mocker.patch('os.remove')
    mocker.patch('os.rmdir')
    mocker.patch('os.listdir', return_value=[])
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])


    job_processor.get_and_claim_job(mock_worker_id)

    assert mock_patch.call_count == 3
    final_call_args = mock_patch.call_args
    assert 'render_time_seconds' in final_call_args.kwargs['json']
    assert final_call_args.kwargs['json']['render_time_seconds'] == 96


def test_get_and_claim_job_uploads_output_on_success(mocker):
    """
    Tests that the render output is uploaded when a job completes successfully.
    """
    mock_worker_id = 123
    mock_job = {'id': 1, 'name': 'Upload Test Job'}
    mock_output_path = "/tmp/mock_render.png"

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_job], raise_for_status=lambda: None
    ))
    mocker.patch('requests.patch', return_value=MagicMock(status_code=200))
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, "Success stdout", "", "", mock_output_path)
    )
    mock_upload = mocker.patch('sethlans_worker_agent.job_processor._upload_render_output', return_value=True)
    mocker.patch('os.remove')
    mocker.patch('os.rmdir')
    mocker.patch('os.listdir', return_value=[])
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])

    job_processor.get_and_claim_job(mock_worker_id)

    mock_upload.assert_called_once_with(mock_job['id'], mock_output_path)


def test_get_and_claim_job_does_not_upload_on_failure(mocker):
    """
    Tests that the render output is NOT uploaded when a job fails.
    """
    mock_worker_id = 123
    mock_job = {'id': 2, 'name': 'Failed Upload Test'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_job], raise_for_status=lambda: None
    ))
    mocker.patch('requests.patch', return_value=MagicMock(status_code=200))
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(False, False, "", "Error stderr", "Blender failed", None)
    )
    mock_upload = mocker.patch('sethlans_worker_agent.job_processor._upload_render_output')
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])

    job_processor.get_and_claim_job(mock_worker_id)

    mock_upload.assert_not_called()


@pytest.mark.parametrize("render_device", ["CPU", "GPU", "ANY"])
def test_command_always_includes_factory_startup(mock_job_exec_deps, render_device):
    """
    Verifies that --factory-startup is always used, regardless of render device.
    """
    mock_popen = mock_job_exec_deps["popen"]
    mock_job_data = {
        'id': 1,
        'asset': {'blend_file': 'http://a.blend'},
        'output_file_pattern': 'f',
        'render_device': render_device,
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
        'id': 1,
        'asset': {},
        'output_file_pattern': 'f',
        'render_device': 'GPU',
        'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "prefs.compute_device_type = 'OPTIX'" in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" in written_script


def test_cpu_job_generates_correct_script(mocker, mock_job_exec_deps):
    """Verifies that a CPU job generates a script that sets the device to CPU."""
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA']) # Even if GPU exists
    mock_write = mock_job_exec_deps["script_write"]
    job_data = {
        'id': 1,
        'asset': {},
        'output_file_pattern': 'f',
        'render_device': 'CPU',
        'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script # Should not try to set a GPU backend


def test_any_job_on_gpu_worker_generates_gpu_script(mocker, mock_job_exec_deps):
    """Verifies an 'ANY' device job on a GPU worker correctly configures for GPU."""
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA'])
    mock_write = mock_job_exec_deps["script_write"]
    job_data = {
        'id': 1,
        'asset': {},
        'output_file_pattern': 'f',
        'render_device': 'ANY',
        'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "prefs.compute_device_type = 'CUDA'" in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" in written_script


def test_any_job_on_cpu_worker_generates_cpu_script(mocker, mock_job_exec_deps):
    """Verifies an 'ANY' device job on a CPU-only worker correctly configures for CPU."""
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
    mock_write = mock_job_exec_deps["script_write"]
    job_data = {
        'id': 1,
        'asset': {},
        'output_file_pattern': 'f',
        'render_device': 'ANY',
        'blender_version': '4.5.0'
    }
    job_processor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script


def test_execute_blender_job_with_render_settings(mock_job_exec_deps):
    """
    Tests that render_settings are correctly included in the generated script.
    """
    mock_write = mock_job_exec_deps["script_write"]
    mock_job_data = {
        'id': 6, 'name': 'Settings Override', 'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'some_render_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_device': 'CPU',
        'render_settings': {RenderSettings.SAMPLES: 128}
    }

    job_processor.execute_blender_job(mock_job_data)

    written_script = mock_write.call_args.args[0]
    assert "scene.cycles.samples = 128" in written_script


def test_execute_blender_job_failure(mock_job_exec_deps):
    """Tests a failed render job execution (non-zero exit code)."""
    mock_process = mock_job_exec_deps["process"]
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['Error: Something went wrong.\n', '']
    mock_process.wait.return_value = 1

    mock_job_data = {
        'id': 2, 'name': 'Failed Render', 'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0'
    }

    success, _, _, _, error_message, _ = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert "Blender exited with code 1" in error_message


def test_execute_blender_job_animation_command(mock_job_exec_deps):
    """Tests that the command for an animation is constructed correctly."""
    mock_popen = mock_job_exec_deps["popen"]
    output_pattern = os.path.join('anim', 'frame_####')
    mock_job_data = {
        'id': 3, 'name': 'Test Animation', 'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': output_pattern, 'start_frame': 10, 'end_frame': 20,
        'blender_version': '4.5.0', 'render_device': 'CPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    called_command = mock_popen.call_args.args[0]
    assert '-s' in called_command and '10' in called_command
    assert '-e' in called_command and '20' in called_command
    assert '-a' in called_command


def test_execute_blender_job_tool_unavailable(mocker):
    """Tests that the job fails if the requested Blender version is not found."""
    mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available',
                 return_value="/mock/local/scene.blend")
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value=None)

    mock_job_data = {
        'id': 4, 'name': 'Tool Fail Render', 'asset': {'blend_file': 'http://server/media/scene.blend'},
        'blender_version': '9.9.9', 'output_file_pattern': 'frame_####'
    }

    success, _, _, _, error_message, _ = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert "Could not find or acquire Blender version '9.9.9'" in error_message


def test_get_and_claim_job_success(mocker):
    """Tests the full success workflow: find, claim, execute, and report job."""
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Claim Me'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_available_job], raise_for_status=lambda: None
    ))
    mocker.patch('requests.patch', side_effect=[MagicMock(status_code=200)] * 3)
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, "output", "", "", "/path/to/render.png")
    )
    mocker.patch('sethlans_worker_agent.job_processor._upload_render_output', return_value=True)
    mocker.patch('os.remove')
    mocker.patch('os.rmdir')
    mocker.patch('os.listdir', return_value=[])
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])

    job_processor.get_and_claim_job(mock_worker_id)


def test_execute_blender_job_cancellation(mock_job_exec_deps, mocker):
    """
    Tests that execute_blender_job correctly handles a cancellation signal from the API.
    """
    mock_process = mock_job_exec_deps["process"]
    mock_process.poll.side_effect = [None, None, 0]
    mocker.patch('psutil.Process')

    mock_job_data = {
        'id': 99, 'name': 'Cancellable Job', 'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'frame_####', 'start_frame': 1, 'end_frame': 1, 'blender_version': '4.5.0'
    }

    mock_response_canceled = MagicMock(status_code=200, json=lambda: {'status': 'CANCELED'})
    mocker.patch('requests.get', side_effect=[MagicMock(status_code=200, json=lambda: {'status': 'RENDERING'}), mock_response_canceled])

    success, was_canceled, _, _, error_message, _ = job_processor.execute_blender_job(mock_job_data)

    assert was_canceled is True
    assert error_message == "Job was canceled by user request."


def test_get_and_claim_job_cleans_up_file_on_success(mocker):
    """
    Tests that the worker cleans up the local render file and directory
    after a successful upload.
    """
    mock_worker_id = 123
    mock_job = {'id': 1, 'name': 'Cleanup Test Job'}
    mock_output_path = "/mock/worker_output/tiles/tile_0_0_0001.png"

    mocker.patch('requests.get', return_value=MagicMock(json=lambda: [mock_job]))
    mocker.patch('requests.patch', return_value=MagicMock(status_code=200))
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, "Success", "", "", mock_output_path)
    )
    mocker.patch('sethlans_worker_agent.job_processor._upload_render_output', return_value=True)
    mock_remove = mocker.patch('os.remove')
    mocker.patch('os.listdir', return_value=[])
    mock_rmdir = mocker.patch('os.rmdir')
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])

    job_processor.get_and_claim_job(mock_worker_id)

    mock_remove.assert_called_once_with(mock_output_path)
    mock_rmdir.assert_called_once_with(os.path.dirname(mock_output_path))