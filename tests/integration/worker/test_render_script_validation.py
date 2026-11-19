# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for render script generation and validation.

Verifies that generated scripts are syntactically valid Python,
and contain expected Blender API calls for each render engine. GPU
device selection is covered in test_render_script_gpu_validation.py.
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
    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_eevee_script_compiles(mocker):
    """EEVEE render script is syntactically valid Python."""
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
    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    assert "import bpy" in script
    assert "_sethlans_set_engine(bpy.context.scene.render, 'CYCLES', ('CYCLES',))" in script


def test_cycles_cpu_sets_cpu_device(mocker):
    """Cycles CPU script sets device to CPU."""
    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='CPU',
        render_settings={},
    )

    assert "bpy.context.scene.cycles.device = 'CPU'" in script


def test_eevee_script_does_not_set_cycles_device(mocker):
    """EEVEE script does not contain Cycles-specific device config."""
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


# -- CPU fallback --

def test_gpu_fallback_to_cpu(mocker):
    """An ANY job on the CPU slot gets CPU device config (device_used decides, FR-CB-1)."""
    from sethlans_worker_agent.capacity.profile import SlotReservation
    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='ANY',
        render_settings={},
        reservation=SlotReservation(job_id=1, device_used='CPU'),
    )

    assert "bpy.context.scene.cycles.device = 'CPU'" in script
    assert "compute_device_type" not in script


# -- Invalid engine --

def test_invalid_engine_raises_value_error(mocker):
    """Invalid render engine raises ValueError."""
    with pytest.raises(ValueError, match="Invalid render engine"):
        generate_render_config_script(
            job_id=1,
            render_engine='INVALID_ENGINE',
            render_device='CPU',
            render_settings={},
        )


def test_all_valid_engines_produce_compilable_scripts(mocker):
    """Every engine in the allowlist produces a compilable script."""
    for engine in _VALID_ENGINES:
        script = generate_render_config_script(
            job_id=1,
            render_engine=engine,
            render_device='CPU',
            render_settings={},
        )
        code = _compile_script(script)
        assert code is not None, f"Script for {engine} failed to compile"
