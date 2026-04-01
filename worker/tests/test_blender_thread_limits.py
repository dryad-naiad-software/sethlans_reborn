# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for CPU thread limit logic in the blender_executor module.
"""

from sethlans_worker_agent import blender_executor, config


def test_manual_override_precedes_automatic_logic(mocker, mock_exec_deps):
    """
    Tests that a manual CPU_THREADS setting is used and prevents the
    automatic calculation from running.
    """
    mocker.patch.object(config, 'CPU_THREADS', 2)
    mock_popen = mock_exec_deps["popen"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--threads" in called_command
    assert "2" in called_command


def test_automatic_thread_limit_in_mixed_mode(mocker, mock_exec_deps):
    """
    Tests the automatic thread calculation in a standard mixed-mode worker
    (CPU+GPU capable, no force flags).
    """
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
        return_value=16
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[{}, {}]
    )
    mock_2_gpus = [{'type': 'CUDA'}, {'type': 'CUDA'}]
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=mock_2_gpus
    )
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    # Expected: 16 total - 2 for GPUs = 14
    assert "--threads" in called_command
    assert "14" in called_command


def test_automatic_thread_limit_is_not_applied_in_force_cpu_mode(
    mocker, mock_exec_deps
):
    """Tests that automatic logic is skipped if FORCE_CPU_ONLY is true."""
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch.object(config, 'FORCE_CPU_ONLY', True)
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--threads" not in called_command


def test_automatic_thread_limit_is_not_applied_if_no_gpus(
    mocker, mock_exec_deps
):
    """Tests that automatic logic is skipped on a CPU-only worker."""
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[]
    )
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=[]
    )
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--threads" not in called_command


def test_automatic_thread_limit_clamps_at_one(mocker, mock_exec_deps):
    """
    Tests that the calculation result is clamped to a minimum of 1 if there
    are more GPUs than CPU threads.
    """
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
        return_value=4
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[{}, {}, {}, {}, {}]
    )
    mock_5_gpus = [{'type': 'CUDA'}] * 5
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=mock_5_gpus
    )
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    # Expected: max(1, 4 - 5) = 1
    assert "--threads" in called_command
    assert "1" in called_command
