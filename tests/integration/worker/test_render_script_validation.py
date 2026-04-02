# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for render script generation and validation.

Verifies that generated scripts are syntactically valid Python,
contain expected Blender API calls for each render engine, and
correctly configure GPU device selection.
"""

import pytest

from sethlans_worker_agent.render_script import (
    generate_render_config_script,
    _VALID_ENGINES,
)


def _compile_script(script_text):
    """Compile a script string and return the code object or raise."""
    return compile(script_text, '<render_script>', 'exec')


# -- Generated script is valid Python --

def test_cycles_cpu_script_compiles(mocker):
    """Cycles CPU render script is syntactically valid Python."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_cycles_gpu_script_compiles(mocker):
    """Cycles GPU render script is syntactically valid Python."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=['CUDA'],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_eevee_script_compiles(mocker):
    """EEVEE render script is syntactically valid Python."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='BLENDER_EEVEE_NEXT',
        render_device='CPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_workbench_script_compiles(mocker):
    """Workbench render script is syntactically valid Python."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='WORKBENCH',
        render_device='CPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_script_with_render_settings_compiles(mocker):
    """Script with user-defined render settings compiles."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={
            'render.resolution_x': 1920,
            'render.resolution_y': 1080,
            'cycles.samples': 128,
        },
    )

    code = _compile_script(script)
    assert code is not None


# -- Script contains expected Blender API calls --

def test_cycles_script_sets_engine(mocker):
    """Cycles script sets bpy.context.scene.render.engine."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    assert "import bpy" in script
    assert "bpy.context.scene.render.engine = 'CYCLES'" in script


def test_cycles_gpu_configures_backend(mocker):
    """Cycles GPU script sets compute_device_type."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=['OPTIX', 'CUDA'],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
    )

    assert "prefs.compute_device_type = 'OPTIX'" in script
    assert "bpy.context.scene.cycles.device = 'GPU'" in script


def test_cycles_cpu_sets_cpu_device(mocker):
    """Cycles CPU script sets device to CPU."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    assert "bpy.context.scene.cycles.device = 'CPU'" in script


def test_eevee_script_does_not_set_cycles_device(mocker):
    """EEVEE script does not contain Cycles-specific device config."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=['CUDA'],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='BLENDER_EEVEE_NEXT',
        render_device='GPU',
        render_settings={},
    )

    assert "BLENDER_EEVEE_NEXT" in script
    assert "cycles.device" not in script
    assert "compute_device_type" not in script


def test_render_settings_applied(mocker):
    """User render settings appear in the generated script."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={
            'render.resolution_x': 1920,
            'cycles.samples': 64,
        },
    )

    assert "scene.render.resolution_x = 1920" in script
    assert "scene.cycles.samples = 64" in script


def test_invalid_setting_key_skipped(mocker):
    """Render settings with unsafe keys are skipped."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={
            'render.resolution_x': 1920,
            '__import__("os").system("rm -rf /")': 'pwned',
        },
    )

    assert "resolution_x" in script
    assert "__import__" not in script


# -- GPU device selection script --

def test_gpu_index_isolation_script(mocker):
    """GPU split mode generates isolation script for specific index."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=['CUDA'],
    )
    mocker.patch(
        'sethlans_worker_agent.render_script.config.FORCE_GPU_INDEX',
        None,
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
        gpu_index_override=2,
    )

    code = _compile_script(script)
    assert code is not None
    assert "target_gpu_index = 2" in script
    assert "target_device.use = True" in script


def test_gpu_fallback_to_cpu(mocker):
    """CPU fallback flag forces CPU device config."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=['CUDA'],
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='ANY',
        render_settings={},
        is_cpu_fallback=True,
    )

    assert "bpy.context.scene.cycles.device = 'CPU'" in script
    assert "compute_device_type" not in script


# -- Invalid engine --

def test_invalid_engine_raises_value_error(mocker):
    """Invalid render engine raises ValueError."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    with pytest.raises(ValueError, match="Invalid render engine"):
        generate_render_config_script(
            job_id=1,
            render_engine='INVALID_ENGINE',
            render_device='CPU',
            render_settings={},
        )


def test_all_valid_engines_produce_compilable_scripts(mocker):
    """Every engine in the allowlist produces a compilable script."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=[],
    )

    for engine in _VALID_ENGINES:
        script = generate_render_config_script(
            job_id=1,
            render_engine=engine,
            render_device='CPU',
            render_settings={},
        )
        code = _compile_script(script)
        assert code is not None, f"Script for {engine} failed to compile"
