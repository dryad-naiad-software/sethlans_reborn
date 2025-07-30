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
def mock_popen_setup(mocker):
    """
    A fixture to provide a standard, complex mock setup for subprocess.Popen,
    psutil, and requests for testing execute_blender_job.
    """
    # Mock config directories
    mocker.patch.object(config, 'WORKER_OUTPUT_DIR', '/mock/worker_output')
    mocker.patch.object(config, 'WORKER_TEMP_DIR', '/mock/worker_temp')

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['']
    mock_process.poll.return_value = 0
    mocker.patch('time.sleep')

    mock_popen = mocker.patch('subprocess.Popen', return_value=mock_process)

    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mocker.patch('requests.get', return_value=mock_response_rendering)

    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value="/mock/tools/blender")
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')

    mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available',
                 return_value="/mock/local/scene.blend")

    return mock_popen, mock_process


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
    # Mock requests.get to return an empty list (no jobs found) to simplify the test
    mock_get = mocker.patch('requests.get', return_value=MagicMock(json=lambda: []))
    # Mock the system_monitor call to control the outcome
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=gpu_devices)

    # Act
    job_processor.get_and_claim_job(mock_worker_id)

    # Assert
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


def test_execute_blender_job_cpu_success(mock_popen_setup):
    """Tests a standard successful CPU render job execution."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 1, 'name': 'CPU Test Render',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': os.path.join('tiles', 'tile_0_0_####'),
        'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_engine': 'CYCLES', 'render_device': 'CPU'
    }

    success, _, _, _, _, output_path = job_processor.execute_blender_job(mock_job_data)

    assert success is True
    expected_path = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, 'tiles', 'tile_0_0_0001.png'))
    assert output_path == expected_path
    called_command = mock_popen.call_args.args[0]
    assert "--factory-startup" in called_command
    assert "/mock/local/scene.blend" in called_command


def test_execute_blender_job_gpu_command(mock_popen_setup):
    """
    Tests that the --factory-startup flag is correctly OMITTED for GPU jobs.
    """
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 5, 'name': 'GPU Test',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': os.path.join('tiles', 'tile_0_0_####'),
        'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_engine': 'CYCLES', 'render_device': 'GPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--factory-startup" not in called_command


def test_execute_blender_job_with_render_settings(mock_popen_setup, mocker):
    """
    Tests that render_settings are correctly written to a temp script
    and passed to Blender via the --python argument.
    """
    mock_popen, _ = mock_popen_setup
    mock_temp_file_context = MagicMock()
    mock_temp_file_context.__enter__.return_value.name = "/mock/worker_temp/fake_script.py"
    mock_temp_file = mocker.patch('tempfile.NamedTemporaryFile', return_value=mock_temp_file_context)
    mocker.patch('os.remove')

    mock_job_data = {
        'id': 6, 'name': 'Settings Override',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'some_render_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_device': 'CPU',
        'render_settings': {
            RenderSettings.SAMPLES: 128,
        }
    }

    job_processor.execute_blender_job(mock_job_data)

    mock_temp_file.assert_called_once_with(mode='w', suffix='.py', delete=False, dir=config.WORKER_TEMP_DIR)
    called_command = mock_popen.call_args.args[0]
    assert "--python" in called_command
    assert "/mock/worker_temp/fake_script.py" in called_command


def test_execute_blender_job_failure(mock_popen_setup):
    """Tests a failed render job execution (non-zero exit code)."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['Error: Something went wrong.\n', '']
    mock_process.wait.return_value = 1

    mock_job_data = {
        'id': 2, 'name': 'Failed Render',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0'
    }

    success, was_canceled, stdout, stderr, error_message, output_path = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert output_path is None
    assert "Blender exited with code 1" in error_message


def test_execute_blender_job_animation_command(mock_popen_setup):
    """Tests that the command for an animation is constructed correctly."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    output_pattern = os.path.join('anim', 'frame_####')
    mock_job_data = {
        'id': 3, 'name': 'Test Animation',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': output_pattern, 'start_frame': 10, 'end_frame': 20,
        'blender_version': '4.5.0', 'render_device': 'CPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    resolved_pattern = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, output_pattern))

    called_command = mock_popen.call_args.args[0]
    assert resolved_pattern in called_command
    assert '-s' in called_command
    assert '10' in called_command
    assert '-e' in called_command
    assert '20' in called_command
    assert '-a' in called_command


def test_execute_blender_job_tool_unavailable(mocker):
    """Tests that the job fails if the requested Blender version is not found."""
    mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available',
                 return_value="/mock/local/scene.blend")
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value=None)
    mocker.patch('subprocess.Popen')

    mock_job_data = {
        'id': 4, 'name': 'Tool Fail Render',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'blender_version': '9.9.9',
        'output_file_pattern': 'frame_####'
    }

    success, was_canceled, stdout, stderr, error_message, output_path = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert output_path is None
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


def test_execute_blender_job_cancellation(mock_popen_setup, mocker):
    """
    Tests that execute_blender_job correctly handles a cancellation signal from the API.
    """
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['output line 1\n', '']
    mock_process.poll.side_effect = [None, None, 0]
    mock_process.wait.return_value = -9
    mocker.patch('psutil.Process')

    mock_job_data = {
        'id': 99, 'name': 'Cancellable Job',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'frame_####', 'start_frame': 1,
        'end_frame': 1, 'blender_version': '4.5.0'
    }

    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mock_response_canceled = MagicMock(status_code=200)
    mock_response_canceled.json.return_value = {'status': 'CANCELED'}
    mocker.patch('requests.get', side_effect=[mock_response_rendering, mock_response_canceled])

    success, was_canceled, stdout, stderr, error_message, output_path = job_processor.execute_blender_job(mock_job_data)

    assert was_canceled is True
    assert output_path is None
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


def test_execute_blender_job_uses_local_output_dir(mock_popen_setup):
    """Tests that the render command uses the local worker_output directory."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 1, 'name': 'CPU Test Render',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': os.path.join('tiles', 'tile_0_0_####'),
        'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_device': 'CPU'
    }

    success, _, _, _, _, output_path = job_processor.execute_blender_job(mock_job_data)

    assert success is True
    expected_path = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, 'tiles', 'tile_0_0_0001.png'))
    assert output_path == expected_path

    called_command = mock_popen.call_args.args[0]
    expected_pattern_in_command = os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, 'tiles', 'tile_0_0_####'))
    assert expected_pattern_in_command in called_command


def test_execute_blender_job_uses_local_temp_dir_for_scripts(mock_popen_setup, mocker):
    """Tests that render_settings scripts are created in the local temp directory."""
    mock_popen, _ = mock_popen_setup
    mock_temp_file_context = MagicMock()
    mock_temp_file_context.__enter__.return_value.name = "/mock/worker_temp/fake_script.py"
    mock_temp_file = mocker.patch('tempfile.NamedTemporaryFile', return_value=mock_temp_file_context)
    mocker.patch('os.remove')

    mock_job_data = {
        'id': 6, 'name': 'Settings Override',
        'asset': {'blend_file': 'http://server/media/scene.blend'},
        'output_file_pattern': 'some_render_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.5.0', 'render_device': 'CPU',
        'render_settings': {RenderSettings.SAMPLES: 128}
    }

    job_processor.execute_blender_job(mock_job_data)

    mock_temp_file.assert_called_once_with(mode='w', suffix='.py', delete=False, dir=config.WORKER_TEMP_DIR)

    called_command = mock_popen.call_args.args[0]
    assert "/mock/worker_temp/fake_script.py" in called_command