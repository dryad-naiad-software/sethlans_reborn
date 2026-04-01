# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for script generation and command construction in blender_executor.
"""

from sethlans_worker_agent import blender_executor, config


def test_command_always_includes_factory_startup(mock_exec_deps):
    """Verifies that --factory-startup is always used."""
    mock_popen = mock_exec_deps["popen"]
    mock_job_data = {
        'id': 1, 'asset': {'blend_file': 'http://a.blend'},
        'output_file_pattern': 'f', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(mock_job_data)

    assert "--factory-startup" in mock_popen.call_args.args[0]


def test_gpu_job_generates_correct_script(mocker, mock_exec_deps):
    """
    Verifies that a GPU job generates a script to enable the best
    available GPU backend.
    """
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['HIP', 'OPTIX']
    )
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'GPU', 'render_engine': 'CYCLES',
        'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "prefs.compute_device_type = 'OPTIX'" in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" in written_script


def test_cpu_job_generates_correct_script(mocker, mock_exec_deps):
    """Verifies that a CPU job sets the device to CPU."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['CUDA']
    )
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'render_engine': 'CYCLES',
        'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'CYCLES'" in written_script
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script


def test_any_job_on_gpu_system_forces_cpu_when_fallback_is_true(
    mocker, mock_exec_deps
):
    """
    Verifies that an 'ANY' job on a GPU system generates a CPU-only script
    when the job processor has determined it's a CPU fallback case.
    """
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['CUDA']
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[{'name': 'GPU'}]
    )
    mock_write = mock_exec_deps["script_write"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'ANY', 'render_engine': 'CYCLES',
        'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data, assigned_gpu_index=None)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.cycles.device = 'CPU'" in written_script
    assert "prefs.compute_device_type" not in written_script
    assert "bpy.context.scene.cycles.device = 'GPU'" not in written_script


def test_workbench_job_skips_cycles_config(mocker, mock_exec_deps):
    """
    Verifies that a non-Cycles job does not attempt to configure
    Cycles devices.
    """
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['CUDA']
    )
    mock_write = mock_exec_deps["script_write"]

    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'CPU', 'render_engine': 'WORKBENCH',
        'blender_version': '4.5.0'
    }
    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "bpy.context.scene.render.engine = 'WORKBENCH'" in written_script
    assert "cycles.device" not in written_script


def test_command_omits_render_engine_flag(mock_exec_deps):
    """Tests that -E flag is not used, as it's handled by the script."""
    mock_popen = mock_exec_deps["popen"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_engine': 'CYCLES', 'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    called_command = mock_popen.call_args.args[0]
    assert "-E" not in called_command


def test_gpu_job_isolates_single_gpu_when_index_is_set(
    mocker, mock_exec_deps
):
    """
    Verifies that setting FORCE_GPU_INDEX generates a script that disables
    all devices first, then enables only the specified GPU.
    """
    mocker.patch.object(config, 'FORCE_GPU_INDEX', '1')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['CUDA']
    )
    mock_write = mock_exec_deps["script_write"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'GPU', 'render_engine': 'CYCLES',
        'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data)

    written_script = mock_write.call_args.args[0]
    assert "target_gpu_index = 1" in written_script
    assert "for device in prefs.devices: device.use = False" in written_script
    assert "target_device.use = True" in written_script


def test_render_script_generation_with_gpu_index_override(
    mocker, mock_exec_deps
):
    """
    Verifies that the `gpu_index_override` parameter correctly generates a
    script to isolate a single GPU, taking precedence over FORCE_GPU_INDEX.
    """
    mocker.patch.object(config, 'FORCE_GPU_INDEX', '0')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=['CUDA']
    )
    mock_write = mock_exec_deps["script_write"]
    job_data = {
        'id': 1, 'asset': {}, 'output_file_pattern': 'f',
        'render_device': 'GPU', 'render_engine': 'CYCLES',
        'blender_version': '4.5.0'
    }

    blender_executor.execute_blender_job(job_data, assigned_gpu_index=1)

    written_script = mock_write.call_args.args[0]
    assert "target_gpu_index = 1" in written_script
    assert "for device in prefs.devices: device.use = False" in written_script
    assert "target_device.use = True" in written_script
    assert "target_gpu_index = 0" not in written_script
