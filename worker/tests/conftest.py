# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest
from unittest.mock import MagicMock

from sethlans_worker_agent import job_processor, config, hardware_detection
from sethlans_worker_agent.tool_manager import tool_manager_instance


@pytest.fixture(autouse=True)
def reset_job_processor_state():
    """Fixture to reset module-level state and ensure locks are released."""
    job_processor._gpu_assignment_map.clear()
    if job_processor._cpu_lock.locked():
        job_processor._cpu_lock.release()
    # Reset hardware detection caches to prevent cross-test contamination
    hardware_detection._gpu_devices_cache = None
    hardware_detection._gpu_details_cache = None
    hardware_detection._cpu_thread_count_cache = None
    yield
    job_processor._gpu_assignment_map.clear()
    if job_processor._cpu_lock.locked():
        job_processor._cpu_lock.release()
    hardware_detection._gpu_devices_cache = None
    hardware_detection._gpu_details_cache = None
    hardware_detection._cpu_thread_count_cache = None


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
    # Mock config flags
    mocker.patch.object(config, 'FORCE_CPU_ONLY', False)
    mocker.patch.object(config, 'FORCE_GPU_ONLY', False)

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
    mocker.patch(
        'requests.get',
        return_value=MagicMock(status_code=200, json=lambda: {'status': 'RENDERING'})
    )
    mocker.patch.object(
        tool_manager_instance, 'ensure_blender_version_available',
        return_value="/mock/tools/blender"
    )
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')
    mocker.patch(
        'sethlans_worker_agent.asset_manager.ensure_asset_is_available',
        return_value="/mock/local/scene.blend"
    )

    # Mock the system_monitor and hardware_detection dependencies.
    # Both must be patched because system_monitor re-exports from
    # hardware_detection, and internal calls within hardware_detection
    # resolve to the original module.
    mock_gpu_details_data = [
        {'name': 'Mock Physical GPU 0', 'type': 'OPTIX', 'id': 'GPU_ID_0'},
        {'name': 'Mock Physical GPU 1', 'type': 'OPTIX', 'id': 'GPU_ID_1'}
    ]
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=mock_gpu_details_data
    )
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=mock_gpu_details_data
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
        return_value=16
    )

    # Mock tempfile to capture script content
    mock_write_method = MagicMock()
    mock_temp_file_context = MagicMock()
    mock_temp_file_context.__enter__.return_value.name = (
        "/mock/worker_temp/fake_script.py"
    )
    mock_temp_file_context.__enter__.return_value.write = mock_write_method
    mocker.patch(
        'tempfile.NamedTemporaryFile', return_value=mock_temp_file_context
    )
    mocker.patch('os.remove')

    return {
        "popen": mock_popen,
        "process": mock_process,
        "script_write": mock_write_method
    }
